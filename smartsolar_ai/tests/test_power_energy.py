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
