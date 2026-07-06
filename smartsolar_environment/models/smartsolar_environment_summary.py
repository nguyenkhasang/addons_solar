# -*- coding: utf-8 -*-
"""Tổng hợp dữ liệu môi trường (thời tiết) theo NGÀY.

Giống charge.power.summary / grid.tie.inverter.summary nhưng ở CẤP HỆ THỐNG:
bảng smartsolar.environment chỉ có system_id (không có device_id) vì thời tiết
gắn với vị trí hệ thống, không gắn thiết bị. Vì vậy khóa bucket là
(bucket_start, bucket_type, system_id).

Vì sao CHỈ gộp theo NGÀY (không có lớp theo giờ như bảng thiết bị)?
    Nhóm giá trị chính của môi trường (bức xạ, UV, giờ nắng) do Open-Meteo trả về
    là GIÁ TRỊ CẢ NGÀY — lặp y hệt ở mọi bản ghi 5 phút trong ngày. Gộp theo giờ
    không thêm thông tin gì, chỉ nhân bản dữ liệu. Vì vậy mức tổng hợp tối đa hợp
    lý là theo ngày: gom thẳng từ raw -> bucket 'day'. Đơn giản hơn và vẫn đủ để
    đối chiếu điều kiện thời tiết với sản lượng.

Cách gộp field theo bản chất:
  - Nhóm CURRENT (nhiệt độ, độ ẩm, mây, gió): số đo tức thời, đổi mỗi 5 phút ->
    AVG (trung bình ngày) + MAX (đỉnh trong ngày).
  - Nhóm DAILY (bức xạ, UV, giờ nắng): đã là giá trị cả ngày -> MAX để giữ nguyên.
"""
from datetime import timedelta
import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

# Mỗi lần chạy quét lại vài ngày gần nhất rồi ON CONFLICT ghi đè, phòng bản ghi
# raw về trễ (API chậm/lỗi mạng) khiến bucket ngày trước bị thiếu mẫu.
DAILY_BUFFER_DAYS = 3


class SmartSolarEnvironmentSummary(models.Model):
    _name = 'smartsolar.environment.summary'
    _description = 'Tổng hợp Môi trường (theo ngày)'
    _order = 'bucket_start desc'

    bucket_start = fields.Datetime(string='Bắt đầu bucket', required=True, index=True)
    # Giữ trường bucket_type (chỉ có giá trị 'day') để đồng nhất schema với các bảng
    # summary khác và để MetricRepository lọc theo bucket_type như một mẫu chung.
    bucket_type = fields.Selection([
        ('day', 'Theo ngày'),
    ], string='Loại bucket', required=True, index=True, default='day')

    system_id = fields.Many2one('smartsolar.system', string='Hệ thống',
                                required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one('res.company', string='Công ty',
                                 related='system_id.company_id', store=True)

    sample_count = fields.Integer(string='Số mẫu')

    # ---- Nhóm CURRENT (tức thời) ----
    temp_avg = fields.Float(string='Nhiệt độ TB (°C)', digits=(6, 2))
    temp_max = fields.Float(string='Nhiệt độ Max (°C)', digits=(6, 2))
    humidity_avg = fields.Float(string='Độ ẩm TB (%)', digits=(6, 2))
    cloud_cover_avg = fields.Float(string='Mây che TB (%)', digits=(6, 2))
    cloud_cover_max = fields.Float(string='Mây che Max (%)', digits=(6, 2))
    wind_speed_avg = fields.Float(string='Gió 10m TB (km/h)', digits=(6, 2))
    wind_speed_max = fields.Float(string='Gió 10m Max (km/h)', digits=(6, 2))

    # ---- Nhóm DAILY (giá trị cả ngày, giữ bằng MAX) ----
    irradiance_max = fields.Float(string='Bức xạ ngày (MJ/m²)', digits=(10, 3))
    uv_index_max = fields.Float(string='UV Max', digits=(6, 2))
    sunshine_duration_max = fields.Float(string='Giờ nắng (giây)', digits=(12, 2))

    _sql_constraints = [
        ('bucket_unique', 'unique(bucket_start, bucket_type, system_id)',
         'Mỗi bucket chỉ có một record per system!'),
    ]

    @api.depends('bucket_start', 'system_id')
    def _compute_display_name(self):
        for record in self:
            label = record.bucket_start and record.bucket_start.strftime('%Y-%m-%d') or ''
            sys_name = record.system_id.name or 'N/A'
            record.display_name = f"{sys_name} - {label}"

    @api.model
    def _aggregate_daily(self):
        """Gom thẳng từ bảng raw thành bucket theo ngày (không qua lớp giờ)."""
        now = fields.Datetime.now()
        end_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_day = end_day - timedelta(days=DAILY_BUFFER_DAYS)

        self.env.cr.execute("""
            INSERT INTO smartsolar_environment_summary (
                bucket_start, bucket_type, system_id,
                sample_count,
                temp_avg, temp_max, humidity_avg,
                cloud_cover_avg, cloud_cover_max,
                wind_speed_avg, wind_speed_max,
                irradiance_max, uv_index_max, sunshine_duration_max,
                create_uid, write_uid, create_date, write_date
            )
            SELECT
                date_trunc('day', record_date) AS bucket_start,
                'day'::varchar,
                system_id,
                COUNT(*),
                AVG(temperature_2m), MAX(temperature_2m), AVG(relative_humidity_2m),
                AVG(cloud_cover), MAX(cloud_cover),
                AVG(wind_speed_10m), MAX(wind_speed_10m),
                MAX(shortwave_radiation_sum), MAX(uv_index_max), MAX(sunshine_duration),
                1, 1, NOW() AT TIME ZONE 'UTC', NOW() AT TIME ZONE 'UTC'
            FROM smartsolar_environment
            WHERE record_date >= %s AND record_date < %s
              AND system_id IS NOT NULL
            GROUP BY date_trunc('day', record_date), system_id
            ON CONFLICT (bucket_start, bucket_type, system_id) DO UPDATE SET
                sample_count = EXCLUDED.sample_count,
                temp_avg = EXCLUDED.temp_avg,
                temp_max = EXCLUDED.temp_max,
                humidity_avg = EXCLUDED.humidity_avg,
                cloud_cover_avg = EXCLUDED.cloud_cover_avg,
                cloud_cover_max = EXCLUDED.cloud_cover_max,
                wind_speed_avg = EXCLUDED.wind_speed_avg,
                wind_speed_max = EXCLUDED.wind_speed_max,
                irradiance_max = EXCLUDED.irradiance_max,
                uv_index_max = EXCLUDED.uv_index_max,
                sunshine_duration_max = EXCLUDED.sunshine_duration_max,
                write_date = NOW() AT TIME ZONE 'UTC';
        """, [start_day, end_day])
        _logger.info('[smartsolar.environment] Aggregated daily: %s buckets', self.env.cr.rowcount)
