# -*- coding: utf-8 -*-
"""Tổng hợp dữ liệu Grid Tie Inverter theo bucket (giờ / ngày)."""
from datetime import timedelta
import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

HOURLY_BUFFER_HOURS = 2
DAILY_BUFFER_DAYS = 2


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

    dc_voltage_avg = fields.Float(string='DC Voltage TB (V)', digits=(16, 3))
    dc_voltage_max = fields.Float(string='DC Voltage Max (V)', digits=(16, 3))
    ac_voltage_avg = fields.Float(string='AC Voltage TB (V)', digits=(16, 3))

    output_power_avg = fields.Float(string='Công suất ra TB (W)', digits=(16, 3))
    output_power_max = fields.Float(string='Công suất ra Max (W)', digits=(16, 3))
    total_power_avg = fields.Float(string='Tổng công suất TB (W)', digits=(16, 3))
    total_power_max = fields.Float(string='Tổng công suất Max (W)', digits=(16, 3))
    limiter_power_avg = fields.Float(string='Limiter Power TB (W)', digits=(16, 3))

    energy_kwh = fields.Float(string='Năng lượng bucket (kWh)', digits=(16, 3))
    energy_total_end = fields.Float(string='Tổng năng lượng cuối bucket (kWh)', digits=(16, 3))

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
    def _aggregate_hourly(self):
        now = fields.Datetime.now()
        end_bucket = now.replace(minute=0, second=0, microsecond=0)
        start_bucket = end_bucket - timedelta(hours=HOURLY_BUFFER_HOURS)

        self.env.cr.execute("""
            INSERT INTO grid_tie_inverter_summary (
                bucket_start, bucket_type, device_id, system_id, device_guid,
                sample_count, online_ratio,
                dc_voltage_avg, dc_voltage_max, ac_voltage_avg,
                output_power_avg, output_power_max,
                total_power_avg, total_power_max,
                limiter_power_avg,
                energy_kwh, energy_total_end,
                temperature_avg, temperature_max,
                create_uid, write_uid, create_date, write_date
            )
            SELECT
                date_trunc('hour', record_date) AS bucket_start,
                'hour'::varchar,
                device_id,
                MAX(system_id),
                MIN(device_guid),
                COUNT(*),
                AVG(CASE WHEN is_online THEN 100.0 ELSE 0.0 END),
                AVG(dc_voltage), MAX(dc_voltage), AVG(ac_voltage),
                AVG(output_power), MAX(output_power),
                AVG(total_power), MAX(total_power),
                AVG(limiter_power),
                COALESCE(MAX(energy_total) - MIN(NULLIF(energy_total, 0)), 0),
                MAX(energy_total),
                AVG(temperature), MAX(temperature),
                1, 1, NOW() AT TIME ZONE 'UTC', NOW() AT TIME ZONE 'UTC'
            FROM grid_tie_inverter
            WHERE record_date >= %s AND record_date < %s
              AND device_id IS NOT NULL
            GROUP BY date_trunc('hour', record_date), device_id
            ON CONFLICT (bucket_start, bucket_type, device_id) DO UPDATE SET
                system_id = EXCLUDED.system_id,
                device_guid = EXCLUDED.device_guid,
                sample_count = EXCLUDED.sample_count,
                online_ratio = EXCLUDED.online_ratio,
                dc_voltage_avg = EXCLUDED.dc_voltage_avg,
                dc_voltage_max = EXCLUDED.dc_voltage_max,
                ac_voltage_avg = EXCLUDED.ac_voltage_avg,
                output_power_avg = EXCLUDED.output_power_avg,
                output_power_max = EXCLUDED.output_power_max,
                total_power_avg = EXCLUDED.total_power_avg,
                total_power_max = EXCLUDED.total_power_max,
                limiter_power_avg = EXCLUDED.limiter_power_avg,
                energy_kwh = EXCLUDED.energy_kwh,
                energy_total_end = EXCLUDED.energy_total_end,
                temperature_avg = EXCLUDED.temperature_avg,
                temperature_max = EXCLUDED.temperature_max,
                write_date = NOW() AT TIME ZONE 'UTC';
        """, [start_bucket, end_bucket])
        _logger.info('[grid.tie.inverter] Aggregated hourly: %s buckets', self.env.cr.rowcount)

    @api.model
    def _aggregate_daily(self):
        now = fields.Datetime.now()
        end_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_day = end_day - timedelta(days=DAILY_BUFFER_DAYS)

        self.env.cr.execute("""
            INSERT INTO grid_tie_inverter_summary (
                bucket_start, bucket_type, device_id, system_id, device_guid,
                sample_count, online_ratio,
                dc_voltage_avg, dc_voltage_max, ac_voltage_avg,
                output_power_avg, output_power_max,
                total_power_avg, total_power_max,
                limiter_power_avg,
                energy_kwh, energy_total_end,
                temperature_avg, temperature_max,
                create_uid, write_uid, create_date, write_date
            )
            SELECT
                date_trunc('day', bucket_start) AS bucket_start,
                'day'::varchar,
                device_id,
                MAX(system_id),
                MIN(device_guid),
                SUM(sample_count),
                AVG(online_ratio),
                AVG(dc_voltage_avg), MAX(dc_voltage_max), AVG(ac_voltage_avg),
                AVG(output_power_avg), MAX(output_power_max),
                AVG(total_power_avg), MAX(total_power_max),
                AVG(limiter_power_avg),
                SUM(energy_kwh),
                MAX(energy_total_end),
                AVG(temperature_avg), MAX(temperature_max),
                1, 1, NOW() AT TIME ZONE 'UTC', NOW() AT TIME ZONE 'UTC'
            FROM grid_tie_inverter_summary
            WHERE bucket_type = 'hour'
              AND bucket_start >= %s AND bucket_start < %s
            GROUP BY date_trunc('day', bucket_start), device_id
            ON CONFLICT (bucket_start, bucket_type, device_id) DO UPDATE SET
                system_id = EXCLUDED.system_id,
                device_guid = EXCLUDED.device_guid,
                sample_count = EXCLUDED.sample_count,
                online_ratio = EXCLUDED.online_ratio,
                dc_voltage_avg = EXCLUDED.dc_voltage_avg,
                dc_voltage_max = EXCLUDED.dc_voltage_max,
                ac_voltage_avg = EXCLUDED.ac_voltage_avg,
                output_power_avg = EXCLUDED.output_power_avg,
                output_power_max = EXCLUDED.output_power_max,
                total_power_avg = EXCLUDED.total_power_avg,
                total_power_max = EXCLUDED.total_power_max,
                limiter_power_avg = EXCLUDED.limiter_power_avg,
                energy_kwh = EXCLUDED.energy_kwh,
                energy_total_end = EXCLUDED.energy_total_end,
                temperature_avg = EXCLUDED.temperature_avg,
                temperature_max = EXCLUDED.temperature_max,
                write_date = NOW() AT TIME ZONE 'UTC';
        """, [start_day, end_day])
        _logger.info('[grid.tie.inverter] Aggregated daily: %s buckets', self.env.cr.rowcount)
