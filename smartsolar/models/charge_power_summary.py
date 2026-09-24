# -*- coding: utf-8 -*-
"""Tổng hợp dữ liệu Charge Power theo bucket (giờ / ngày)."""
from datetime import timedelta
import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

HOURLY_BUFFER_HOURS = 48
DAILY_BUFFER_DAYS = 7
AGGREGATION_VERSION = 2


class ChargePowerSummary(models.Model):
    _name = 'charge.power.summary'
    _description = 'Tổng hợp Charge Power (giờ/ngày)'
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

    pv_voltage_avg = fields.Float(string='PV Voltage TB (V)', digits=(16, 3))
    pv_voltage_max = fields.Float(string='PV Voltage Max (V)', digits=(16, 3))
    pv_current_avg = fields.Float(string='PV Current TB (A)', digits=(16, 3))
    pv_input_power_avg = fields.Float(string='PV Input Power TB (W)', digits=(16, 3))
    bat_voltage_avg = fields.Float(string='Bat Voltage TB (V)', digits=(16, 3))
    bat_voltage_min = fields.Float(string='Bat Voltage Min (V)', digits=(16, 3))
    bat_current_avg = fields.Float(string='Bat Current TB (A)', digits=(16, 3))

    charge_power_avg = fields.Float(string='Công suất TB (W)', digits=(16, 3))
    charge_power_max = fields.Float(string='Công suất Max (W)', digits=(16, 3))

    energy_kwh = fields.Float(string='Năng lượng bucket (kWh)', digits=(16, 3))
    total_kwh_start = fields.Float(string='Tổng kWh đầu bucket', digits=(16, 3))
    total_kwh_end = fields.Float(string='Tổng kWh cuối bucket', digits=(16, 3))
    counter_reset_count = fields.Integer(string='Số lần reset counter')

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
                  FROM charge_power
                 WHERE record_date >= %s AND record_date < %s
                   AND device_id IS NOT NULL
            ), source_rows AS (
                SELECT r.id, r.record_date, r.device_id, r.system_id, r.device_guid,
                       r.is_online, r.pv_voltage, r.pv_current,
                       r.bat_voltage, r.bat_current, r.charge_power,
                       r.total_kwh, r.temperature
                  FROM charge_power r
                 WHERE r.record_date >= %s AND r.record_date < %s
                   AND r.device_id IS NOT NULL
                UNION ALL
                SELECT p.id, p.record_date, p.device_id, p.system_id, p.device_guid,
                       p.is_online, p.pv_voltage, p.pv_current,
                       p.bat_voltage, p.bat_current, p.charge_power,
                       p.total_kwh, p.temperature
                  FROM active_devices d
                  JOIN LATERAL (
                        SELECT r.* FROM charge_power r
                         WHERE r.device_id = d.device_id AND r.record_date < %s
                      ORDER BY r.record_date DESC, r.id DESC LIMIT 1
                  ) p ON TRUE
            ), ordered AS (
                SELECT *,
                       LAG(total_kwh) OVER (
                           PARTITION BY device_id ORDER BY record_date, id
                       ) AS previous_total_kwh
                  FROM source_rows
            )
            INSERT INTO charge_power_summary (
                bucket_start, bucket_type, device_id, system_id, device_guid,
                sample_count, online_ratio, aggregation_version,
                pv_voltage_avg, pv_voltage_max, pv_current_avg, pv_input_power_avg,
                bat_voltage_avg, bat_voltage_min, bat_current_avg,
                charge_power_avg, charge_power_max,
                energy_kwh, total_kwh_start, total_kwh_end, counter_reset_count,
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
                AVG(pv_voltage), MAX(pv_voltage), AVG(pv_current),
                AVG(pv_voltage * pv_current),
                AVG(bat_voltage), MIN(NULLIF(bat_voltage, 0)), AVG(bat_current),
                AVG(charge_power), MAX(charge_power),
                COALESCE(SUM(CASE
                    WHEN total_kwh IS NULL OR previous_total_kwh IS NULL THEN 0
                    WHEN total_kwh >= previous_total_kwh THEN total_kwh - previous_total_kwh
                    ELSE GREATEST(total_kwh, 0)
                END), 0),
                (ARRAY_AGG(total_kwh ORDER BY record_date, id))[1],
                (ARRAY_AGG(total_kwh ORDER BY record_date DESC, id DESC))[1],
                COUNT(*) FILTER (
                    WHERE previous_total_kwh IS NOT NULL
                      AND total_kwh < previous_total_kwh
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
                pv_voltage_avg = EXCLUDED.pv_voltage_avg,
                pv_voltage_max = EXCLUDED.pv_voltage_max,
                pv_current_avg = EXCLUDED.pv_current_avg,
                pv_input_power_avg = EXCLUDED.pv_input_power_avg,
                bat_voltage_avg = EXCLUDED.bat_voltage_avg,
                bat_voltage_min = EXCLUDED.bat_voltage_min,
                bat_current_avg = EXCLUDED.bat_current_avg,
                charge_power_avg = EXCLUDED.charge_power_avg,
                charge_power_max = EXCLUDED.charge_power_max,
                energy_kwh = EXCLUDED.energy_kwh,
                total_kwh_start = EXCLUDED.total_kwh_start,
                total_kwh_end = EXCLUDED.total_kwh_end,
                counter_reset_count = EXCLUDED.counter_reset_count,
                temperature_avg = EXCLUDED.temperature_avg,
                temperature_max = EXCLUDED.temperature_max,
                write_date = NOW() AT TIME ZONE 'UTC';
        """, [
            start_bucket, end_bucket, start_bucket, end_bucket, start_bucket,
            sync_interval, AGGREGATION_VERSION, start_bucket, end_bucket,
        ])
        _logger.info('[charge.power] Aggregated hourly: %s buckets', self.env.cr.rowcount)

    @api.model
    def _aggregate_daily(self, lookback_days=None):
        now = fields.Datetime.now()
        days = max(1, int(lookback_days or DAILY_BUFFER_DAYS))
        scan_start = now - timedelta(days=days + 2)

        # V1 stored daily buckets at UTC midnight. Remove the rolling window first
        # so V2 local-midnight buckets cannot coexist with stale UTC-day rows.
        self.env.cr.execute("""
            DELETE FROM charge_power_summary
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
                  FROM charge_power_summary h
                  JOIN smartsolar_system s ON s.id = h.system_id
                 WHERE h.bucket_type = 'hour'
                   AND h.bucket_start >= %s AND h.bucket_start < %s
            ), completed_days AS (
                SELECT * FROM hourly_local
                 WHERE local_bucket_start >= local_today_start - (%s * INTERVAL '1 day')
                   AND local_bucket_start < local_today_start
            )
            INSERT INTO charge_power_summary (
                bucket_start, bucket_type, device_id, system_id, device_guid,
                sample_count, online_ratio, aggregation_version,
                pv_voltage_avg, pv_voltage_max, pv_current_avg, pv_input_power_avg,
                bat_voltage_avg, bat_voltage_min, bat_current_avg,
                charge_power_avg, charge_power_max,
                energy_kwh, total_kwh_start, total_kwh_end, counter_reset_count,
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
                SUM(pv_voltage_avg * sample_count) / NULLIF(SUM(sample_count), 0),
                MAX(pv_voltage_max),
                SUM(pv_current_avg * sample_count) / NULLIF(SUM(sample_count), 0),
                SUM(pv_input_power_avg * sample_count) / NULLIF(SUM(sample_count), 0),
                SUM(bat_voltage_avg * sample_count) / NULLIF(SUM(sample_count), 0),
                MIN(NULLIF(bat_voltage_min, 0)),
                SUM(bat_current_avg * sample_count) / NULLIF(SUM(sample_count), 0),
                SUM(charge_power_avg * sample_count) / NULLIF(SUM(sample_count), 0),
                MAX(charge_power_max),
                SUM(energy_kwh),
                (ARRAY_AGG(total_kwh_start ORDER BY bucket_start))[1],
                (ARRAY_AGG(total_kwh_end ORDER BY bucket_start DESC))[1],
                SUM(counter_reset_count),
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
                pv_voltage_avg = EXCLUDED.pv_voltage_avg,
                pv_voltage_max = EXCLUDED.pv_voltage_max,
                pv_current_avg = EXCLUDED.pv_current_avg,
                pv_input_power_avg = EXCLUDED.pv_input_power_avg,
                bat_voltage_avg = EXCLUDED.bat_voltage_avg,
                bat_voltage_min = EXCLUDED.bat_voltage_min,
                bat_current_avg = EXCLUDED.bat_current_avg,
                charge_power_avg = EXCLUDED.charge_power_avg,
                charge_power_max = EXCLUDED.charge_power_max,
                energy_kwh = EXCLUDED.energy_kwh,
                total_kwh_start = EXCLUDED.total_kwh_start,
                total_kwh_end = EXCLUDED.total_kwh_end,
                counter_reset_count = EXCLUDED.counter_reset_count,
                temperature_avg = EXCLUDED.temperature_avg,
                temperature_max = EXCLUDED.temperature_max,
                write_date = NOW() AT TIME ZONE 'UTC';
        """, [
            now, scan_start, now, days, AGGREGATION_VERSION,
        ])
        _logger.info('[charge.power] Aggregated daily: %s buckets', self.env.cr.rowcount)
