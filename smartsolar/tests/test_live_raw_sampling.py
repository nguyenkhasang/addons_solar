"""Database proof for ten-second sampling, retry/restart and mixed-rate summaries."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'smartsolar')
class TestLiveRawSampling(TransactionCase):
    def setUp(self):
        super().setUp()
        self.env['ir.config_parameter'].sudo().set_param('smartsolar.raw_sample_seconds', 10)
        self.system = self.env['smartsolar.system'].create({'name': 'Raw 10s', 'code': 'RAW10-TEST'})
        self.devices = {}
        for kind, guid in [('charge_power', 'RAW10-MPPT'), ('grid_tie_inverter', 'RAW10-GTI')]:
            self.devices[kind] = self.env['smartsolar.device'].create({
                'name': guid, 'device_guid': guid, 'device_type': kind, 'system_id': self.system.id})
        self.start = datetime(2026, 10, 8)

    def frame(self, kind='charge_power', offset=0):
        device = self.devices[kind]
        return {'topic': 'mppt_charger' if kind == 'charge_power' else 'grid_tie_inverter',
                'deviceId': device.device_guid,
                '_received_at': (self.start + timedelta(seconds=offset)).replace(tzinfo=timezone.utc).timestamp(),
                'payload': {'pv_voltage': 100, 'pv_current': 0, 'bat_voltage': 50,
                            'bat_current': 0, 'total_kwh': 0,
                            'output_power': 0, 'limiter_power': 0, 'energy_total': 0, 'limiter_total': 0}}

    def raw(self, kind):
        name = 'charge.power' if kind == 'charge_power' else 'grid.tie.inverter'
        return self.env[name].search([('device_id', '=', self.devices[kind].id)], order='record_date')

    def test_exact_ten_second_gate_and_per_device_independence(self):
        for kind, device in self.devices.items():
            self.assertTrue(device._save_mqsolar_raw_sample(self.frame(kind)))
            self.assertFalse(device._save_mqsolar_raw_sample(self.frame(kind, 9.999)))
            self.assertTrue(device._save_mqsolar_raw_sample(self.frame(kind, 10)))
            self.assertEqual(len(self.raw(kind)), 2)
            self.assertEqual(self.raw(kind).mapped('sample_interval_seconds'), [10, 10])

    def test_restart_replay_out_of_order_and_long_outage(self):
        device = self.devices['charge_power']
        self.assertTrue(device._save_mqsolar_raw_sample(self.frame(offset=10)))
        self.env.invalidate_all()
        self.assertFalse(device._save_mqsolar_raw_sample(self.frame(offset=10)))
        self.assertFalse(device._save_mqsolar_raw_sample(self.frame(offset=9)))
        self.assertTrue(device._save_mqsolar_raw_sample(self.frame(offset=120)))
        self.assertEqual(len(self.raw('charge_power')), 2)

    def test_configured_rate_can_decrease_and_increase_with_correct_metadata(self):
        device = self.devices['charge_power']
        self.assertTrue(device._save_mqsolar_raw_sample(self.frame()))
        param = self.env['ir.config_parameter'].sudo()
        param.set_param('smartsolar.raw_sample_seconds', 3)
        self.assertFalse(device._save_mqsolar_raw_sample(self.frame(offset=2.999)))
        self.assertTrue(device._save_mqsolar_raw_sample(self.frame(offset=3)))
        param.set_param('smartsolar.raw_sample_seconds', 20)
        self.assertFalse(device._save_mqsolar_raw_sample(self.frame(offset=22.999)))
        self.assertTrue(device._save_mqsolar_raw_sample(self.frame(offset=23)))
        self.assertEqual(self.raw('charge_power').mapped('sample_interval_seconds'), [10, 3, 20])

    def test_listener_context_uses_one_interval_for_schedule_and_database_guard(self):
        device = self.devices['charge_power'].with_context(smartsolar_sample_interval_seconds=25)
        self.assertTrue(device._save_mqsolar_raw_sample(self.frame()))
        self.assertFalse(device._save_mqsolar_raw_sample(self.frame(offset=24.999)))
        self.assertTrue(device._save_mqsolar_raw_sample(self.frame(offset=25)))
        self.assertEqual(self.raw('charge_power').mapped('sample_interval_seconds'), [25, 25])

    def test_bad_or_missing_database_parameter_keeps_default_interval(self):
        param = self.env['ir.config_parameter'].sudo()
        for value in ('bad', '0', '-1', '2.5'):
            param.set_param('smartsolar.raw_sample_seconds', value)
            self.assertEqual(self.devices['charge_power']._get_raw_sample_interval_seconds(), 10)
        param.search([('key', '=', 'smartsolar.raw_sample_seconds')]).unlink()
        self.assertEqual(self.devices['charge_power']._get_raw_sample_interval_seconds(), 10)

    def test_failed_transaction_does_not_consume_sample_slot(self):
        device = self.devices['charge_power']
        with self.assertRaises(ValueError):
            with self.cr.savepoint():
                self.assertTrue(device._save_mqsolar_raw_sample(self.frame()))
                raise ValueError('Simulated transaction failure')
        self.assertFalse(self.raw('charge_power'))
        self.assertTrue(device._save_mqsolar_raw_sample(self.frame()))
        self.assertEqual(len(self.raw('charge_power')), 1)

    def test_invalid_time_is_not_saved_and_zero_measurement_is_saved(self):
        device = self.devices['charge_power']
        for value in [None, 'invalid', float('nan'), float('inf')]:
            raw = self.frame(); raw['_received_at'] = value
            self.assertFalse(device._save_mqsolar_raw_sample(raw))
        self.assertTrue(device._save_mqsolar_raw_sample(self.frame()))
        self.assertEqual(self.raw('charge_power').pv_current, 0)

    def test_legacy_and_new_rates_preserve_hourly_and_daily_coverage(self):
        for kind, device in self.devices.items():
            for offset in [0, 60]:
                self.assertTrue(device._sync_data_from_mqsolar_message(self.frame(kind, offset)))
            for offset in [120, 130]:
                self.assertTrue(device._save_mqsolar_raw_sample(self.frame(kind, offset)))
            self.assertEqual(self.raw(kind).mapped('sample_interval_seconds'), [0, 0, 10, 10])
        self.env['ir.config_parameter'].sudo().set_param('smartsolar.sync_interval_seconds', 60)
        with patch.object(fields.Datetime, 'now', return_value=self.start + timedelta(days=2)):
            for model in ['charge.power.summary', 'grid.tie.inverter.summary']:
                self.env[model]._aggregate_hourly(lookback_hours=60)
                domain = [('device_id', 'in', list(d.id for d in self.devices.values())),
                          ('bucket_type', '=', 'hour'), ('bucket_start', '=', self.start)]
                hour = self.env[model].search(domain)
                self.assertEqual(len(hour), 1)
                self.assertEqual(hour.sample_count, 4)
                self.assertAlmostEqual(hour.online_ratio, 140 / 3600 * 100, places=2)
                self.env[model]._aggregate_daily(lookback_days=3)
                day = self.env[model].search([('device_id', '=', hour.device_id.id), ('bucket_type', '=', 'day')])
                self.assertEqual(len(day), 1)
                self.assertEqual(day.sample_count, 4)
                self.assertAlmostEqual(day.online_ratio, 140 / 86400 * 100, places=2)
                before = (day.sample_count, day.energy_kwh)
                self.env[model]._aggregate_daily(lookback_days=3)
                self.assertEqual((day.sample_count, day.energy_kwh), before)
