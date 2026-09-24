# -*- coding: utf-8 -*-
"""Tổng hợp dữ liệu Grid Tie Inverter theo bucket (giờ / ngày)."""
from datetime import timedelta
import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

HOURLY_BUFFER_HOURS = 48
DAILY_BUFFER_DAYS = 7
AGGREGATION_VERSION = 2


class GridTieInverterSummary(models.Model):
    _name = 'grid.tie.inverter.summary'
    _description = 'Tổng hợp Grid Tie Inverter (giờ/ngày)'
    _order = 'bucket_start desc'

    bucket_start = fields.Datetime(string='Bắt đầu bucket', required=True, index=True)
    bucket_type = fields.Selection([
        ('hour', 'Theo giờ'),
        ('day', 'Theo ngày'),
    ], string='Loại bucket', required=True, index=True)

    device_id = fields.Many2one('smartsolar.device', string='Thiết bị',
                                required=True, ondelete='cascade', index=True)
    system_id = fields.Many2one('smartsolar.system', string='Hệ thống',
                                ondelete='cascade', index=True)
    device_guid = fields.Char(string='Device GUID', index=True)
    company_id = fields.Many2one('res.company', string='Công ty',
                                 related='system_id.company_id', store=True)

    sample_count = fields.Integer(string='Số mẫu')
    online_ratio = fields.Float(string='Tỷ lệ online (%)', digits=(5, 2))
    aggregation_version = fields.Integer(string='Phiên bản tổng hợp', default=AGGREGATION_VERSION)

    dc_voltage_avg = fields.Float(string='DC Voltage TB (V)', digits=(16, 3))
    dc_voltage_max = fields.Float(string='DC Voltage Max (V)', digits=(16, 3))
    ac_voltage_avg = fields.Float(string='AC Voltage TB (V)', digits=(16, 3))

    output_power_avg = fields.Float(string='Công suất ra TB (W)', digits=(16, 3))
    output_power_max = fields.Float(string='Công suất ra Max (W)', digits=(16, 3))
    total_power_avg = fields.Float(string='Tổng công suất TB (W)', digits=(16, 3))
    total_power_max = fields.Float(string='Tổng công suất Max (W)', digits=(16, 3))
    limiter_power_avg = fields.Float(string='Limiter Power TB (W)', digits=(16, 3))

    energy_kwh = fields.Float(string='Năng lượng bucket (kWh)', digits=(16, 3))
    energy_total_start = fields.Float(string='Tổng năng lượng đầu bucket (kWh)', digits=(16, 3))
    energy_total_end = fields.Float(string='Tổng năng lượng cuối bucket (kWh)', digits=(16, 3))
    counter_reset_count = fields.Integer(string='Số lần reset counter')
    limiter_energy_kwh = fields.Float(string='Điện inverter cấp tải bucket (kWh)', digits=(16, 3))
    limiter_total_start = fields.Float(string='Limiter total đầu bucket (kWh)', digits=(16, 3))
    limiter_total_end = fields.Float(string='Limiter total cuối bucket (kWh)', digits=(16, 3))
    limiter_reset_count = fields.Integer(string='Số lần reset limiter counter')

    temperature_avg = fields.Float(string='Nhiệt độ TB (°C)', digits=(16, 1))
    temperature_max = fields.Float(string='Nhiệt độ Max (°C)', digits=(16, 1))

    _sql_constraints = [
        ('bucket_unique', 'unique(bucket_start, bucket_type, device_id)',
         'Mỗi bucket chỉ có một record per device!'),
    ]

    @api.depends('bucket_start', 'bucket_type', 'device_guid')
    def _compute_display_name(self):
        for record in self:
            label = record.bucket_start and record.bucket_start.strftime(
                '%Y-%m-%d %H:%M' if record.bucket_type == 'hour' else '%Y-%m-%d'
            ) or ''
            record.display_name = f"{record.device_guid or 'N/A'} - {label}"

    @api.model
    def _aggregate_hourly(self, lookback_hours=None):
        now = fields.Datetime.now()
        end_bucket = now.replace(minute=0, second=0, microsecond=0)
        hours = max(1, int(lookback_hours or HOURLY_BUFFER_HOURS))
        start_bucket = end_bucket - timedelta(hours=hours)
        try:
            sync_interval = max(1, int(self.env['ir.config_parameter'].sudo().get_param(
                'smartsolar.sync_interval_seconds', '60')))
        except (TypeError, ValueError):
            sync_interval = 60

        self.env.cr.execute("""
            WITH active_devices AS (
                SELECT DISTINCT device_id
                  FROM grid_tie_inverter
                 WHERE record_date >= %s AND record_date < %s
                   AND device_id IS NOT NULL
            ), source_rows AS (
                SELECT r.id, r.record_date, r.device_id, r.system_id, r.device_guid,
                       r.is_online, r.dc_voltage, r.ac_voltage,
                       r.output_power, r.total_power, r.limiter_power,
                       r.energy_total, r.limiter_total, r.temperature
                  FROM grid_tie_inverter r
                 WHERE r.record_date >= %s AND r.record_date < %s
                   AND r.device_id IS NOT NULL
                UNION ALL
                SELECT p.id, p.record_date, p.device_id, p.system_id, p.device_guid,
                       p.is_online, p.dc_voltage, p.ac_voltage,
                       p.output_power, p.total_power, p.limiter_power,
                       p.energy_total, p.limiter_total, p.temperature
                  FROM active_devices d
                  JOIN LATERAL (
                        SELECT r.* FROM grid_tie_inverter r
                         WHERE r.device_id = d.device_id AND r.record_date < %s
                      ORDER BY r.record_date DESC, r.id DESC LIMIT 1
                  ) p ON TRUE
            ), energy_ordered AS (
                SELECT id,
                       LAG(energy_total) OVER (
                           PARTITION BY device_id ORDER BY record_date, id
                       ) AS previous_energy_total
                  FROM source_rows
                 WHERE energy_total IS NOT NULL AND energy_total <> 0
            ), limiter_ordered AS (
                SELECT id,
                       LAG(limiter_total) OVER (
                           PARTITION BY device_id ORDER BY record_date, id
                       ) AS previous_limiter_total
                  FROM source_rows
                 WHERE limiter_total IS NOT NULL AND limiter_total <> 0
            ), ordered AS (
                SELECT s.*, e.previous_energy_total, l.previous_limiter_total
                  FROM source_rows s
             LEFT JOIN energy_ordered e ON e.id = s.id
             LEFT JOIN limiter_ordered l ON l.id = s.id
            )
            INSERT INTO grid_tie_inverter_summary (
                bucket_start, bucket_type, device_id, system_id, device_guid,
                sample_count, online_ratio, aggregation_version,
                dc_voltage_avg, dc_voltage_max, ac_voltage_avg,
                output_power_avg, output_power_max,
                total_power_avg, total_power_max,
                limiter_power_avg,
                energy_kwh, energy_total_start, energy_total_end, counter_reset_count,
                limiter_energy_kwh, limiter_total_start, limiter_total_end,
                limiter_reset_count,
                temperature_avg, temperature_max,
                create_uid, write_uid, create_date, write_date
            )
            SELECT
                date_trunc('hour', record_date) AS bucket_start,
                'hour'::varchar,
                device_id,
                (ARRAY_AGG(system_id ORDER BY record_date DESC, id DESC))[1],
                (ARRAY_AGG(device_guid ORDER BY record_date DESC, id DESC))[1],
                COUNT(*),
                LEAST(
                    SUM(CASE WHEN is_online THEN 1.0 ELSE 0.0 END)
                    * %s / 3600.0 * 100.0,
                    100.0
                ),
                %s,
                AVG(dc_voltage), MAX(dc_voltage), AVG(ac_voltage),
                AVG(output_power), MAX(output_power),
                AVG(total_power), MAX(total_power),
                AVG(limiter_power),
                COALESCE(SUM(CASE
                    WHEN energy_total IS NULL OR previous_energy_total IS NULL THEN 0
                    WHEN energy_total >= previous_energy_total THEN energy_total - previous_energy_total
                    ELSE GREATEST(energy_total, 0)
                END), 0),
                (ARRAY_AGG(energy_total ORDER BY record_date, id)
                    FILTER (WHERE energy_total IS NOT NULL AND energy_total <> 0))[1],
                (ARRAY_AGG(energy_total ORDER BY record_date DESC, id DESC)
                    FILTER (WHERE energy_total IS NOT NULL AND energy_total <> 0))[1],
                COUNT(*) FILTER (
                    WHERE previous_energy_total IS NOT NULL
                      AND energy_total < previous_energy_total
                ),
                COALESCE(SUM(CASE
                    WHEN limiter_total IS NULL OR previous_limiter_total IS NULL THEN 0
                    WHEN limiter_total >= previous_limiter_total
                        THEN limiter_total - previous_limiter_total
                    ELSE GREATEST(limiter_total, 0)
                END), 0),
                (ARRAY_AGG(limiter_total ORDER BY record_date, id)
                    FILTER (WHERE limiter_total IS NOT NULL AND limiter_total <> 0))[1],
                (ARRAY_AGG(limiter_total ORDER BY record_date DESC, id DESC)
                    FILTER (WHERE limiter_total IS NOT NULL AND limiter_total <> 0))[1],
                COUNT(*) FILTER (
                    WHERE previous_limiter_total IS NOT NULL
                      AND limiter_total < previous_limiter_total
                ),
                AVG(temperature), MAX(temperature),
                1, 1, NOW() AT TIME ZONE 'UTC', NOW() AT TIME ZONE 'UTC'
            FROM ordered
            WHERE record_date >= %s AND record_date < %s
            GROUP BY date_trunc('hour', record_date), device_id
            ON CONFLICT (bucket_start, bucket_type, device_id) DO UPDATE SET
                system_id = EXCLUDED.system_id,
                device_guid = EXCLUDED.device_guid,
                sample_count = EXCLUDED.sample_count,
                online_ratio = EXCLUDED.online_ratio,
                aggregation_version = EXCLUDED.aggregation_version,
                dc_voltage_avg = EXCLUDED.dc_voltage_avg,
                dc_voltage_max = EXCLUDED.dc_voltage_max,
                ac_voltage_avg = EXCLUDED.ac_voltage_avg,
                output_power_avg = EXCLUDED.output_power_avg,
                output_power_max = EXCLUDED.output_power_max,
                total_power_avg = EXCLUDED.total_power_avg,
                total_power_max = EXCLUDED.total_power_max,
                limiter_power_avg = EXCLUDED.limiter_power_avg,
                energy_kwh = EXCLUDED.energy_kwh,
                energy_total_start = EXCLUDED.energy_total_start,
                energy_total_end = EXCLUDED.energy_total_end,
                counter_reset_count = EXCLUDED.counter_reset_count,
                limiter_energy_kwh = EXCLUDED.limiter_energy_kwh,
                limiter_total_start = EXCLUDED.limiter_total_start,
                limiter_total_end = EXCLUDED.limiter_total_end,
                limiter_reset_count = EXCLUDED.limiter_reset_count,
                temperature_avg = EXCLUDED.temperature_avg,
                temperature_max = EXCLUDED.temperature_max,
                write_date = NOW() AT TIME ZONE 'UTC';
        """, [
            start_bucket, end_bucket, start_bucket, end_bucket, start_bucket,
            sync_interval, AGGREGATION_VERSION, start_bucket, end_bucket,
        ])
        _logger.info('[grid.tie.inverter] Aggregated hourly: %s buckets', self.env.cr.rowcount)

    @api.model
    def _aggregate_daily(self, lookback_days=None):
        now = fields.Datetime.now()
        days = max(1, int(lookback_days or DAILY_BUFFER_DAYS))
        scan_start = now - timedelta(days=days + 2)

        self.env.cr.execute("""
            DELETE FROM grid_tie_inverter_summary
             WHERE bucket_type = 'day' AND bucket_start >= %s
        """, [scan_start])

        self.env.cr.execute("""
            WITH hourly_local AS (
                SELECT h.*,
                       timezone(
                           'UTC',
                           timezone(
                               COALESCE(NULLIF(s.timezone, ''), 'Asia/Ho_Chi_Minh'),
                               date_trunc(
                                   'day',
                                   timezone(
                                       COALESCE(NULLIF(s.timezone, ''), 'Asia/Ho_Chi_Minh'),
                                       h.bucket_start AT TIME ZONE 'UTC'
                                   )
                               )
                           )
                       ) AS local_bucket_start,
                       timezone(
                           'UTC',
                           timezone(
                               COALESCE(NULLIF(s.timezone, ''), 'Asia/Ho_Chi_Minh'),
                               date_trunc(
                                   'day',
                                   timezone(
                                       COALESCE(NULLIF(s.timezone, ''), 'Asia/Ho_Chi_Minh'),
                                       %s AT TIME ZONE 'UTC'
                                   )
                               )
                           )
                       ) AS local_today_start
                  FROM grid_tie_inverter_summary h
                  JOIN smartsolar_system s ON s.id = h.system_id
                 WHERE h.bucket_type = 'hour'
                   AND h.bucket_start >= %s AND h.bucket_start < %s
            ), completed_days AS (
                SELECT * FROM hourly_local
                 WHERE local_bucket_start >= local_today_start - (%s * INTERVAL '1 day')
                   AND local_bucket_start < local_today_start
            )
            INSERT INTO grid_tie_inverter_summary (
                bucket_start, bucket_type, device_id, system_id, device_guid,
                sample_count, online_ratio, aggregation_version,
                dc_voltage_avg, dc_voltage_max, ac_voltage_avg,
                output_power_avg, output_power_max,
                total_power_avg, total_power_max,
                limiter_power_avg,
                energy_kwh, energy_total_start, energy_total_end, counter_reset_count,
                limiter_energy_kwh, limiter_total_start, limiter_total_end,
                limiter_reset_count,
                temperature_avg, temperature_max,
                create_uid, write_uid, create_date, write_date
            )
            SELECT
                local_bucket_start,
                'day'::varchar,
                device_id,
                (ARRAY_AGG(system_id ORDER BY bucket_start DESC))[1],
                (ARRAY_AGG(device_guid ORDER BY bucket_start DESC))[1],
                SUM(sample_count),
                SUM(online_ratio * sample_count) / NULLIF(SUM(sample_count), 0),
                %s,
                SUM(dc_voltage_avg * sample_count) / NULLIF(SUM(sample_count), 0),
                MAX(dc_voltage_max),
                SUM(ac_voltage_avg * sample_count) / NULLIF(SUM(sample_count), 0),
                SUM(output_power_avg * sample_count) / NULLIF(SUM(sample_count), 0),
                MAX(output_power_max),
                SUM(total_power_avg * sample_count) / NULLIF(SUM(sample_count), 0),
                MAX(total_power_max),
                SUM(limiter_power_avg * sample_count) / NULLIF(SUM(sample_count), 0),
                SUM(energy_kwh),
                (ARRAY_AGG(energy_total_start ORDER BY bucket_start))[1],
                (ARRAY_AGG(energy_total_end ORDER BY bucket_start DESC))[1],
                SUM(counter_reset_count),
                SUM(limiter_energy_kwh),
                (ARRAY_AGG(limiter_total_start ORDER BY bucket_start))[1],
                (ARRAY_AGG(limiter_total_end ORDER BY bucket_start DESC))[1],
                SUM(limiter_reset_count),
                SUM(temperature_avg * sample_count) / NULLIF(SUM(sample_count), 0),
                MAX(temperature_max),
                1, 1, NOW() AT TIME ZONE 'UTC', NOW() AT TIME ZONE 'UTC'
            FROM completed_days
            GROUP BY local_bucket_start, device_id
            ON CONFLICT (bucket_start, bucket_type, device_id) DO UPDATE SET
                system_id = EXCLUDED.system_id,
                device_guid = EXCLUDED.device_guid,
                sample_count = EXCLUDED.sample_count,
                online_ratio = EXCLUDED.online_ratio,
                aggregation_version = EXCLUDED.aggregation_version,
                dc_voltage_avg = EXCLUDED.dc_voltage_avg,
                dc_voltage_max = EXCLUDED.dc_voltage_max,
                ac_voltage_avg = EXCLUDED.ac_voltage_avg,
                output_power_avg = EXCLUDED.output_power_avg,
                output_power_max = EXCLUDED.output_power_max,
                total_power_avg = EXCLUDED.total_power_avg,
                total_power_max = EXCLUDED.total_power_max,
                limiter_power_avg = EXCLUDED.limiter_power_avg,
                energy_kwh = EXCLUDED.energy_kwh,
                energy_total_start = EXCLUDED.energy_total_start,
                energy_total_end = EXCLUDED.energy_total_end,
                counter_reset_count = EXCLUDED.counter_reset_count,
                limiter_energy_kwh = EXCLUDED.limiter_energy_kwh,
                limiter_total_start = EXCLUDED.limiter_total_start,
                limiter_total_end = EXCLUDED.limiter_total_end,
                limiter_reset_count = EXCLUDED.limiter_reset_count,
                temperature_avg = EXCLUDED.temperature_avg,
                temperature_max = EXCLUDED.temperature_max,
                write_date = NOW() AT TIME ZONE 'UTC';
        """, [
            now, scan_start, now, days, AGGREGATION_VERSION,
        ])
        _logger.info('[grid.tie.inverter] Aggregated daily: %s buckets', self.env.cr.rowcount)
