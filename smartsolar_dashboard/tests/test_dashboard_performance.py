from datetime import datetime, timedelta
from unittest.mock import patch
from contextlib import ExitStack

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'smartsolar_dashboard')
class TestDashboardPerformance(TransactionCase):
    def setUp(self):
        super().setUp()
        self.system = self.env['smartsolar.system'].create({
            'name': 'Performance', 'code': 'PERF-TEST', 'timezone': 'Asia/Ho_Chi_Minh'})
        self.at = datetime(2026, 10, 14, 5)
        self.now = self.at+timedelta(hours=1)
        self.mppt = self.device('charge_power', 'PERF-MPPT')
        self.gti = self.device('grid_tie_inverter', 'PERF-GTI')
        self.original = self.env['smartsolar.dashboard']
        self.fast = self.original.with_context(smartsolar_dashboard_read_ledger=True)

    def device(self, kind, guid, system=None):
        return self.env['smartsolar.device'].create({'name': guid, 'device_guid': guid,
            'system_id': (system or self.system).id, 'device_type': kind})

    def charger(self, offset=0, device=None, **extra):
        device = device or self.mppt
        return self.env['charge.power'].create(dict({
            'device_id': device.id, 'system_id': device.system_id.id, 'device_guid': device.device_guid,
            'record_date': self.at+timedelta(seconds=offset),
            'pv_voltage': 100, 'pv_current': 6, 'bat_voltage': 50, 'bat_current': 10}, **extra))

    def inverter(self, offset=0, device=None, **extra):
        device = device or self.gti
        return self.env['grid.tie.inverter'].create(dict({
            'device_id': device.id, 'system_id': device.system_id.id, 'device_guid': device.device_guid,
            'record_date': self.at+timedelta(seconds=offset),
            'output_power': 200, 'limiter_power': 100}, **extra))

    def assert_pairing(self, system_id=None):
        with patch.object(fields.Datetime, 'now', return_value=self.now):
            for period in ('2min', '24h', '1week', '1month', '1year'):
                old = self.original.get_battery_flow_series(period, system_id)
                new = self.fast.get_battery_flow_series(period, system_id)
                self.assertEqual(new, old, period)

    def test_pairing_duplicate_eligibility_nearest_ties_and_missing(self):
        for offset in (0, 60, 120, 180, 240, 300):
            self.charger(offset, bat_voltage=50+offset/60, bat_current=offset/60-2)
            self.inverter(offset-5, output_power=offset)
            self.inverter(offset+5, output_power=offset+10)
        self.charger(60, bat_voltage=0)  # ineligible later duplicate
        self.inverter(65, output_power=900)  # eligible latest duplicate wins
        self.charger(1000)  # no inverter within the pairing window
        self.assert_pairing(self.system.id)

    def test_pairing_multiple_devices_and_systems_keeps_null_rules(self):
        second = self.device('charge_power', 'PERF-CP2')
        second_gti = self.device('grid_tie_inverter', 'PERF-GT2')
        self.charger(); self.inverter()
        self.charger(60); self.inverter(60)
        self.charger(60, second); self.inverter(60, second_gti)
        other = self.env['smartsolar.system'].create({'name': 'Other performance', 'code': 'PERF-OTHER'})
        self.charger(60, self.device('charge_power', 'PERF-OTHER-CP', other))
        self.assert_pairing(None)

    def test_dashboard_does_not_call_removed_widget_queries(self):
        self.charger(); self.inverter()
        with ExitStack() as patches:
            for name in ('get_overview_kpi', 'get_battery_series', 'get_monthly_comparison',
                         'get_bms_history', 'get_performance_alert', '_get_today_energy',
                         '_get_today_grid_export', '_get_today_grid_import', '_get_today_pv_energy'):
                patches.enter_context(patch.object(type(self.original), name,
                    side_effect=AssertionError('Removed widget query: '+name)))
            patches.enter_context(patch.object(fields.Datetime, 'now', return_value=self.now))
            data = self.fast.get_dashboard_data('24h', self.system.id)
        for name in ('battery', 'monthly_comparison', 'bms_history'):
            self.assertNotIn(name, data)
        self.assertIn('battery_flow', data)
        self.assertIn('pv_peak', data['kpi'])
        self.assertIn('energy_widgets', data['kpi'])

    def ledger(self):
        return self.env['smartsolar.lifetime'].create({
            'system_id': self.system.id, 'first_day': '2026-10-01',
            'updated_at': self.now, 'initialized': True})

    def test_closed_ledger_day_avoids_raw_and_pending_day_falls_back(self):
        account = self.ledger()
        day = self.env['smartsolar.lifetime.day'].create({'lifetime_id': account.id,
            'day': '2026-10-02', 'known': True, 'inverter_kwh': 5, 'grid_kwh': 2,
            'pv_known': True, 'pv_kwh': 7, 'estimated': True, 'coverage_pct': 81})
        bounds = (datetime(2026, 10, 1, 17), datetime(2026, 10, 2, 17))
        with patch.object(type(self.original), '_energy_distribution_for_range',
                          side_effect=AssertionError('Closed day must not scan raw')):
            result = self.fast.get_energy_comparison('1month', self.system.id, date_bounds=bounds)
        self.assertEqual(result['grid_kwh'], [2])
        self.assertEqual(result['coverage_pct'], [81])
        account.pending_days = ['2026-10-02']
        result = self.fast.get_energy_comparison('1month', self.system.id, date_bounds=bounds)
        self.assertEqual(result['grid_kwh'], [None])

    def test_heatmap_weighted_values_match_raw_and_late_edit_invalidates_summary(self):
        self.charger(); self.charger(60, pv_current=12)
        self.inverter(); self.inverter(60, output_power=400)
        with patch.object(fields.Datetime, 'now', return_value=self.now):
            expected = self.original.get_heatmap_data(self.system.id)
            actual = self.fast.get_heatmap_data(self.system.id)
        self.assertEqual(actual, expected)
        summary = self.env['grid.tie.inverter.summary'].create({
            'device_id': self.gti.id, 'system_id': self.system.id,
            'bucket_type': 'hour', 'bucket_start': self.at, 'quality_version': 4,
            'sample_count': 2, 'metric_statistics': {
                'output_power': {'sum': 600, 'count': 2},
                'total_load_power': {'sum': 800, 'count': 2}}})
        self.inverter(120, output_power=900)  # summary count is stale
        with patch.object(fields.Datetime, 'now', return_value=self.now+timedelta(minutes=1)):
            self.assertEqual(self.fast.get_heatmap_data(self.system.id),
                             self.original.get_heatmap_data(self.system.id))

    def test_request_reuses_today_between_global_single_meter_and_chart(self):
        self.ledger(); self.charger(); self.charger(60); self.inverter(); self.inverter(60)
        start = datetime(2026, 10, 13, 17)
        cache = {}
        with patch.object(type(self.original), '_energy_distribution_for_range',
                          wraps=self.original._energy_distribution_for_range) as raw:
            self.fast.get_energy_comparison('1month', self.system.id, cache,
                                           date_bounds=(start, self.now))
            count = raw.call_count
            self.fast.get_energy_comparison('1week', self.system.id, cache,
                                           date_bounds=(start, self.now))
        self.assertEqual(raw.call_count, count)

    def test_charts_only_does_not_repeat_overview_and_uses_snapshot_cutoff(self):
        self.charger(); self.inverter()
        self.charger(120, pv_current=1000)
        cutoff = self.at+timedelta(seconds=60)
        with patch.object(type(self.original), '_get_dashboard_overview',
                          side_effect=AssertionError('Charts must not repeat overview')):
            result = self.fast.get_dashboard_data('24h', self.system.id,
                                                  end_at=cutoff, include_overview=False)
        self.assertNotIn('kpi', result)
        self.assertLess(max(result['charge_power']['avg_power']), 100000)
        cfg, start, end = self.fast.with_context(smartsolar_dashboard_end_at=cutoff)._resolve_time_range('24h', self.system.id)
        self.assertEqual(end, cutoff)
        self.assertEqual(end-start, timedelta(days=1))

    def test_counter_with_no_usable_observation_needs_no_seed_but_declared_zero_is_valid(self):
        self.inverter(-60, limiter_total=10)
        row = self.inverter(0, limiter_total=0)
        result = self.original._raw_counter_delta('limiter_total', self.at,
                                                  self.at+timedelta(seconds=60), self.system.id)
        self.assertEqual(result, (0, 0))
        row.counter_validity = {'limiter_total': True}
        self.env.flush_all()
        result = self.original._raw_counter_delta('limiter_total', self.at,
                                                  self.at+timedelta(seconds=60), self.system.id)
        self.assertEqual(result, (0, 1))

    def assert_shared_series(self, period, system_id):
        legacy = self.original.with_context(smartsolar_dashboard_end_at=self.now,
                                            smartsolar_chart_rows=None)
        shared = legacy.with_context(smartsolar_chart_rows={})
        for name in ('charge_power', 'grid_tie', 'temperature', 'pv_efficiency'):
            kwargs = {'balance': {'labels': [], 'mppt_output_power': []}} if name == 'pv_efficiency' else {}
            expected = getattr(legacy, 'get_'+name+'_series')(period, system_id, **kwargs)
            actual = getattr(shared, 'get_'+name+'_series')(period, system_id, **kwargs)
            self.assertEqual(actual, expected, (period, system_id, name))
        return shared

    def test_shared_raw_scans_preserve_nulls_products_devices_and_request_scope(self):
        second = self.device('charge_power', 'SHARED-MPPT-2')
        for offset, volts, amps in ((0, 100, 2), (60, 200, 1), (120, 0, 0)):
            self.charger(offset, pv_voltage=volts, pv_current=amps, temperature=30+offset)
            self.inverter(offset, output_power=offset, temperature=40+offset)
        self.charger(60, second, pv_voltage=300, pv_current=4, temperature=70)
        other = self.env['smartsolar.system'].create({'name': 'Shared other', 'code': 'SHARED-OTHER'})
        self.charger(3590, self.device('charge_power', 'SHARED-OTHER-CP', other),
                     pv_voltage=50, pv_current=7, temperature=21)
        self.inverter(3590, self.device('grid_tie_inverter', 'SHARED-OTHER-GT', other), temperature=55)
        orphan = self.charger(180, temperature=99)
        self.env.flush_all()
        self.env.cr.execute('UPDATE charge_power SET device_id=NULL, pv_current=NULL WHERE id=%s', [orphan.id])
        for period in ('2min', '1h', '6h', '12h', '24h'):
            for scope in (self.system.id, None):
                shared = self.assert_shared_series(period, scope)
                self.assertEqual(len(shared.env.context['smartsolar_chart_rows']), 1)
        shared = self.assert_shared_series('24h', self.system.id)
        cfg, start, end = shared._resolve_time_range('24h', self.system.id)
        queries = self.env.cr.sql_log_count
        shared._read_chart_bundle(cfg, start, end, self.system.id)
        self.assertEqual(self.env.cr.sql_log_count-queries, 2)
        # A later standalone request must not reuse data from an earlier snapshot.
        self.charger(240, pv_voltage=500, pv_current=10)
        self.env.flush_all()
        later = self.assert_shared_series('24h', self.system.id)
        self.assertIsNot(shared.env.context['smartsolar_chart_rows'], later.env.context['smartsolar_chart_rows'])

    def test_shared_summary_weights_edges_and_timezones_match_standalone(self):
        second = self.device('charge_power', 'SHARED-SUMMARY-2')
        for kind in ('hour', 'day'):
            for device, count, voltage in ((self.mppt, 3, 100), (second, 7, 200)):
                self.env['charge.power.summary'].create({'device_id': device.id,
                    'system_id': self.system.id, 'bucket_type': kind,
                    'bucket_start': self.at-timedelta(days=2), 'sample_count': count,
                    'pv_voltage_avg': voltage, 'pv_current_avg': 3,
                    'pv_input_power_avg': voltage*2, 'temperature_avg': voltage/10,
                    'temperature_max': voltage/5})
            self.env['grid.tie.inverter.summary'].create({'device_id': self.gti.id,
                'system_id': self.system.id, 'bucket_type': kind,
                'bucket_start': self.at-timedelta(days=2), 'sample_count': 5,
                'output_power_avg': 123, 'output_power_max': 456,
                'temperature_avg': 30, 'temperature_max': 50})
        self.charger(); self.inverter()
        self.env.flush_all()
        for tz in ('Asia/Ho_Chi_Minh', 'Asia/Kathmandu', 'America/New_York'):
            self.system.timezone = tz
            for period in ('1week', '1month', '3month', '6month', '1year', '5year'):
                self.assert_shared_series(period, self.system.id)

    def test_shared_empty_and_device_subset_keep_legacy_contract(self):
        empty = self.env['smartsolar.system'].create({'name': 'Shared empty', 'code': 'SHARED-EMPTY'})
        self.assert_shared_series('24h', empty.id)
        self.charger(); self.env.flush_all()
        shared = self.original.with_context(smartsolar_dashboard_end_at=self.now, smartsolar_chart_rows={})
        legacy = shared.with_context(smartsolar_chart_rows=None)
        for name in ('charge_power', 'grid_tie', 'pv_efficiency'):
            args = {'device_ids': [self.mppt.id]}
            if name == 'pv_efficiency':args['balance'] = {'labels': [], 'mppt_output_power': []}
            self.assertEqual(getattr(shared, 'get_'+name+'_series')('24h', self.system.id, **args),
                             getattr(legacy, 'get_'+name+'_series')('24h', self.system.id, **args))
        self.assertEqual(shared.env.context['smartsolar_chart_rows'], {})
