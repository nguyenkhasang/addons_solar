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
from odoo.addons.smartsolar_ai.domain.enums import MetricKind


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

    def test_aggregate_empty_range_returns_zeroed(self):
        env = self.reg.execute('get_aggregate',
                               {'metrics': ['output_power'],
                                'start': '2020-01-01T00:00:00',
                                'end': '2020-01-02T00:00:00'})
        self.assertTrue(env['ok'])
        self.assertIn('output_power', env['data']['metrics'])

    def test_device_status_tool(self):
        env = self.reg.execute('get_device_status', {})
        self.assertTrue(env['ok'])
        self.assertIn('devices', env['data'])

    def test_openai_specs_shape(self):
        from odoo.addons.smartsolar_ai.adapters.openai_adapter import OpenAIAdapter
        specs = OpenAIAdapter(self.reg).tool_specs()
        self.assertTrue(all(s['type'] == 'function' for s in specs))
        self.assertEqual(len(specs), len(self.reg.names()))
