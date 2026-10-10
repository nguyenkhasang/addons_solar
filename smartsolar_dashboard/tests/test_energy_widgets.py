"""Pure aggregation tests: no database or live readings are required."""
from datetime import datetime
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from odoo import fields
from ..models.smartsolar_dashboard import SmartSolarDashboard


class TestEnergyWidgets(TestCase):
    def daily(self):
        return {
            'labels': ['2026-10-01', '2026-10-02', '2026-10-03'],
            'inverter_kwh': [2, 5, 3], 'grid_kwh': [8, 1, 4],
            'total_kwh': [10, 6, 7], 'estimated': [False, True, False],
            'notes': ['counter', 'power', 'counter'], 'coverage_pct': [100, 80, 100],
        }

    def test_month_totals_independent_peak_days_and_costs(self):
        result = SmartSolarDashboard._build_energy_widgets(self.daily(), [4, 6, 20], 3000)
        grid, inverter, load, pv = result['metrics']
        self.assertEqual([m['month'] for m in result['metrics']], [13, 10, 23, 30])
        self.assertEqual([m['today'] for m in result['metrics']], [4, 3, 7, 20])
        self.assertEqual([m['peak_date'] for m in result['metrics']],
                         ['2026-10-01', '2026-10-02', '2026-10-01', '2026-10-03'])
        self.assertEqual(result['saved'], 30000)  # inverter supply, not PV input
        self.assertEqual(result['payable'], 39000)
        self.assertTrue(result['estimated'])

    def test_missing_days_remain_missing_and_partial_month_is_explicit(self):
        daily = self.daily()
        daily['grid_kwh'] = daily['inverter_kwh'] = daily['total_kwh'] = [None, 0, None]
        result = SmartSolarDashboard._build_energy_widgets(daily, [None, None, None], 3000)
        self.assertIsNone(result['metrics'][0]['today'])
        self.assertEqual(result['metrics'][0]['month'], 0)
        self.assertEqual(result['metrics'][0]['days_available'], 1)
        self.assertEqual(result['days_in_period'], 3)
        self.assertIsNone(result['metrics'][3]['month'])
        self.assertIsNone(result['metrics'][3]['peak_date'])
        self.assertEqual(result['payable'], 0)
        empty = self.daily()
        for key in ('grid_kwh', 'inverter_kwh', 'total_kwh'):
            empty[key] = [None] * 3
        self.assertIsNone(SmartSolarDashboard._build_energy_widgets(empty, [None]*3, 3000)['payable'])

    def test_midnight_does_not_show_yesterdays_energy_as_today(self):
        result = SmartSolarDashboard._build_energy_widgets(self.daily(), [4, 6, 20], 3000, '2026-10-04')
        self.assertTrue(all(m['today'] is None for m in result['metrics']))

    def test_this_month_uses_calendar_boundary_in_system_timezone(self):
        for tz, expected in [('Asia/Ho_Chi_Minh', datetime(2026, 9, 30, 17)),
                             ('UTC', datetime(2026, 10, 1))]:
            fake = SimpleNamespace(_get_system_timezone=lambda sid: tz)
            with patch.object(fields.Datetime, 'now', return_value=datetime(2026, 10, 8, 8)):
                cfg, start, end = SmartSolarDashboard._resolve_time_range(fake, 'this_month', 12)
            self.assertEqual(start, expected)
            self.assertEqual(end, datetime(2026, 10, 8, 8))
            self.assertEqual(cfg['delta'], (end-start).total_seconds())

    def test_comparison_aligns_pv_days_without_adding_pv_to_consumption(self):
        start, end = datetime(2026, 10, 6, 17), datetime(2026, 10, 8, 17)
        fake = SimpleNamespace(
            env=SimpleNamespace(flush_all=lambda: None),
            _resolve_time_range=lambda *args: ({'delta': 172800}, start, end),
            _get_system_timezone=lambda *args: 'Asia/Ho_Chi_Minh',
            _energy_distribution_for_range=lambda *args: {
                'data': [2, 1], 'available': True, 'estimated': False},
            _get_daily_pv_energy=lambda *args: {'2026-10-07': 4.5})
        result = SmartSolarDashboard.get_energy_comparison(fake, '1week', 12)
        self.assertEqual(result['labels'], ['2026-10-07', '2026-10-08'])
        self.assertEqual(result['pv_kwh'], [4.5, None])
        self.assertEqual(result['total_kwh'], [3, 3])
        self.assertEqual(result['energy_kwh'], result['inverter_kwh'])
