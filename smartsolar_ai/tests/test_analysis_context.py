from datetime import datetime, timedelta
import json

from odoo.tests import TransactionCase, tagged
from odoo import fields

from odoo.addons.smartsolar_ai.domain.dto import HealthResult
from odoo.addons.smartsolar_ai.repositories.alarm_repository import classify_status
from odoo.addons.smartsolar_ai.services.analytics_service import AnalyticsService
from odoo.addons.smartsolar_ai.tools.registry import ToolRegistry


@tagged('post_install', '-at_install', 'smartsolar_ai')
class TestAnalysisContext(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.system = cls.env['smartsolar.system'].create({
            'name': 'Analysis test', 'code': 'analysis_context_test', 'capacity': 2,
            'mqsolar_cloud_token': 'private-test-token',
        })
        cls.device = cls.env['smartsolar.device'].create({
            'device_guid': 'analysis_context_gti', 'system_id': cls.system.id,
            'device_type': 'grid_tie_inverter', 'is_online': True,
        })
        for hour, power in [(1, 300), (2, 900)]:
            cls.env['grid.tie.inverter'].create({
                'device_id': cls.device.id, 'system_id': cls.system.id,
                'device_guid': cls.device.device_guid,
                'record_date': datetime(2026, 7, 1, hour),
                'limiter_power': power, 'energy_total': 10,
                'temperature': 35, 'status': '0.0',
            })
        cls.env.flush_all()
        cls.registry_tools = ToolRegistry(cls.env)

    def _aggregate(self, metrics):
        return self.registry_tools.execute('get_aggregate', {
            'system_id': self.system.id, 'metrics': metrics,
            'start': '2026-07-01T07:00:00', 'end': '2026-07-01T10:00:00',
        })

    def test_aggregate_has_observation_time_and_counter_consistency(self):
        response = self._aggregate(['grid_import_power', 'grid_import_energy_total'])
        self.assertTrue(response['ok'], response['error'])
        metrics = response['data']['metrics']
        self.assertEqual(metrics['grid_import_power']['quality']['last_observed_at'],
                         '2026-07-01T09:00:00+07:00')
        self.assertEqual(metrics['grid_import_power']['quality']['end_gap_seconds'], 3600)
        codes = {item['code'] for item in metrics['grid_import_energy_total']['quality']['warnings']}
        self.assertIn('constant_counter', codes)
        self.assertIn('zero_energy_with_nonzero_power', codes)

    def test_context_returns_capacity_without_secrets(self):
        response = self.registry_tools.execute('get_system_context', {'system_id': self.system.id})
        self.assertTrue(response['ok'], response['error'])
        self.assertEqual(response['data']['systems'][0]['capacity_kw'], 2)
        self.assertNotIn('private-test-token', json.dumps(response))
        self.assertIn('battery', response['data']['metric_groups'])

    def test_trends_batch_preserves_scope_and_full_statistics(self):
        response = self.registry_tools.execute('get_metric_trends', {
            'system_id': self.system.id, 'metrics': ['grid_import_power', 'inverter_temp'],
            'start': '2026-07-01T07:00:00', 'end': '2026-07-01T10:00:00', 'interval': 'raw',
        })
        self.assertTrue(response['ok'], response['error'])
        metric = response['data']['metrics']['grid_import_power']
        self.assertEqual(metric['statistics']['max'], 900)
        self.assertEqual(metric['series']['count'], 2)

    def test_snapshot_reports_missing_recent_data(self):
        response = self.registry_tools.execute('get_snapshot', {
            'system_id': self.system.id, 'metrics': ['grid_import_power'],
        })
        self.assertTrue(response['ok'], response['error'])
        self.assertFalse(response['data']['metrics']['grid_import_power']['available'])
        self.assertIsNone(response['data']['metrics']['grid_import_power']['last'])

    def test_snapshot_separates_multiple_devices_instead_of_claiming_a_total(self):
        second = self.env['smartsolar.device'].create({
            'device_guid': 'analysis_second_gti', 'system_id': self.system.id,
        })
        for device, power in [(self.device, 100), (second, 200)]:
            self.env['grid.tie.inverter'].create({
                'device_id': device.id, 'system_id': self.system.id,
                'device_guid': device.device_guid,
                'record_date': fields.Datetime.now() - timedelta(minutes=1),
                'limiter_power': power,
            })
        self.env.flush_all()
        response = self.registry_tools.execute('get_snapshot', {
            'system_id': self.system.id, 'metrics': ['grid_import_power'],
        })
        self.assertTrue(response['ok'], response['error'])
        readings = response['data']['per_device']
        self.assertEqual({r['metrics']['grid_import_power']['last'] for r in readings}, {100, 200})
        codes = {w['code'] for w in response['data']['metrics']['grid_import_power']['quality']['warnings']}
        self.assertIn('multiple_devices', codes)

    def test_alarm_zero_float_is_normal_and_unknown_code_is_not_fault(self):
        self.assertEqual(classify_status('0.0'), 'normal')
        self.assertEqual(classify_status('4'), 'unknown')
        self.env['grid.tie.inverter'].search([('system_id', '=', self.system.id)]).write({'status': '4'})
        self.env.flush_all()
        response = self.registry_tools.execute('get_alarms', {
            'system_id': self.system.id, 'start': '2026-07-01T07:00:00',
            'end': '2026-07-01T10:00:00',
        })
        self.assertTrue(response['ok'], response['error'])
        alarm = response['data']['alarms'][0]
        self.assertEqual(alarm['severity'], 'info')
        self.assertFalse(alarm['confirmed_fault'])

    def test_partial_health_cannot_imply_complete_assessment(self):
        result = HealthResult(range_local=[], score=100, coverage_pct=60,
                              components={'production': {'available': False},
                                          'thermal': {'available': True}}).to_dict()
        self.assertEqual(result['assessment'], 'partial')
        self.assertEqual(result['missing_components'], ['production'])

    def test_counter_comparison_does_not_fall_back_to_lifetime_total(self):
        self.assertIsNone(AnalyticsService._representative({
            'available': True, 'energy': None, 'last': 500,
        }))

    def test_tool_limits_are_enforced_at_runtime(self):
        response = self._aggregate(['grid_import_power'] * 11)
        self.assertFalse(response['ok'])
        response = self.registry_tools.execute('get_metric_trends', {
            'metrics': ['grid_import_power'] * 7, 'start': 'today', 'end': 'now',
        })
        self.assertFalse(response['ok'])


    def test_context_exposes_only_configured_public_price_and_no_secret(self):
        Param = self.env['ir.config_parameter'].sudo()
        Param.set_param('smartsolar.currency_symbol', '₫')
        Param.set_param('smartsolar_ai.api_key', 'secret-reporting-test')
        for raw, expected in [('2000', 2000.0), ('0', 0.0), ('NaN', None),
                              ('inf', None), ('-100', None), ('invalid', None)]:
            Param.set_param('smartsolar.electricity_price', raw)
            response = self.registry_tools.execute('get_system_context', {'system_id': self.system.id})
            self.assertTrue(response['ok'], response['error'])
            price = response['data']['reporting_settings']['electricity_price']
            self.assertEqual(price['value'], expected)
            self.assertEqual(price['available'], expected is not None)
            self.assertTrue(price['is_estimate'])
            self.assertNotIn('secret-reporting-test', json.dumps(response))
        Param.search([('key', '=', 'smartsolar.electricity_price')]).unlink()
        response = self.registry_tools.execute('get_system_context', {'system_id': self.system.id})
        price = response['data']['reporting_settings']['electricity_price']
        self.assertIsNone(price['value'])
        self.assertFalse(price['available'])

    def test_compact_trend_retains_full_stats_and_exposes_sampling(self):
        from datetime import timedelta
        self.env['grid.tie.inverter'].create([{
            'device_id': self.device.id, 'system_id': self.system.id,
            'device_guid': self.device.device_guid,
            'record_date': datetime(2026, 7, 1, 1) + timedelta(minutes=index),
            'limiter_power': 12345 if index == 31 else 100,
        } for index in range(1, 60)])
        self.env.flush_all()
        response = self.registry_tools.execute('get_metric_trends', {
            'system_id': self.system.id, 'metrics': ['grid_import_power'],
            'start': '2026-07-01T07:00:00', 'end': '2026-07-01T10:00:00', 'interval': 'raw',
        })
        self.assertTrue(response['ok'], response['error'])
        metric = response['data']['metrics']['grid_import_power']
        self.assertEqual(metric['statistics']['max'], 12345)
        self.assertEqual(metric['statistics']['count'], 61)
        self.assertLessEqual(len(metric['series']['points']), 20)
        self.assertTrue(metric['series']['truncated'])
        detailed = self.registry_tools.execute('get_metric_trends', {
            'system_id': self.system.id, 'metrics': ['grid_import_power'],
            'start': '2026-07-01T07:00:00', 'end': '2026-07-01T10:00:00', 'interval': 'raw',
            'max_points': 80,
        })['data']['metrics']['grid_import_power']
        self.assertEqual(detailed['statistics']['max'], 12345)
        self.assertEqual(len(detailed['series']['points']), 61)
        self.assertFalse(detailed['series']['truncated'])


    def test_period_alignment_is_independent_of_missing_measurements(self):
        args = {'system_id': self.system.id, 'metrics': ['grid_import_power'],
                'a_start': '2026-07-01T07:00:00', 'a_end': '2026-07-01T10:00:00',
                'b_start': '2026-06-30T07:00:00', 'b_end': '2026-06-30T10:00:00'}
        response = self.registry_tools.execute('compare_periods', args)
        self.assertTrue(response['ok'], response['error'])
        data = response['data']
        self.assertFalse(data['period_b']['metrics']['grid_import_power']['available'])
        self.assertTrue(data['period_alignment']['equal_duration'])
        self.assertEqual(data['period_alignment']['a_duration_seconds'], 10800)
        args['b_end'] = '2026-06-30T09:00:00'
        data = self.registry_tools.execute('compare_periods', args)['data']
        self.assertFalse(data['period_alignment']['equal_duration'])
        self.assertEqual(data['period_alignment']['b_duration_seconds'], 7200)
