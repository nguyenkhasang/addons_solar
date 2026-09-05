# -*- coding: utf-8 -*-
"""Unit test cho tầng AI Tool.

- Test tầng domain: chạy được KHÔNG cần DB (logic thuần).
- Test tầng service/tool: dùng TransactionCase của Odoo (cần env + DB tạm).
Chạy: odoo --test-enable --test-tags smartsolar_ai -i smartsolar_ai
"""
from datetime import datetime

from odoo.tests import TransactionCase, tagged

from odoo.addons.smartsolar_ai.domain.value_objects import TimeRange, UTC7
from odoo.addons.smartsolar_ai.domain.metric_registry import MetricRegistry
from odoo.addons.smartsolar_ai.domain.enums import (
    AggregationType, Granularity, MetricKind,
)
from odoo.addons.smartsolar_ai.repositories.metric_repository import MetricRepository
from odoo.addons.smartsolar_ai.services.analytics_service import AnalyticsService
from odoo.addons.smartsolar_ai.services.forecast_service import ForecastService
from odoo.addons.smartsolar_ai.services.health_service import HealthService


@tagged('post_install', '-at_install', 'smartsolar_ai')
class TestDomain(TransactionCase):

    def test_timerange_parses_local_to_utc(self):
        tr = TimeRange.from_iso('2026-07-02T07:00:00', '2026-07-02T08:00:00')
        # 07:00 UTC+7 -> 00:00 UTC
        self.assertEqual(tr.start_utc, datetime(2026, 7, 2, 0, 0, 0))
        self.assertAlmostEqual(tr.days, 1 / 24.0, places=4)

    def test_timerange_rejects_inverted(self):
        with self.assertRaises(ValueError):
            TimeRange.from_iso('2026-07-02T08:00:00', '2026-07-02T07:00:00')

    def test_timerange_iso_with_offset_not_double_shifted(self):
        # Chuỗi kèm offset phải được TÔN TRỌNG (không cộng/trừ thêm lần nữa).
        # 00:00+07:00 = 17:00 UTC hôm trước; 00:00Z = 00:00 UTC.
        self.assertEqual(
            TimeRange.from_iso('2026-07-02T00:00:00+07:00',
                               '2026-07-03T00:00:00+07:00').start_utc,
            datetime(2026, 7, 1, 17, 0, 0))
        self.assertEqual(
            TimeRange.from_iso('2026-07-02T00:00:00Z',
                               '2026-07-03T00:00:00Z').start_utc,
            datetime(2026, 7, 2, 0, 0, 0))

    def test_timerange_relative_now_delta(self):
        # 'now-2h' -> 'now' luôn dài đúng 2 giờ, bất kể chạy lúc nào.
        from datetime import timedelta
        tr = TimeRange.from_iso('now-2h', 'now')
        self.assertAlmostEqual(tr.duration, timedelta(hours=2),
                               delta=timedelta(seconds=5))

    def test_timerange_relative_day_tokens(self):
        # 'today' -> 'tomorrow' là trọn 1 ngày; 'yesterday' -> 'today' cũng vậy.
        from datetime import timedelta
        self.assertEqual(TimeRange.from_iso('today', 'tomorrow').duration,
                         timedelta(days=1))
        self.assertEqual(TimeRange.from_iso('yesterday', 'today').duration,
                         timedelta(days=1))

    def test_timerange_relative_bad_token_raises(self):
        with self.assertRaises(ValueError):
            TimeRange.from_iso('now-2x', 'now')

    def test_timerange_relative_composite_year_token(self):
        from datetime import timedelta
        tr = TimeRange.from_iso('now-1y-3d', 'now-1y')
        self.assertAlmostEqual(tr.duration, timedelta(days=3),
                               delta=timedelta(seconds=5))

    def test_registry_has_core_metrics(self):
        self.assertTrue(MetricRegistry.exists('output_power'))
        self.assertTrue(MetricRegistry.exists('bat_voltage'))
        self.assertEqual(MetricRegistry.get('output_power').unit, 'W')

    def test_registry_unknown_metric_raises(self):
        with self.assertRaises(KeyError):
            MetricRegistry.get('does_not_exist')

    def test_derived_metric_flagged(self):
        spec = MetricRegistry.get('self_consumption_pct')
        self.assertEqual(spec.kind, MetricKind.DERIVED)
        self.assertTrue(spec.is_derived)

    def test_unsupported_metric_is_described_with_warning(self):
        metrics = {m['key']: m for m in MetricRegistry.describe()}
        grid_dependency = metrics['grid_dependency_pct']
        self.assertFalse(grid_dependency['supported'])
        self.assertTrue(grid_dependency['unreliable'])
        self.assertIn('Chưa có công-tơ', grid_dependency['note'])
        self.assertEqual(
            grid_dependency['depends_on'],
            ['grid_import_energy', 'load_energy'])
        self.assertIn('LẤY TỪ lưới', metrics['grid_import_power']['flow'])

    def test_instantaneous_sum_resolves_to_avg(self):
        repo = MetricRepository(None)
        resolved = repo.resolve_aggregation(
            MetricRegistry.get('output_power'), AggregationType.SUM)
        self.assertEqual(resolved, AggregationType.AVG)

    def test_first_last_aggregation_does_not_fall_back_to_avg(self):
        last_expr = MetricRepository._aggregation_expr(
            'energy_total_end', AggregationType.LAST, 'bucket_start')
        first_expr = MetricRepository._aggregation_expr(
            'energy_total_end', AggregationType.FIRST, 'bucket_start')
        self.assertIn('ORDER BY bucket_start DESC', last_expr)
        self.assertIn('ORDER BY bucket_start ASC', first_expr)
        self.assertNotIn('AVG', last_expr)

    def test_counter_summary_last_sums_each_device(self):
        class FakeCursor:
            sql = ''
            params = []

            def execute(self, sql, params):
                self.sql = sql
                self.params = params

            @staticmethod
            def fetchall():
                return []

        class FakeModel:
            _table = 'charge_power_summary'

        class FakeEnv:
            cr = FakeCursor()

            @staticmethod
            def __getitem__(key):
                return FakeModel()

        class FakeRange:
            start_utc = 'start'
            end_utc = 'end'

        repo = MetricRepository(FakeEnv())
        repo._fetch_series_summary(
            MetricRegistry.get('pv_energy_total'), FakeRange(),
            AggregationType.LAST, Granularity.DAY, None, 1)
        sql = ' '.join(repo.env.cr.sql.split())
        self.assertIn('SUM(val)', sql)
        self.assertIn('GROUP BY bucket, device_id', sql)
        self.assertIn('ORDER BY bucket_start DESC', sql)

    def test_counter_scalar_sums_first_last_per_device(self):
        class FakeCursor:
            sql = ''
            params = []

            def execute(self, sql, params):
                self.sql = sql
                self.params = params

            @staticmethod
            def fetchone():
                # avg, min, max, sum, sum(last/device), sum(first/device), count
                return (15.0, 10.0, 20.0, 60.0, 220.0, 200.0, 4)

        class FakeModel:
            _table = 'charger_reading'

        class FakeEnv:
            cr = FakeCursor()

            @staticmethod
            def __getitem__(name):
                return FakeModel()

        repo = MetricRepository(FakeEnv())
        tr = TimeRange.from_iso('2026-07-01T00:00:00',
                                '2026-07-02T00:00:00')
        result = repo.fetch_scalar(MetricRegistry.get('pv_energy_total'), tr)
        sql = ' '.join(FakeEnv.cr.sql.split())
        self.assertIn('GROUP BY device_id', sql)
        self.assertIn('SUM(last_v)', sql)
        self.assertIn('SUM(first_v)', sql)
        self.assertEqual(result['last'] - result['first'], 20.0)

    def test_timeseries_downsample_caps_points_and_keeps_edges(self):
        points = list(range(1000))
        sampled = AnalyticsService._downsample(points, 240)
        self.assertEqual(len(sampled), 240)
        self.assertEqual(sampled[0], 0)
        self.assertEqual(sampled[-1], 999)

    def test_health_without_coverage_has_no_score(self):
        class EmptyMetricRepo:
            @staticmethod
            def fetch_scalar(*args, **kwargs):
                return {'avg': 0.0, 'min': 0.0, 'max': 0.0, 'sum': 0.0,
                        'last': 0.0, 'first': 0.0, 'count': 0}

        class EmptyDeviceRepo:
            @staticmethod
            def fetch_devices(*args, **kwargs):
                return []

        svc = HealthService.__new__(HealthService)
        svc._metric_repo = EmptyMetricRepo()
        svc._device_repo = EmptyDeviceRepo()
        result = svc.get_health_score(
            TimeRange.from_iso('2020-01-01T00:00:00', '2020-01-02T00:00:00'))
        self.assertFalse(result.available)
        self.assertIsNone(result.score)
        self.assertEqual(result.coverage_pct, 0.0)

    def test_health_daylight_overlap_uses_vietnam_time(self):
        night = TimeRange.from_iso('2026-07-02T20:00:00',
                                   '2026-07-02T22:00:00')
        dawn = TimeRange.from_iso('2026-07-02T04:30:00',
                                  '2026-07-02T05:30:00')
        self.assertFalse(HealthService._overlaps_daylight(night))
        self.assertTrue(HealthService._overlaps_daylight(dawn))

    def test_forecast_missing_history_has_no_zero_points(self):
        class EmptySeries:
            available = False
            reason = 'Không có lịch sử'
            points = []

        class EmptyAnalytics:
            @staticmethod
            def get_timeseries(*args, **kwargs):
                return EmptySeries()

        svc = ForecastService.__new__(ForecastService)
        svc._analytics = EmptyAnalytics()
        result = svc.forecast('output_power', 6, lookback_days=7)
        self.assertFalse(result.available)
        self.assertEqual(result.points, [])
        self.assertIsNone(result.confidence_pct)


@tagged('post_install', '-at_install', 'smartsolar_ai')
class TestToolLayer(TransactionCase):

    def setUp(self):
        super().setUp()
        from odoo.addons.smartsolar_ai.tools.registry import ToolRegistry
        self.reg = ToolRegistry(self.env)

    def test_list_metrics_tool(self):
        env = self.reg.execute('list_metrics', {})
        self.assertTrue(env['ok'])
        self.assertIn('metrics', env['data'])
        self.assertGreater(len(env['data']['metrics']), 0)
        metrics = {m['key']: m for m in env['data']['metrics']}
        self.assertFalse(metrics['grid_dependency_pct']['supported'])
        self.assertTrue(metrics['grid_dependency_pct']['note'])

    def test_unknown_tool_returns_error_envelope(self):
        env = self.reg.execute('nope', {})
        self.assertFalse(env['ok'])
        self.assertEqual(env['error']['code'], 'unknown_tool')

    def test_timeseries_missing_metric_is_bad_request(self):
        env = self.reg.execute('get_timeseries',
                               {'start': '2026-07-01T00:00:00',
                                'end': '2026-07-02T00:00:00'})
        self.assertFalse(env['ok'])
        self.assertEqual(env['error']['code'], 'bad_request')

    def test_timeseries_unknown_metric_is_unknown_metric(self):
        env = self.reg.execute('get_timeseries',
                               {'metric': 'ghost',
                                'start': '2026-07-01T00:00:00',
                                'end': '2026-07-02T00:00:00'})
        self.assertFalse(env['ok'])
        self.assertEqual(env['error']['code'], 'unknown_metric')

    def test_aggregate_empty_range_returns_unavailable(self):
        env = self.reg.execute('get_aggregate',
                               {'metrics': ['output_power'],
                                'start': '2020-01-01T00:00:00',
                                'end': '2020-01-02T00:00:00'})
        self.assertTrue(env['ok'])
        self.assertIn('output_power', env['data']['metrics'])
        metric = env['data']['metrics']['output_power']
        self.assertFalse(metric['available'])
        self.assertIsNone(metric['avg'])
        self.assertIsNone(metric['last'])

    def test_unsupported_derived_metric_returns_no_number(self):
        env = self.reg.execute(
            'get_aggregate',
            {'metrics': ['grid_dependency_pct'],
             'start': '2026-07-01T00:00:00',
             'end': '2026-07-02T00:00:00'})
        self.assertTrue(env['ok'])
        metric = env['data']['metrics']['grid_dependency_pct']
        self.assertFalse(metric['available'])
        self.assertIsNone(metric['value'])
        self.assertIn('Chưa có công-tơ', metric['reason'])

    def test_unsupported_derived_timeseries_has_no_fake_point(self):
        env = self.reg.execute(
            'get_timeseries',
            {'metric': 'grid_dependency_pct',
             'start': '2026-07-01T00:00:00',
             'end': '2026-07-02T00:00:00'})
        self.assertTrue(env['ok'])
        self.assertFalse(env['data']['available'])
        self.assertEqual(env['data']['count'], 0)
        self.assertEqual(env['data']['points'], [])

    def test_missing_derived_inputs_return_unavailable(self):
        env = self.reg.execute(
            'get_aggregate',
            {'metrics': ['self_consumption_pct'],
             'start': '2020-01-01T00:00:00',
             'end': '2020-01-02T00:00:00'})
        self.assertTrue(env['ok'])
        metric = env['data']['metrics']['self_consumption_pct']
        self.assertFalse(metric['available'])
        self.assertIsNone(metric['value'])
        self.assertTrue(metric['missing_inputs'])

    def test_aggregation_description_uses_counter_safe_guidance(self):
        params = self.reg.get('get_timeseries').parameters()
        description = params['properties']['aggregation']['description']
        self.assertIn('get_aggregate', description)
        self.assertIn('Không dùng sum', description)
        self.assertIn('auto (nên dùng)',
                      params['properties']['interval']['description'])

    def test_counter_timeseries_rejects_sum_aggregation(self):
        env = self.reg.execute(
            'get_timeseries',
            {'metric': 'pv_energy_total',
             'start': '2026-07-01T00:00:00',
             'end': '2026-07-02T00:00:00',
             'aggregation': 'sum'})
        self.assertFalse(env['ok'])
        self.assertEqual(env['error']['code'], 'bad_request')
        self.assertIn('get_aggregate', env['error']['message'])

    def test_timeseries_rejects_unknown_parameter(self):
        env = self.reg.execute(
            'get_timeseries',
            {'metric': 'output_power', 'start': 'today', 'end': 'tomorrow',
             'unexpected': True})
        self.assertFalse(env['ok'])
        self.assertEqual(env['error']['code'], 'bad_request')

    def test_tool_specs_disallow_additional_properties(self):
        specs = self.reg.specs()
        self.assertTrue(all(
            spec['parameters']['additionalProperties'] is False for spec in specs))
        timeseries = next(s for s in specs if s['name'] == 'get_timeseries')
        max_points = timeseries['parameters']['properties']['max_points']
        self.assertEqual(max_points['maximum'], 500)

    def test_forecast_rejects_counter_metric(self):
        env = self.reg.execute(
            'forecast', {'metric': 'pv_energy_total', 'horizon_hours': 6})
        self.assertFalse(env['ok'])
        self.assertEqual(env['error']['code'], 'bad_request')
        self.assertIn('tức thời', env['error']['message'])

    def test_anomaly_empty_range_is_unavailable_not_clean(self):
        env = self.reg.execute(
            'find_anomalies',
            {'metric': 'output_power', 'start': '2020-01-01T00:00:00',
             'end': '2020-01-02T00:00:00'})
        self.assertTrue(env['ok'])
        self.assertFalse(env['data']['available'])
        self.assertEqual(env['data']['event_count'], 0)
        self.assertEqual(env['data']['sample_count'], 0)

    def test_device_status_tool(self):
        env = self.reg.execute('get_device_status', {})
        self.assertTrue(env['ok'])
        self.assertIn('devices', env['data'])

    def test_openai_specs_shape(self):
        from odoo.addons.smartsolar_ai.adapters.openai_adapter import OpenAIAdapter
        specs = OpenAIAdapter(self.reg).tool_specs()
        self.assertTrue(all(s['type'] == 'function' for s in specs))
        self.assertEqual(len(specs), len(self.reg.names()))
