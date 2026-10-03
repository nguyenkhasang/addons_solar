# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged

from ..models.utils import mqsolar_message_to_legacy_api_data


@tagged('post_install', '-at_install', 'smartsolar')
class TestSummaryAggregation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.system = cls.env['smartsolar.system'].create({
            'name': 'Summary test',
            'code': 'SUMMARY-TEST',
            'timezone': 'Asia/Ho_Chi_Minh',
        })
        cls.device = cls.env['smartsolar.device'].create({
            'name': 'GTI test',
            'device_guid': 'SUMMARY-GTI-TEST',
            'device_type': 'grid_tie_inverter',
            'system_id': cls.system.id,
        })

    def _create_gti(self, at, energy_total, limiter_total=0.0, output_power=100.0):
        return self.env['grid.tie.inverter'].create({
            'device_guid': self.device.device_guid,
            'device_id': self.device.id,
            'system_id': self.system.id,
            'record_date': at,
            'is_online': True,
            'energy_total': energy_total,
            'limiter_total': limiter_total,
            'output_power': output_power,
        })

    def test_converter_prefers_canonical_limiter_counters(self):
        converted = mqsolar_message_to_legacy_api_data({
            'deviceId': self.device.device_guid,
            'topic': 'grid_tie_inverter/data',
            'payload': {
                'output_power': 100,
                'limiter_power': 25,
                'limmiter_power': 0,
                'limiter_today': 3,
                'limmiter_today': 0,
                'limiter_total': 12,
                'limmiter_total': 0,
            },
        })
        streams = {
            item['name']: item['value']
            for item in converted['lastMessage']['dataStreams']
        }
        self.assertEqual(streams['limmiter_power'], 25)
        self.assertEqual(streams['limmiter_today'], 3)
        self.assertEqual(streams['limmiter_total'], 12)

    def test_hourly_counter_connects_boundary_and_handles_reset(self):
        hour = (fields.Datetime.now().replace(minute=0, second=0, microsecond=0)
                - timedelta(hours=1))
        self._create_gti(hour - timedelta(seconds=1), 98.0, 48.0)
        self._create_gti(hour + timedelta(minutes=1), 0.0, 0.0)
        self._create_gti(hour + timedelta(minutes=5), 100.0, 50.0)
        self._create_gti(hour + timedelta(minutes=10), 2.0, 1.0)
        self._create_gti(hour + timedelta(minutes=15), 5.0, 4.0)

        self.env['grid.tie.inverter.summary']._aggregate_hourly(2)
        summary = self.env['grid.tie.inverter.summary'].search([
            ('device_id', '=', self.device.id),
            ('bucket_type', '=', 'hour'),
            ('bucket_start', '=', hour),
        ])
        self.assertEqual(len(summary), 1)
        self.assertAlmostEqual(summary.energy_kwh, 7.0, places=3)
        self.assertEqual(summary.energy_total_start, 100.0)
        self.assertEqual(summary.energy_total_end, 5.0)
        self.assertEqual(summary.counter_reset_count, 1)
        self.assertAlmostEqual(summary.limiter_energy_kwh, 6.0, places=3)
        self.assertEqual(summary.limiter_total_start, 50.0)
        self.assertEqual(summary.limiter_total_end, 4.0)
        self.assertEqual(summary.limiter_reset_count, 1)

    def test_daily_average_is_weighted_and_bucket_is_local_midnight(self):
        tz = ZoneInfo(self.system.timezone)
        now_utc = fields.Datetime.now().replace(tzinfo=timezone.utc)
        local_today = now_utc.astimezone(tz).replace(
            hour=0, minute=0, second=0, microsecond=0)
        bucket_start = (local_today - timedelta(days=1)).astimezone(
            timezone.utc).replace(tzinfo=None)

        values = [
            (bucket_start, 60, 10.0),
            (bucket_start + timedelta(hours=1), 1, 100.0),
        ]
        for at, count, avg_power in values:
            self.env['grid.tie.inverter.summary'].create({
                'bucket_start': at,
                'bucket_type': 'hour',
                'device_id': self.device.id,
                'system_id': self.system.id,
                'device_guid': self.device.device_guid,
                'sample_count': count,
                'online_ratio': 100.0,
                'output_power_avg': avg_power,
                'output_power_max': avg_power,
                'energy_kwh': 1.0,
                'energy_total_start': 10.0,
                'energy_total_end': 11.0,
                'limiter_energy_kwh': 2.0,
                'limiter_total_start': 20.0,
                'limiter_total_end': 22.0,
            })

        self.env['grid.tie.inverter.summary']._aggregate_daily(2)
        summary = self.env['grid.tie.inverter.summary'].search([
            ('device_id', '=', self.device.id),
            ('bucket_type', '=', 'day'),
            ('bucket_start', '=', bucket_start),
        ])
        self.assertEqual(len(summary), 1)
        self.assertAlmostEqual(
            summary.output_power_avg, (60 * 10.0 + 100.0) / 61, places=3)
        self.assertEqual(summary.sample_count, 61)
        self.assertEqual(summary.energy_kwh, 2.0)
        self.assertEqual(summary.limiter_energy_kwh, 4.0)
        self.assertEqual(
            summary.bucket_start.replace(tzinfo=timezone.utc).astimezone(tz).hour,
            0,
        )

    def test_daily_rebuild_preserves_history_outside_window(self):
        old_day = (fields.Datetime.now().replace(hour=0, minute=0, second=0,
                                                microsecond=0) - timedelta(days=8))
        for name in ('charge.power.summary', 'grid.tie.inverter.summary'):
            with self.subTest(model=name):
                record = self.env[name].create({
                    'bucket_start': old_day, 'bucket_type': 'day',
                    'device_id': self.device.id, 'system_id': self.system.id,
                    'sample_count': 60, 'energy_kwh': 3.0,
                })
                self.env[name]._aggregate_daily(7)
                self.assertTrue(record.exists())
                self.assertEqual(record.energy_kwh, 3.0)
        if 'smartsolar.environment.summary' in self.env:
            record = self.env['smartsolar.environment.summary'].create({
                'bucket_start': old_day, 'bucket_type': 'day',
                'system_id': self.system.id, 'sample_count': 60,
            })
            self.env['smartsolar.environment.summary']._aggregate_daily(7)
            self.assertTrue(record.exists())

    def test_daily_online_uses_whole_day_including_missing_hours(self):
        tz = ZoneInfo(self.system.timezone)
        today = fields.Datetime.now().replace(tzinfo=timezone.utc).astimezone(tz)
        day = (today.replace(hour=0, minute=0, second=0, microsecond=0)
               - timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None)
        for name in ('charge.power.summary', 'grid.tie.inverter.summary'):
            with self.subTest(model=name):
                self.env[name].create([
                    {'bucket_start': day, 'bucket_type': 'hour',
                     'device_id': self.device.id, 'system_id': self.system.id,
                     'sample_count': 60, 'online_ratio': 100.0},
                    {'bucket_start': day + timedelta(hours=1), 'bucket_type': 'hour',
                     'device_id': self.device.id, 'system_id': self.system.id,
                     'sample_count': 30, 'online_ratio': 50.0},
                ])
                self.env[name]._aggregate_daily(2)
                summary = self.env[name].search([
                    ('device_id', '=', self.device.id), ('bucket_type', '=', 'day'),
                    ('bucket_start', '=', day),
                ])
                self.assertEqual(len(summary), 1)
                self.assertAlmostEqual(summary.online_ratio, 150.0 / 24, places=3)

    def test_failed_hourly_run_propagates_and_keeps_checkpoint(self):
        icp = self.env['ir.config_parameter'].sudo()
        key = 'smartsolar.summary_hourly_last_success'
        original = icp.get_param(key)
        with patch.object(type(self.env['grid.tie.inverter.summary']),
                          '_aggregate_hourly', side_effect=RuntimeError('test failure')):
            with self.assertRaisesRegex(RuntimeError, 'test failure'):
                with self.env.cr.savepoint():
                    self.env['smartsolar.system']._cron_aggregate_hourly()
        self.assertEqual(icp.get_param(key), original)

    def test_hourly_cron_catches_up_beyond_normal_buffer(self):
        hour = (fields.Datetime.now().replace(minute=0, second=0, microsecond=0)
                - timedelta(days=6))
        self._create_gti(hour + timedelta(minutes=1), 10.0)
        self._create_gti(hour + timedelta(minutes=2), 11.0)
        self.env['ir.config_parameter'].sudo().set_param(
            'smartsolar.summary_hourly_last_success', hour - timedelta(days=1))
        self.env['smartsolar.system']._cron_aggregate_hourly()
        summary = self.env['grid.tie.inverter.summary'].search([
            ('device_id', '=', self.device.id), ('bucket_type', '=', 'hour'),
            ('bucket_start', '=', hour),
        ])
        self.assertEqual(len(summary), 1)
        self.assertAlmostEqual(summary.energy_kwh, 1.0, places=3)

    def test_daily_cron_catches_up_beyond_normal_buffer(self):
        tz = ZoneInfo(self.system.timezone)
        today = fields.Datetime.now().replace(tzinfo=timezone.utc).astimezone(tz)
        day = (today.replace(hour=0, minute=0, second=0, microsecond=0)
               - timedelta(days=10)).astimezone(timezone.utc).replace(tzinfo=None)
        self.env['grid.tie.inverter.summary'].create({
            'bucket_start': day, 'bucket_type': 'hour',
            'device_id': self.device.id, 'system_id': self.system.id,
            'sample_count': 60, 'energy_kwh': 2.0,
        })
        self.env['ir.config_parameter'].sudo().set_param(
            'smartsolar.summary_daily_last_success', day - timedelta(days=1))
        self.env['smartsolar.system']._cron_aggregate_daily()
        summary = self.env['grid.tie.inverter.summary'].search([
            ('device_id', '=', self.device.id), ('bucket_type', '=', 'day'),
            ('bucket_start', '=', day),
        ])
        self.assertEqual(len(summary), 1)
        self.assertAlmostEqual(summary.energy_kwh, 2.0, places=3)

    def test_purge_does_not_delete_raw_after_aggregation_failure(self):
        raw = self._create_gti(fields.Datetime.now() - timedelta(days=40), 5.0)
        with patch.object(type(self.env['smartsolar.system']),
                          '_cron_aggregate_daily', side_effect=RuntimeError('test failure')):
            with self.assertRaisesRegex(RuntimeError, 'test failure'):
                with self.env.cr.savepoint():
                    self.env['smartsolar.system']._cron_purge_old_data()
        self.assertTrue(raw.exists())

    def test_converter_marks_missing_counter_and_valid_zero_separately(self):
        for topic, model, field in [('grid_tie_inverter/data', 'grid.tie.inverter', 'energy_total'),
                                    ('mppt_charger/data', 'charge.power', 'total_kwh')]:
            for payload, expected in [({'temperature': 10}, False),
                                      ({'temperature': 10, field: 0}, True)]:
                converted = mqsolar_message_to_legacy_api_data({
                    'deviceId': self.device.device_guid, 'topic': topic, 'payload': payload})
                record = self.env[model].create_from_api_data(converted, system_id=self.system.id,
                                                            device_id=self.device.id)
                self.assertIs(record.counter_validity[field], expected)

    def test_charge_counter_missing_zero_is_not_a_false_reset(self):
        hour = fields.Datetime.now().replace(minute=0, second=0, microsecond=0)-timedelta(hours=1)
        for minute, value, validity in [(0, 10, True), (1, 0, False), (2, 11, True), (3, 0, True), (4, 2, True)]:
            self.env['charge.power'].create({
                'device_id': self.device.id, 'system_id': self.system.id, 'record_date': hour+timedelta(minutes=minute),
                'device_guid': self.device.device_guid, 'total_kwh': value, 'counter_validity': {'total_kwh': validity}})
        self.env['charge.power.summary']._aggregate_hourly(2)
        record = self.env['charge.power.summary'].search([
            ('device_id', '=', self.device.id), ('bucket_type', '=', 'hour'), ('bucket_start', '=', hour)])
        self.assertAlmostEqual(record.energy_kwh, 3)
        self.assertEqual(record.counter_reset_count, 1)
        self.assertEqual(record.counter_quality['total_kwh']['invalid_samples'], 1)
