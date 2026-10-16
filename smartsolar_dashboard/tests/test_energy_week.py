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
