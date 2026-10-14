"""Real-ORM proof for retained days, raw fallbacks, request reuse and peaks."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged
from odoo.addons.smartsolar.models.summary_math import calculate
from ..models.smartsolar_dashboard import TIME_RANGE_CONFIG


@tagged('post_install', '-at_install', 'smartsolar_dashboard')
class TestKpiOptimization(TransactionCase):
    def setUp(self):
        super().setUp()
        self.system = self.env['smartsolar.system'].create({
            'name': 'KPI optimization', 'code': 'KPI-OPT', 'timezone': 'Asia/Ho_Chi_Minh'})
        self.device = self.env['smartsolar.device'].create({
            'name': 'KPI inverter', 'device_guid': 'KPI-OPT-GTI',
            'device_type': 'grid_tie_inverter', 'system_id': self.system.id})
        self.dashboard = self.env['smartsolar.dashboard']
        self.start = datetime(2026, 10, 6, 17)
        self.end = self.start + timedelta(days=1)

    def raw(self, offset, output=600, grid=60, counter=None):
        values = {'device_id': self.device.id, 'system_id': self.system.id,
                  'device_guid': self.device.device_guid,
                  'record_date': self.start + timedelta(seconds=offset),
                  'output_power': output, 'limiter_power': grid,
                  'energy_total': 2, 'limiter_total': 1,
                  'counter_validity': {'energy_total': True, 'limiter_total': True}}
        if counter is not None:
            values.update(limiter_total=counter[0], energy_total=counter[1])
        return self.env['grid.tie.inverter'].create(values)

    def summary(self, raw):
        self.env.flush_all()
        self.cr.execute('SELECT * FROM grid_tie_inverter WHERE device_id=%s ORDER BY record_date,id',
                        [self.device.id])
        result = calculate(self.cr.dictfetchall(), 'grid_tie_inverter', self.start, self.end)
        return self.env['grid.tie.inverter.summary'].create({
            'device_id': self.device.id, 'system_id': self.system.id,
            'bucket_type': 'day', 'bucket_start': self.start, 'quality_version': 4,
            'sample_count': len(raw.filtered(lambda r: self.start <= r.record_date < self.end)),
            'power_energy': result['power_energy'], 'metric_statistics': result['metric_statistics'],
            'counter_quality': result['counter_quality'],
            'limiter_energy_kwh': result['counters']['limiter_total']['energy'],
            'energy_kwh': result['counters']['energy_total']['energy']})

    def comparison(self, start=None, end=None, cache=None):
        with patch.object(type(self.dashboard), '_resolve_time_range', return_value=(
                TIME_RANGE_CONFIG['1week'], start or self.start, end or self.end)):
            return self.dashboard.get_energy_comparison('1week', self.system.id, cache)

    def test_completed_day_summary_equals_raw_including_midnight_segments(self):
        raw = self.raw(-10) | self.raw(10) | self.raw(60) | self.raw(86410)
        before = self.comparison()
        self.summary(raw)
        with patch.object(type(self.dashboard), '_energy_distribution_for_range',
                          side_effect=AssertionError('Complete day should use summary')):
            after = self.comparison()
        for key in ('inverter_kwh', 'grid_kwh', 'total_kwh', 'estimated', 'coverage_pct'):
            self.assertEqual(after[key], before[key], key)

    def test_purged_raw_preserves_valid_summary_and_bill(self):
        raw = self.raw(0) | self.raw(60)
        before = self.comparison()
        self.summary(raw)
        raw.unlink()
        after = self.comparison()
        for key in ('inverter_kwh', 'grid_kwh', 'total_kwh', 'estimated', 'coverage_pct'):
            self.assertEqual(after[key], before[key], key)
        for daily in (before, after):
            daily['pv_kwh'] = [None]
        old_bill = self.dashboard._build_energy_widgets(before, [None], 3000)
        new_bill = self.dashboard._build_energy_widgets(after, [None], 3000)
        self.assertEqual(old_bill, new_bill)

    def test_partial_day_uses_raw_not_full_day_summary(self):
        raw = self.raw(0) | self.raw(60) | self.raw(120)
        self.summary(raw)
        with patch.object(type(self.dashboard), '_energy_distribution_for_range',
                          wraps=self.dashboard._energy_distribution_for_range) as fn:
            result = self.comparison(end=self.start+timedelta(seconds=60))
        self.assertEqual(fn.call_count, 1)
        self.assertEqual(result['total_kwh'], [0.011])

    def test_stale_summary_after_late_sample_falls_back_to_raw(self):
        raw = self.raw(0) | self.raw(60)
        self.summary(raw)
        self.raw(120)
        with patch.object(type(self.dashboard), '_energy_distribution_for_range',
                          wraps=self.dashboard._energy_distribution_for_range) as fn:
            result = self.comparison()
        self.assertEqual(fn.call_count, 1)
        self.assertEqual(result['total_kwh'], [0.022])

    def test_late_midnight_neighbor_invalidates_boundary_integral(self):
        raw = self.raw(86340) | self.raw(86390)
        row = self.summary(raw)
        late = self.raw(86410, output=1200)
        self.env.flush_all()
        self.cr.execute('UPDATE grid_tie_inverter SET write_date=%s WHERE id=%s',
                        [row.write_date+timedelta(seconds=1), late.id])
        late.invalidate_recordset(['write_date'])
        with patch.object(type(self.dashboard), '_energy_distribution_for_range',
                          wraps=self.dashboard._energy_distribution_for_range) as fn:
            self.comparison()
        self.assertEqual(fn.call_count, 1)

    def test_new_device_missing_from_summary_falls_back_to_raw(self):
        raw = self.raw(0) | self.raw(60)
        self.summary(raw)
        other = self.device.copy({'device_guid': 'KPI-OTHER'})
        (self.raw(0) | self.raw(60)).write({'device_id': other.id})
        with patch.object(type(self.dashboard), '_energy_distribution_for_range',
                          wraps=self.dashboard._energy_distribution_for_range) as fn:
            result = self.comparison()
        self.assertEqual(fn.call_count, 1)
        self.assertEqual(result['total_kwh'], [0.022])

    def test_summary_coverage_accounts_for_expected_device_without_data(self):
        raw = self.raw(0) | self.raw(60)
        before = self.comparison()
        self.summary(raw)
        self.device.copy({'device_guid': 'KPI-MISSING'})
        after = self.comparison()
        self.assertEqual(after['total_kwh'], before['total_kwh'])
        self.assertEqual(after['coverage_pct'], [0.03])

    def test_unknown_summary_metadata_falls_back_to_raw(self):
        raw = self.raw(0) | self.raw(60)
        row = self.summary(raw)
        row.quality_version = 0
        with patch.object(type(self.dashboard), '_energy_distribution_for_range',
                          wraps=self.dashboard._energy_distribution_for_range) as fn:
            self.comparison()
        self.assertEqual(fn.call_count, 1)

    def test_long_range_keeps_missing_days_without_per_day_raw_queries(self):
        end = self.start + timedelta(days=60)
        with patch.object(type(self.dashboard), '_resolve_time_range', return_value=(
                TIME_RANGE_CONFIG['3month'], self.start, end)), \
                patch.object(type(self.dashboard), '_energy_distribution_for_range',
                             side_effect=AssertionError('Empty full days need no raw query')):
            result = self.dashboard.get_energy_comparison('3month', self.system.id)
        self.assertEqual(len(result['labels']), 60)
        self.assertEqual(result['total_kwh'], [None]*60)

    def test_negative_or_unpaired_power_metadata_falls_back(self):
        raw = self.raw(0, output=-100) | self.raw(60)
        row = self.summary(raw)
        self.assertFalse(self.dashboard._can_use_daily_summaries(row, {self.device.id: 2}))
        row.metric_statistics = {k: {'count': 2, 'min': 0}
                                 for k in ('output_power', 'grid_import_power', 'total_load_power')}
        row.power_energy = {k: {'value': 1, 'covered_seconds': value} for k, value in (
            ('output_power', 60), ('grid_import_power', 30), ('total_load_power', 30))}
        self.assertFalse(self.dashboard._can_use_daily_summaries(row, {self.device.id: 2}))

    def test_request_cache_reuses_same_day_but_never_other_system_or_cutoff(self):
        self.raw(0) | self.raw(60)
        cache = {}
        with patch.object(type(self.dashboard), '_energy_distribution_for_range',
                          wraps=self.dashboard._energy_distribution_for_range) as fn:
            self.assertEqual(self.comparison(cache=cache), self.comparison(cache=cache))
            self.assertEqual(fn.call_count, 1)
            self.comparison(end=self.start+timedelta(seconds=60), cache=cache)
            self.assertEqual(fn.call_count, 2)
            self.comparison(cache={})
            self.assertEqual(fn.call_count, 3)
            other = self.system.copy({'code': 'KPI-CACHE-OTHER'})
            with patch.object(type(self.dashboard), '_resolve_time_range', return_value=(
                    TIME_RANGE_CONFIG['1week'], self.start, self.end)):
                self.dashboard.get_energy_comparison('1week', other.id, cache)
            self.assertEqual(fn.call_count, 4)

    def test_request_cutoff_keeps_previous_month_when_clock_crosses_midnight(self):
        cutoff = datetime(2026, 10, 31, 16, 59, 59)
        with patch.object(fields.Datetime, 'now', return_value=datetime(2026, 10, 31, 17)), \
                patch.object(type(self.dashboard), '_energy_distribution_for_range',
                             return_value={'available': False, 'data': [None, None]}):
            result = self.dashboard.get_energy_comparison('this_month', self.system.id, end_at=cutoff)
        self.assertEqual(result['labels'][0], '2026-10-01')
        self.assertEqual(result['labels'][-1], '2026-10-31')
        self.assertEqual(len(result['labels']), 31)

    def test_counter_reset_day_matches_raw(self):
        raw = self.raw(-10, counter=(1, 2))
        # One reset with both deltas matching power over the following day.
        raw |= self.raw(0, counter=(0.01, 0.001))
        raw |= self.raw(60, counter=(0.02, 0.002))
        before = self.comparison()
        row = self.summary(raw)
        self.assertEqual(row.counter_quality['energy_total']['reset_count'], 1)
        after = self.comparison()
        self.assertEqual(before['inverter_kwh'], after['inverter_kwh'])
        self.assertEqual(before['grid_kwh'], after['grid_kwh'])

    def test_summary_rejects_counter_power_scale_mismatch_and_mixed_devices(self):
        row = SimpleNamespace(quality_version=4, limiter_energy_kwh=100, energy_kwh=50,
            counter_quality={k: {'reliable': True} for k in ('limiter_total', 'energy_total')},
            power_energy={'output_power': {'value': 1, 'covered_seconds': 86400},
                          'grid_import_power': {'value': 0.1, 'covered_seconds': 86400}})
        flow = self.dashboard._daily_summary_distribution([row], 86400)
        self.assertTrue(flow['estimated'])
        self.assertEqual(flow['data'], [1, 0.1])
        other = SimpleNamespace(**vars(row))
        other.counter_quality = {}
        # Rejecting any counter rejects all devices' counters, as on the raw path.
        self.assertEqual(self.dashboard._daily_summary_distribution([row, other], 86400)['data'], [2, 0.2])

    def test_peak_sweep_duplicate_null_exact_freshness_and_earliest_tie(self):
        peak = self.dashboard._peak_from_observations
        at = self.start
        rows = [(1, at, 100), (1, at, 200), (2, at+timedelta(seconds=10), 300),
                (1, at+timedelta(seconds=20), None), (1, at+timedelta(seconds=30), 200),
                (2, at+timedelta(seconds=30), 300)]
        self.assertEqual(peak(rows, 2), (at+timedelta(seconds=10), 500))
        self.assertEqual(peak([(1, at, 100), (2, at+timedelta(seconds=11), 300)], 2), (None, None))
        self.assertEqual(peak([(1, at, 0)], 1), (at, 0))
