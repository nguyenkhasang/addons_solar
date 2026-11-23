"""The daily weekly chart uses seven calendar dates, not eight partial dates."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from odoo import fields
from ..models.smartsolar_dashboard import SmartSolarDashboard


class TestEnergyWeek(TestCase):
    def reader(self, tz='Asia/Ho_Chi_Minh', ledger=False):
        env = MagicMock()
        env.context = {'smartsolar_dashboard_read_ledger': ledger}
        env.cr.fetchall.return_value = []
        env.__getitem__.return_value.search.return_value.ids = []
        fake = SimpleNamespace(
            env=env, _get_system_timezone=lambda *args: tz,
            _can_use_daily_summaries=lambda *args: False,
            _energy_distribution_for_range=MagicMock(return_value={
                'available': True, 'data': [2, 1], 'estimated': False}),
            _get_daily_pv_energy=MagicMock(return_value={
                '2026-10-10': 6, '2026-10-16': 5}),
            _read_energy_ledger=MagicMock(return_value={'source': 'ledger'}))
        fake._resolve_time_range = lambda *args: SmartSolarDashboard._resolve_time_range(fake, *args)
        return fake

    def test_week_contains_seven_dates_and_full_first_day(self):
        end = datetime(2026, 10, 16, 6, 38)
        fake = self.reader()
        with patch.object(fields.Datetime, 'now', return_value=end):
            result = SmartSolarDashboard.get_energy_comparison(fake, '1week', 12, end_at=end)
        self.assertEqual(result['labels'], [f'2026-10-{day}' for day in range(10, 17)])
        start = datetime(2026, 10, 9, 17)  # Oct 10 00:00 local
        fake._get_daily_pv_energy.assert_called_once_with(start, end, 12)
        flows = fake._energy_distribution_for_range.call_args_list
        self.assertEqual(flows[0].args[1:3], (start, start+timedelta(days=1)))
        self.assertEqual(flows[-1].args[1:3], (datetime(2026, 10, 15, 17), end))
        self.assertEqual(result['pv_kwh'], [6, None, None, None, None, None, 5])

    def test_ledger_receives_same_calendar_bounds_and_snapshot_cutoff(self):
        cutoff = datetime(2026, 10, 16, 6, 38)
        for tz, start in [('Asia/Ho_Chi_Minh', datetime(2026, 10, 9, 17)),
                          ('UTC', datetime(2026, 10, 10))]:
            with self.subTest(tz=tz):
                fake = self.reader(tz, ledger=True)
                with patch.object(fields.Datetime, 'now', return_value=cutoff+timedelta(days=1)):
                    result = SmartSolarDashboard.get_energy_comparison(
                        fake, '1week', 12, end_at=cutoff)
                self.assertEqual(result, {'source': 'ledger'})
                fake._read_energy_ledger.assert_called_once_with(start, cutoff, 12, None)

    def test_midnight_cutoff_has_seven_completed_dates(self):
        end = datetime(2026, 10, 15, 17)  # Oct 16 00:00 local, exclusive
        fake = self.reader()
        with patch.object(fields.Datetime, 'now', return_value=end):
            result = SmartSolarDashboard.get_energy_comparison(fake, '1week', 12)
        self.assertEqual(result['labels'], [f'2026-10-{day:02d}' for day in range(9, 16)])

    def test_explicit_partial_day_bounds_are_preserved(self):
        end = datetime(2026, 10, 16, 6)
        start = end-timedelta(hours=6)
        fake = self.reader(ledger=True)
        with patch.object(fields.Datetime, 'now', return_value=end):
            SmartSolarDashboard.get_energy_comparison(fake, '1week', 12, date_bounds=(start, end))
        fake._read_energy_ledger.assert_called_once_with(start, end, 12, None)

    def test_other_periods_keep_their_existing_bounds(self):
        end = datetime(2026, 10, 16, 6)
        for period in ('6h', '1month', 'this_month'):
            with self.subTest(period=period):
                fake = self.reader(ledger=True)
                with patch.object(fields.Datetime, 'now', return_value=end):
                    _, start, _ = fake._resolve_time_range(period, 12)
                    SmartSolarDashboard.get_energy_comparison(fake, period, 12)
                fake._read_energy_ledger.assert_called_once_with(start, end, 12, None)


class TestChartEnergyComparison(TestCase):
    def reader(self, tz='Asia/Ho_Chi_Minh'):
        fake = SimpleNamespace(_get_system_timezone=lambda *args: tz, _get_safe_timezone=lambda *args: tz)
        fake._resolve_time_range = lambda *args: SmartSolarDashboard._resolve_time_range(fake, *args)
        fake._energy_distribution_for_range = MagicMock(side_effect=lambda cfg, a, b, sid, period: {
            'available': True, 'data': [(b-a).total_seconds()/6000, (b-a).total_seconds()/60000],
            'estimated': True, 'coverage_pct': 100})
        fake._get_daily_pv_energy = MagicMock(side_effect=lambda a, b, sid: {
            '2026-11-20': (b-a).total_seconds()/4000})
        fake.get_energy_comparison = MagicMock(return_value={'source': 'daily'})
        return fake

    def test_24h_is_today_with_24_hours_and_unknown_future(self):
        cutoff = datetime(2026, 11, 19, 18, 30)  # Nov 20 01:30 in Vietnam
        fake = self.reader()
        with patch.object(fields.Datetime, 'now', return_value=cutoff+timedelta(days=1)):
            result = SmartSolarDashboard._get_chart_energy_comparison(fake, '24h', 12, end_at=cutoff)
        self.assertEqual(result['labels'], [f'2026-11-20 {hour:02d}:00' for hour in range(24)])
        self.assertEqual(result['inverter_kwh'][:2], [0.6, 0.3])
        self.assertEqual(result['grid_kwh'][:2], [0.06, 0.03])
        self.assertEqual(result['total_kwh'][:2], [0.66, 0.33])
        self.assertEqual(result['pv_kwh'][:2], [0.9, 0.45])
        self.assertEqual(result['total_kwh'][2:], [None]*22)
        self.assertEqual(result['pv_kwh'][2:], [None]*22)
        self.assertEqual(fake._energy_distribution_for_range.call_count, 2)
        self.assertEqual(fake._energy_distribution_for_range.call_args_list[0].args[1], datetime(2026, 11, 19, 17))
        self.assertEqual(fake._energy_distribution_for_range.call_args_list[-1].args[2], cutoff)
        fake.get_energy_comparison.assert_not_called()

    def test_midnight_and_system_timezone_determine_calendar_day(self):
        cutoff = datetime(2026, 11, 19, 17)
        fake = self.reader()
        with patch.object(fields.Datetime, 'now', return_value=cutoff):
            result = SmartSolarDashboard._get_chart_energy_comparison(fake, '24h', 12)
        self.assertEqual(result['labels'][0], '2026-11-20 00:00')
        self.assertEqual(result['total_kwh'], [None]*24)
        fake._energy_distribution_for_range.assert_not_called()
        fake._get_daily_pv_energy.assert_not_called()
        utc = self.reader('UTC')
        with patch.object(fields.Datetime, 'now', return_value=cutoff):
            result = SmartSolarDashboard._get_chart_energy_comparison(utc, '24h', 12)
        self.assertEqual(result['labels'][0], '2026-11-19 00:00')
        self.assertEqual(result['labels'][-1], '2026-11-19 23:00')

    def test_rolling_short_windows_keep_exact_shared_bounds_and_zero(self):
        cutoff = datetime(2026, 11, 20, 2, 36, 28)
        for period, seconds in [('2min', 120), ('1h', 3600), ('6h', 21600), ('12h', 43200)]:
            with self.subTest(period=period):
                fake = self.reader()
                with patch.object(fields.Datetime, 'now', return_value=cutoff):
                    result = SmartSolarDashboard._get_chart_energy_comparison(fake, period, 12)
                calls = fake._energy_distribution_for_range.call_args_list
                self.assertEqual(calls[0].args[1], cutoff-timedelta(seconds=seconds))
                self.assertEqual(calls[-1].args[2], cutoff)
                self.assertTrue(all(left.args[2] == right.args[1] for left, right in zip(calls, calls[1:])))
                self.assertAlmostEqual(sum(result['inverter_kwh']), seconds/6000, places=5)
                self.assertLessEqual(len(result['labels']), 14)
                fake.get_energy_comparison.assert_not_called()
        fake = self.reader()
        fake._energy_distribution_for_range.side_effect = None
        fake._energy_distribution_for_range.return_value = {'available': True, 'data': [0, 0]}
        fake._get_daily_pv_energy.side_effect = None
        fake._get_daily_pv_energy.return_value = {'2026-11-20': 0}
        with patch.object(fields.Datetime, 'now', return_value=cutoff):
            result = SmartSolarDashboard._get_chart_energy_comparison(fake, '1h', 12)
        self.assertEqual(set(result['total_kwh']), {0})
        self.assertEqual(set(result['pv_kwh']), {0})

    def test_missing_measurements_remain_unknown(self):
        cutoff = datetime(2026, 11, 20, 2)
        fake = self.reader()
        fake._energy_distribution_for_range.side_effect = None
        fake._energy_distribution_for_range.return_value = {'available': False, 'data': [None, None]}
        fake._get_daily_pv_energy.side_effect = None
        fake._get_daily_pv_energy.return_value = {}
        with patch.object(fields.Datetime, 'now', return_value=cutoff):
            result = SmartSolarDashboard._get_chart_energy_comparison(fake, '1h', 12)
        self.assertTrue(all(value is None for value in result['total_kwh']))
        self.assertTrue(all(value is None for value in result['pv_kwh']))

    def test_long_windows_delegate_daily_reader_with_exact_shared_bounds(self):
        cutoff = datetime(2026, 11, 20, 2)
        for period in ('1week', '1month', '3month', '6month', '1year', '5year'):
            with self.subTest(period=period):
                fake = self.reader()
                with patch.object(fields.Datetime, 'now', return_value=cutoff):
                    _, start, _ = fake._resolve_time_range(period, 12)
                    result = SmartSolarDashboard._get_chart_energy_comparison(fake, period, 12, end_at=cutoff)
                fake.get_energy_comparison.assert_called_once_with(
                    period, 12, None, end_at=cutoff, date_bounds=(start, cutoff))
                self.assertEqual(result['bucket'], 'day')

    def test_dashboard_requests_selected_period_without_week_override(self):
        cutoff = datetime(2026, 11, 20, 2)
        for period in ('2min', '1h', '6h', '12h', '24h', '1week', '1month'):
            with self.subTest(period=period):
                dashboard = MagicMock()
                dashboard.with_context.return_value = dashboard
                dashboard._get_chart_energy_comparison.return_value = {'source': 'selected_range'}
                result = SmartSolarDashboard.get_dashboard_data(
                    dashboard, period, 12, end_at=cutoff, include_overview=False)
                dashboard._get_chart_energy_comparison.assert_called_once_with(period, 12, {}, cutoff)
                self.assertEqual(result['energy_comparison'], {'source': 'selected_range'})

    def test_dashboard_can_skip_the_independent_heatmap(self):
        dashboard = MagicMock()
        dashboard.with_context.return_value = dashboard
        result = SmartSolarDashboard.get_dashboard_data(
            dashboard, '24h', 12, include_overview=False, include_energy_comparison=False,
            include_heatmap=False)
        self.assertNotIn('heatmap', result)
        dashboard.get_heatmap_data.assert_not_called()


class TestIndependentEnergyChart(TestCase):
    reader = TestEnergyWeek.reader

    def test_chart_day_has_full_hour_axis_and_stops_reading_at_now(self):
        end = datetime(2026, 10, 16, 6, 38)
        fake = self.reader()
        with patch.object(fields.Datetime, 'now', return_value=end):
            result = SmartSolarDashboard.get_energy_chart(fake, 'day', 12)
        self.assertEqual(result['labels'], [f'{hour:02d}:00' for hour in range(24)])
        self.assertEqual(result['total_kwh'], [3] * 14 + [None] * 10)
        calls = fake._energy_distribution_for_range.call_args_list
        self.assertEqual(calls[0].args[1:3], (datetime(2026, 10, 15, 17), datetime(2026, 10, 15, 18)))
        self.assertEqual(calls[-1].args[1:3], (datetime(2026, 10, 16, 6), end))

    def test_chart_week_keeps_today_even_at_midnight(self):
        end = datetime(2026, 10, 15, 17)
        fake = self.reader()
        fake.with_context = MagicMock(return_value=fake)
        fake.get_energy_comparison = MagicMock(return_value={
            'labels': ['2026-10-10'], 'inverter_kwh': [2], 'grid_kwh': [1], 'pv_kwh': [3]})
        with patch.object(fields.Datetime, 'now', return_value=end):
            result = SmartSolarDashboard.get_energy_chart(fake, '1week', 12)
        self.assertEqual(result['labels'], [f'{day}/10' for day in range(10, 17)])
        self.assertEqual(result['total_kwh'], [3] + [None] * 6)
        fake.get_energy_comparison.assert_called_once_with(
            '1week', 12, date_bounds=(datetime(2026, 10, 9, 17), end))

    def test_chart_year_sums_12_calendar_months_with_missing_data(self):
        end = datetime(2026, 10, 16, 6)
        fake = self.reader()
        fake.with_context = MagicMock(return_value=fake)
        fake.get_energy_comparison = MagicMock(return_value={
            'labels': ['2025-11-01', '2025-11-02', '2026-10-16'],
            'inverter_kwh': [2, 4, 0], 'grid_kwh': [1, 3, 0], 'pv_kwh': [5, 6, None]})
        with patch.object(fields.Datetime, 'now', return_value=end):
            result = SmartSolarDashboard.get_energy_chart(fake, '1year', 12)
        self.assertEqual(len(result['labels']), 12)
        self.assertEqual((result['labels'][0], result['labels'][-1]), ('11/2025', '10/2026'))
        self.assertEqual(result['total_kwh'], [10] + [None] * 10 + [0])
        self.assertEqual(result['pv_kwh'], [11] + [None] * 11)
        fake.get_energy_comparison.assert_called_once_with(
            '1year', 12, date_bounds=(datetime(2025, 10, 31, 17), end))
