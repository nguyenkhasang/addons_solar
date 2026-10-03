from datetime import datetime, timedelta

from odoo.tests import TransactionCase, tagged
from odoo.addons.smartsolar_ai.services.analytics_service import AnalyticsService
from odoo.addons.smartsolar_ai.domain.value_objects import TimeRange
from odoo.addons.smartsolar_ai.domain.metric_registry import MetricRegistry


@tagged('post_install', '-at_install', 'smartsolar_ai')
class TestPowerEnergy(TransactionCase):
    def setUp(self):
        super().setUp()
        self.system = self.env['smartsolar.system'].create({
            'name': 'Power energy test', 'code': 'power_energy_test', 'capacity': 2})
        self.device = self.env['smartsolar.device'].create({
            'device_guid': 'power_energy_gti', 'system_id': self.system.id,
            'device_type': 'grid_tie_inverter'})
        self.start = datetime(2026, 7, 2)
        self.service = AnalyticsService(self.env)

    def samples(self, offsets, device=None):
        device = device or self.device
        for offset in offsets:
            self.env['grid.tie.inverter'].create({
                'device_id': device.id, 'system_id': self.system.id,
                'device_guid': device.device_guid,
                'record_date': self.start + timedelta(seconds=offset),
                'output_power': 1000, 'limiter_power': 500,
                'energy_total': 10, 'limiter_total': 20})
        self.env.flush_all()

    def aggregate(self, start=0, end=120):
        return self.service.get_aggregate(
            ['total_load_energy', 'output_power', 'grid_import_energy_total'],
            TimeRange(self.start + timedelta(seconds=start),
                      self.start + timedelta(seconds=end)),
            system_id=self.system.id).metrics

    def test_constant_counter_uses_integrated_power(self):
        self.samples([0, 60, 120])
        data = self.aggregate()
        self.assertEqual(data['grid_import_energy_total']['energy'], 0)
        self.assertAlmostEqual(data['total_load_energy']['value'], 0.05)
        self.assertEqual(data['total_load_energy']['coverage_pct'], 100)
        self.assertTrue(data['total_load_energy']['estimated'])
        self.assertAlmostEqual(data['output_power']['energy_estimate']['value'], 0.0333)

    def test_clips_boundaries_and_sums_devices(self):
        self.samples([0, 60, 120])
        device = self.env['smartsolar.device'].create({
            'device_guid': 'power_energy_second', 'system_id': self.system.id,
            'device_type': 'grid_tie_inverter'})
        self.samples([0, 60, 120], device)
        data = self.aggregate(30, 90)['total_load_energy']
        self.assertAlmostEqual(data['value'], 0.05)
        self.assertEqual(len(data['per_device']), 2)
        self.assertEqual(data['coverage_pct'], 100)

    def test_outage_is_excluded_not_extrapolated(self):
        self.samples([0, 60, 660, 720])
        data = self.aggregate(0, 720)['total_load_energy']
        self.assertAlmostEqual(data['value'], 0.05)
        self.assertAlmostEqual(data['coverage_pct'], 16.67)
        self.assertEqual(data['per_device'][0]['covered_seconds'], 120)

    def test_no_connected_samples_returns_unavailable_not_zero(self):
        self.samples([0, 600])
        data = self.aggregate(0, 600)['total_load_energy']
        self.assertFalse(data['available'])
        self.assertIsNone(data['value'])

    def test_linear_interpolation_and_scope(self):
        self.samples([0, 120])
        last = self.env['grid.tie.inverter'].search([
            ('device_id', '=', self.device.id)], order='record_date desc', limit=1)
        last.write({'output_power': 2000})
        self.env.flush_all()
        data = self.aggregate()['total_load_energy']
        self.assertAlmostEqual(data['value'], 0.0667)
        other = self.env['smartsolar.system'].create({
            'name': 'Other scope', 'code': 'power_energy_other', 'capacity': 2})
        result = self.service.get_aggregate(['total_load_energy'],
            TimeRange(self.start, self.start + timedelta(seconds=120)),
            system_id=other.id).metrics['total_load_energy']
        self.assertFalse(result['available'])

    def extrema(self, metric='total_load_power', device_id=None):
        return self.service.get_extrema(metric,
            TimeRange(self.start, self.start + timedelta(seconds=180)),
            device_id=device_id, system_id=self.system.id)

    def test_extrema_adds_branches_at_same_sample_not_separate_maxima(self):
        self.samples([0, 60, 120])
        rows = self.env['grid.tie.inverter'].search(
            [('device_id', '=', self.device.id)], order='record_date')
        for row, values in zip(rows, [(2000, 0), (0, 1900), (1000, 1800)]):
            row.write({'output_power': values[0], 'limiter_power': values[1]})
        self.env.flush_all()
        result = self.extrema()
        self.assertEqual(result['max']['value'], 2800)
        self.assertEqual(result['max']['observed_at'], '2026-07-02T07:02:00+07:00')
        self.assertEqual(result['max']['components'], {'output_power': 1000, 'grid_import_power': 1800})
        self.assertEqual(result['min']['value'], 1900)
        self.assertEqual(result['sample_count'], 3)
        self.assertNotIn('points', result)

    def test_extrema_ties_earliest_and_filters_device_scope(self):
        self.samples([0, 60, 120])
        result = self.extrema()
        self.assertEqual(result['max']['observed_at'], '2026-07-02T07:00:00+07:00')
        self.assertEqual(result['max']['device_ids'], [self.device.id])
        self.assertFalse(self.extrema(device_id=self.device.id + 999)['available'])

    def test_extrema_sums_only_synchronized_device_samples(self):
        self.samples([0, 60, 120])
        second = self.env['smartsolar.device'].create({
            'device_guid': 'peak_second', 'system_id': self.system.id,
            'device_type': 'grid_tie_inverter'})
        self.samples([0, 60, 120], second)
        result = self.extrema()
        self.assertEqual(result['max']['value'], 3000)
        self.assertEqual(result['observed_device_count'], 2)
        self.assertEqual(result['evaluated_observations'], 3)
        self.assertEqual(self.extrema(device_id=second.id)['max']['value'], 1500)

    def test_extrema_does_not_invent_peak_from_asynchronous_devices(self):
        self.samples([0, 60, 120])
        second = self.env['smartsolar.device'].create({
            'device_guid': 'peak_async', 'system_id': self.system.id,
            'device_type': 'grid_tie_inverter'})
        self.samples([30, 90, 150], second)
        result = self.extrema()
        self.assertFalse(result['available'])
        self.assertIsNone(result['max'])
        self.assertEqual(result['evaluated_observations'], 0)

    def test_extrema_keeps_peak_omitted_by_timeseries_downsampling(self):
        self.samples([0, 60, 120, 180])
        rows = self.env['grid.tie.inverter'].search(
            [('device_id', '=', self.device.id)], order='record_date')
        rows[1].write({'limiter_power': 9000})
        self.env.flush_all()
        from odoo.addons.smartsolar_ai.domain.enums import Granularity, AggregationType
        series = self.service._repo.fetch_series(
            MetricRegistry.get('total_load_power'),
            TimeRange(self.start, self.start + timedelta(seconds=240)),
            AggregationType.MAX, Granularity.RAW, system_id=self.system.id)
        reduced = self.service._downsample(series, 2)
        self.assertTrue(all(point.value < 10000 for point in reduced))
        self.assertEqual(self.extrema()['max']['value'], 10000)
        self.assertEqual(self.extrema()['max']['observed_at'], '2026-07-02T07:01:00+07:00')

    def test_extrema_rejects_energy_counters(self):
        with self.assertRaises(ValueError):
            self.extrema('grid_import_energy_total')

    def summarize(self, hours=1):
        from unittest.mock import patch
        from odoo import fields
        with patch.object(fields.Datetime, 'now', return_value=self.start + timedelta(hours=hours)):
            self.env['grid.tie.inverter.summary']._aggregate_hourly(hours)
        self.env.flush_all()
        return self.env['grid.tie.inverter.summary'].search([
            ('device_id', '=', self.device.id), ('bucket_type', '=', 'hour'),
            ('bucket_start', '>=', self.start)])

    def test_durable_estimate_and_peak_survive_raw_purge(self):
        self.samples([0, 60, 120])
        records = self.summarize()
        self.assertEqual(records.quality_version, 4)
        self.env['grid.tie.inverter'].search([('device_id', '=', self.device.id)]).unlink()
        self.env.flush_all()
        scope = TimeRange(self.start, self.start + timedelta(hours=1))
        energy = self.service._repo.fetch_power_energy('total_load_energy', scope, system_id=self.system.id)
        self.assertAlmostEqual(energy['value'], 0.05)
        self.assertAlmostEqual(energy['coverage_pct'], 3.33, places=2)
        self.assertEqual(energy['source'], 'summary')
        peak = self.service.get_extrema('total_load_power', scope, system_id=self.system.id)
        self.assertEqual(peak['max']['value'], 1500)
        self.assertEqual(peak['max']['observed_at'], '2026-07-02T07:00:00+07:00')
        result = self.service._repo.fetch_energy_result(
            MetricRegistry.get('grid_import_energy_total'), scope, system_id=self.system.id)
        self.assertTrue(result['counter_quality'][0]['stalled_with_power'])
        self.assertFalse(result['counter_quality'][0]['reliable'])

    def test_raw_missing_complete_hour_is_filled_without_double_count(self):
        self.samples([0, 60, 3600, 3660])
        rows = self.env['grid.tie.inverter'].search([('device_id', '=', self.device.id)], order='record_date')
        for row, value in zip(rows, [10, 11, 12, 13]):
            row.energy_total = value
        summary = self.summarize(2)
        summary.filtered(lambda row: row.bucket_start == self.start + timedelta(hours=1)).unlink()
        self.env.flush_all()
        result = self.service._repo.fetch_energy_result(MetricRegistry.get('grid_import_energy_total'),
            TimeRange(self.start, self.start + timedelta(hours=2)), system_id=self.system.id)
        self.assertAlmostEqual(result['value'], 3)
        self.assertEqual(result['source'], 'raw+summary')
        self.assertEqual(len(result['counter_quality']), 1)
        self.assertEqual(result['counter_quality'][0]['valid_samples'], 4)

    def test_hour_series_fills_missing_device_bucket(self):
        from odoo.addons.smartsolar_ai.domain.enums import Granularity, AggregationType
        self.samples([0, 60, 3600, 3660])
        records = self.summarize(2)
        records.filtered(lambda row: row.bucket_start == self.start + timedelta(hours=1)).unlink()
        second = self.env['smartsolar.device'].create({
            'device_guid': 'summary_missing_second', 'system_id': self.system.id,
            'device_type': 'grid_tie_inverter'})
        self.samples([0, 60], second)
        self.env['grid.tie.inverter'].search([('device_id', '=', second.id)]).write({'output_power': 2000})
        self.env.flush_all()
        points = self.service._repo.fetch_series(MetricRegistry.get('output_power'),
            TimeRange(self.start, self.start + timedelta(hours=2)),
            AggregationType.AVG, Granularity.HOUR, system_id=self.system.id)
        self.assertEqual([point.value for point in points], [1500, 1000])

    def test_partial_raw_does_not_overwrite_retained_full_summary(self):
        self.samples([0, 60, 120])
        record = self.summarize()
        old_power = record.power_energy
        self.env['grid.tie.inverter'].search([
            ('device_id', '=', self.device.id), ('record_date', '=', self.start)]).unlink()
        self.env.flush_all()
        rebuilt = self.summarize()
        self.assertEqual(rebuilt.sample_count, 3)
        self.assertEqual(rebuilt.power_energy, old_power)

    def test_explicit_zero_reset_and_missing_counter_use_same_policy(self):
        self.samples([0, 60, 120, 180])
        rows = self.env['grid.tie.inverter'].search([('device_id', '=', self.device.id)], order='record_date')
        for row, value, valid in zip(rows, [10, 0, 0, 2], [True, False, True, True]):
            row.write({'energy_total': value, 'counter_validity': {'energy_total': valid}})
        self.env.flush_all()
        result = self.service._repo.fetch_energy_result(MetricRegistry.get('grid_import_energy_total'),
            TimeRange(self.start, self.start + timedelta(seconds=240)), system_id=self.system.id)
        self.assertAlmostEqual(result['value'], 2)
        self.assertEqual(result['counter_quality'][0]['invalid_samples'], 1)
        self.assertEqual(result['counter_quality'][0]['reset_count'], 1)
        record = self.summarize()
        self.assertAlmostEqual(record.energy_kwh, 2)
        self.assertEqual(record.counter_reset_count, 1)
        self.assertEqual(record.counter_quality['energy_total']['invalid_samples'], 1)

    def test_daily_only_metadata_preserves_estimate_and_observation_time(self):
        from unittest.mock import patch
        from odoo import fields
        self.start = datetime(2026, 7, 1, 17)  # Vietnam local midnight.
        self.samples([0, 60, 120])
        self.summarize()
        with patch.object(fields.Datetime, 'now', return_value=self.start + timedelta(days=1)):
            self.env['grid.tie.inverter.summary']._aggregate_daily(2)
        self.env['grid.tie.inverter'].search([('device_id', '=', self.device.id)]).unlink()
        self.env['grid.tie.inverter.summary'].search([
            ('device_id', '=', self.device.id), ('bucket_type', '=', 'hour')]).unlink()
        self.env.flush_all()
        scope = TimeRange(self.start, self.start + timedelta(days=1))
        estimate = self.service._repo.fetch_power_energy('total_load_energy', scope, system_id=self.system.id)
        self.assertAlmostEqual(estimate['value'], 0.05)
        self.assertAlmostEqual(estimate['coverage_pct'], 0.14, places=2)
        self.assertEqual(self.service.get_extrema('total_load_power', scope, system_id=self.system.id)
                         ['max']['observed_at'], '2026-07-02T00:00:00+07:00')

    def test_legacy_summary_does_not_invent_estimate_zero(self):
        self.env['grid.tie.inverter.summary'].create({
            'bucket_start': self.start, 'bucket_type': 'hour',
            'device_id': self.device.id, 'system_id': self.system.id, 'sample_count': 60,
            'output_power_avg': 1000, 'energy_kwh': 0})
        self.env.flush_all()
        result = self.service._repo.fetch_power_energy('total_load_energy',
            TimeRange(self.start, self.start + timedelta(hours=1)), system_id=self.system.id)
        self.assertFalse(result['available'])
        self.assertIsNone(result['value'])

    def test_historical_device_peaks_are_not_added_as_system_peak(self):
        self.samples([0, 60])
        self.summarize()
        second = self.env['smartsolar.device'].create({
            'device_guid': 'summary_async_second', 'system_id': self.system.id,
            'device_type': 'grid_tie_inverter'})
        self.env['grid.tie.inverter.summary'].create({
            'bucket_start': self.start, 'bucket_type': 'hour', 'quality_version': 4,
            'device_id': second.id, 'system_id': self.system.id, 'sample_count': 2,
            'metric_extrema': {'total_load_power': {
                'max': {'value': 2000, 'observed_at': self.start.isoformat()},
                'min': {'value': 2000, 'observed_at': self.start.isoformat()}}}})
        self.env['grid.tie.inverter'].search([('device_id', '=', self.device.id)]).unlink()
        self.env.flush_all()
        result = self.service.get_extrema('total_load_power',
            TimeRange(self.start, self.start + timedelta(hours=1)), system_id=self.system.id)
        self.assertFalse(result['available'])
        self.assertIn('device_id', result['reason'])

    def test_historical_scalar_uses_sample_extrema_and_weights_across_sources(self):
        self.samples([0, 60, 3600, 3660])
        rows = self.env['grid.tie.inverter'].search([('device_id', '=', self.device.id)], order='record_date')
        for row, value in zip(rows, [100, 1900, 300, 3700]):
            row.output_power = value
        self.summarize(2)
        self.env['grid.tie.inverter'].search([
            ('device_id', '=', self.device.id), ('record_date', '<', self.start + timedelta(hours=1))]).unlink()
        self.env['grid.tie.inverter.summary'].search([
            ('device_id', '=', self.device.id), ('bucket_type', '=', 'hour'),
            ('bucket_start', '=', self.start + timedelta(hours=1))]).unlink()
        self.env.flush_all()
        data = self.service.get_aggregate(['output_power'],
            TimeRange(self.start, self.start + timedelta(hours=2)), system_id=self.system.id).metrics['output_power']
        self.assertEqual(data['min'], 100)
        self.assertEqual(data['max'], 3700)
        self.assertEqual(data['avg'], 1500)
        self.assertEqual(data['last'], 3700)
        self.assertEqual(data['count'], 4)

    def test_purged_preceding_seed_does_not_erase_hour_boundary_delta(self):
        self.samples([-60, 0, 60])
        rows = self.env['grid.tie.inverter'].search([('device_id', '=', self.device.id)], order='record_date')
        for row, value in zip(rows, [9, 10, 11]):
            row.energy_total = value
        record = self.summarize()
        self.assertEqual(record.energy_kwh, 2)
        old_power = record.power_energy
        rows[0].unlink()
        record = self.summarize()
        self.assertEqual(record.energy_kwh, 2)
        self.assertEqual(record.power_energy, old_power)

    def test_hour_series_for_raw_expression_never_opens_missing_summary_model(self):
        from odoo.addons.smartsolar_ai.domain.enums import Granularity, AggregationType
        self.samples([0, 60, 3600, 3660])
        points = self.service._repo.fetch_series(MetricRegistry.get('total_load_power'),
            TimeRange(self.start, self.start + timedelta(hours=2)),
            AggregationType.MAX, Granularity.HOUR, system_id=self.system.id)
        self.assertEqual([point.ts_utc for point in points], [self.start, self.start + timedelta(hours=1)])
        self.assertEqual([point.value for point in points], [1500, 1500])
