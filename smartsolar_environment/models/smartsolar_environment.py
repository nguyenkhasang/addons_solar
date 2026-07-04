# -*- coding: utf-8 -*-
"""Model lưu dữ liệu môi trường (thời tiết) của hệ thống điện mặt trời.

Nguồn dữ liệu: Open-Meteo (https://open-meteo.com) — API thời tiết miễn phí,
không cần API key. Mỗi hệ thống PV có toạ độ (latitude/longitude) riêng cấu hình
trên smartsolar.system; cron gọi API 5 phút/lần và lưu lại một bản ghi.

Vì sao cần dữ liệu môi trường?
    Bức xạ (shortwave_radiation), mây che (cloud_cover), nhiệt độ, giờ nắng...
    tương quan trực tiếp với sản lượng PV. Lưu lại để đối chiếu hiệu suất thực tế
    với điều kiện thời tiết, phát hiện bất thường (nắng tốt mà sản lượng thấp),
    và cho tầng AI phân tích.

Mỗi bản ghi gộp 2 nhóm dữ liệu Open-Meteo trả về:
  - current: số đo tức thời tại thời điểm gọi (nhiệt độ, độ ẩm, mây, mưa...).
  - daily (của HÔM NAY): tổng hợp trong ngày (bình minh/hoàng hôn, bức xạ, UV...).
"""
from __future__ import annotations

from odoo import models, fields, api


class SmartSolarEnvironment(models.Model):
    _name = 'smartsolar.environment'
    _description = 'Dữ liệu môi trường (thời tiết) hệ thống Solar'
    _order = 'record_date desc'

    # ---- Quan hệ + thời gian ----
    system_id = fields.Many2one('smartsolar.system', string='Hệ thống',
                                required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one('res.company', string='Công ty',
                                 related='system_id.company_id', store=True)
    record_date = fields.Datetime(string='Thời điểm ghi nhận',
                                  default=fields.Datetime.now, index=True)

    # Toạ độ thực tế API đã dùng (snapshot để biết bản ghi lấy ở đâu).
    latitude = fields.Float(string='Vĩ độ', digits=(9, 6))
    longitude = fields.Float(string='Kinh độ', digits=(9, 6))
    elevation = fields.Float(string='Độ cao (m)', digits=(9, 2))

    # ---- Nhóm CURRENT (tức thời) ----
    current_time = fields.Datetime(string='Thời điểm số đo (API)')
    temperature_2m = fields.Float(string='Nhiệt độ 2m (°C)', digits=(6, 2))
    relative_humidity_2m = fields.Float(string='Độ ẩm tương đối (%)', digits=(6, 2))
    wind_speed_10m = fields.Float(string='Tốc độ gió 10m (km/h)', digits=(6, 2))
    cloud_cover = fields.Float(string='Mây che phủ (%)', digits=(6, 2))
    surface_pressure = fields.Float(string='Áp suất bề mặt (hPa)', digits=(8, 2))
    rain = fields.Float(string='Mưa (mm)', digits=(6, 2))
    precipitation = fields.Float(string='Lượng giáng thủy (mm)', digits=(6, 2))

    # ---- Nhóm DAILY (tổng hợp trong ngày hôm nay) ----
    daily_date = fields.Date(string='Ngày (daily)')
    sunrise = fields.Datetime(string='Bình minh')
    sunset = fields.Datetime(string='Hoàng hôn')
    daylight_duration = fields.Float(string='Thời lượng ban ngày (giây)', digits=(12, 2))
    sunshine_duration = fields.Float(string='Thời lượng nắng (giây)', digits=(12, 2))
    uv_index_max = fields.Float(string='Chỉ số UV Max', digits=(6, 2))
    uv_index_clear_sky_max = fields.Float(string='UV Max (trời quang)', digits=(6, 2))
    shortwave_radiation_sum = fields.Float(string='Tổng bức xạ sóng ngắn (MJ/m²)', digits=(10, 3))
    wind_speed_10m_max = fields.Float(string='Gió 10m Max (km/h)', digits=(6, 2))
    wind_gusts_10m_max = fields.Float(string='Gió giật 10m Max (km/h)', digits=(6, 2))
    temperature_2m_max = fields.Float(string='Nhiệt độ Max (°C)', digits=(6, 2))
    precipitation_probability_max = fields.Float(string='Xác suất mưa Max (%)', digits=(6, 2))
    precipitation_hours = fields.Float(string='Số giờ mưa', digits=(6, 2))
    precipitation_sum = fields.Float(string='Tổng giáng thủy (mm)', digits=(8, 2))
    weather_code = fields.Integer(string='Mã thời tiết (WMO)')
    weather_description = fields.Char(string='Mô tả thời tiết', compute='_compute_weather_description', store=True)

    @api.depends('weather_code')
    def _compute_weather_description(self):
        """Diễn giải mã thời tiết WMO sang mô tả tiếng Việt (tra bảng WW code)."""
        mapping = self._wmo_code_map()
        for record in self:
            record.weather_description = mapping.get(record.weather_code, '')

    @api.depends('system_id', 'record_date')
    def _compute_display_name(self):
        for record in self:
            sys_name = record.system_id.name or 'N/A'
            when = record.record_date and record.record_date.strftime('%Y-%m-%d %H:%M') or ''
            record.display_name = f"{sys_name} - {when}"

    @staticmethod
    def _wmo_code_map():
        """Bảng tra mã thời tiết WMO (World Meteorological Organization).

        Open-Meteo trả weather_code theo chuẩn WMO WW. Chỉ map các mã phổ biến;
        mã không có trong bảng để trống mô tả.
        """
        return {
            0: 'Trời quang',
            1: 'Ít mây', 2: 'Có mây', 3: 'Nhiều mây',
            45: 'Sương mù', 48: 'Sương mù đóng băng',
            51: 'Mưa phùn nhẹ', 53: 'Mưa phùn vừa', 55: 'Mưa phùn dày',
            56: 'Mưa phùn băng nhẹ', 57: 'Mưa phùn băng dày',
            61: 'Mưa nhẹ', 63: 'Mưa vừa', 65: 'Mưa to',
            66: 'Mưa băng nhẹ', 67: 'Mưa băng to',
            71: 'Tuyết nhẹ', 73: 'Tuyết vừa', 75: 'Tuyết dày',
            77: 'Hạt tuyết',
            80: 'Mưa rào nhẹ', 81: 'Mưa rào vừa', 82: 'Mưa rào dữ dội',
            85: 'Mưa tuyết nhẹ', 86: 'Mưa tuyết dày',
            95: 'Dông', 96: 'Dông kèm mưa đá nhẹ', 99: 'Dông kèm mưa đá to',
        }
