from datetime import datetime, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'smartsolar_dashboard')
class TestLifetime(TransactionCase):
    def setUp(self):
        super().setUp()
        self.system = self.env['smartsolar.system'].create({
            'name': 'Lifetime test', 'code': 'LIFETIME-TEST', 'timezone': 'Asia/Ho_Chi_Minh'})
        self.ledger = self.env['smartsolar.lifetime'].create({
            'system_id': self.system.id, 'first_day': '2026-10-01'})
        self.dashboard = self.env['smartsolar.dashboard']
        self.cutoff = datetime(2026, 10, 14, 8)
        self.tariff = {'mode': 'tiered', 'flat_price': 3000, 'vat_pct': 8,
                       'rates': [1984, 2050, 2380, 2998, 3350, 3460]}

    def day(self, date='2026-10-01', inverter=5, grid=2, pv=7):
        return self.env['smartsolar.lifetime.day'].create({
            'lifetime_id': self.ledger.id, 'day': date, 'known': True,
            'inverter_kwh': inverter, 'grid_kwh': grid, 'pv_kwh': pv, 'pv_known': True})

    def refresh(self, month='2026-10-01', tariff=None):
        self.ledger._refresh_month(fields.Date.to_date(month), self.cutoff, tariff or self.tariff)

    def test_rerun_correction_and_downward_correction_apply_only_delta(self):
        day = self.day()
        self.refresh()
        original_saved = self.ledger.saved
        self.refresh()
        self.assertEqual(self.ledger.inverter_kwh, 5)
        self.assertEqual(self.ledger.saved, original_saved)
        day.inverter_kwh = 5.2
        self.refresh()
        self.assertAlmostEqual(self.ledger.inverter_kwh, 5.2)
        day.write({'inverter_kwh': 3, 'grid_kwh': 1})
        self.refresh()
        self.assertEqual(self.ledger.inverter_kwh, 3)
        self.assertEqual(self.ledger.grid_kwh, 1)
        self.assertEqual(self.ledger.known_days, 1)

    def test_monthly_tiers_not_daily_bill_sum_and_closed_tariff_frozen(self):
        self.day('2026-09-01', inverter=40, grid=40)
        self.day('2026-09-02', inverter=40, grid=40)
        self.refresh('2026-09-01')
        from ..models.electricity_tariff import electricity_bill
        expected = electricity_bill(160, self.tariff)['total'] - electricity_bill(80, self.tariff)['total']
        self.assertEqual(self.ledger.saved, expected)
        doubled = dict(self.tariff, rates=[v*2 for v in self.tariff['rates']])
        self.refresh('2026-09-01', doubled)
        self.assertEqual(self.ledger.saved, expected)
        self.day('2026-10-01', inverter=40, grid=40)
        self.refresh(tariff=doubled)
        current = electricity_bill(80, doubled)['total'] - electricity_bill(40, doubled)['total']
        self.assertEqual(self.ledger.saved, expected + current)

    def test_unknown_is_not_zero_and_total_read_never_reads_sources(self):
        result = self.env['smartsolar.lifetime'].get_totals(self.system.id)
        self.assertIsNone(result['grid_kwh'])
        self.assertIsNone(result['saved'])
        self.day(inverter=0, grid=0, pv=0)
        self.refresh()
        self.ledger.write({'updated_at': self.cutoff, 'initialized': True})
        with patch.object(type(self.dashboard), 'get_energy_comparison',
                          side_effect=AssertionError('F5 must not recalculate history')):
            result = self.env['smartsolar.lifetime'].get_totals(self.system.id)
        self.assertEqual(result['grid_kwh'], 0)
        self.assertEqual(result['saved'], 0)
        self.assertEqual(result['missing_days'], 13)

    def test_transaction_failure_does_not_leave_half_applied_total(self):
        self.day()
        with self.assertRaises(RuntimeError):
            with self.env.cr.savepoint():
                self.refresh()
                raise RuntimeError('simulate worker failure')
        self.assertEqual(self.ledger.inverter_kwh, 0)
        self.assertFalse(self.env['smartsolar.lifetime.month'].search([
            ('lifetime_id', '=', self.ledger.id)]))
        self.refresh()
        self.assertEqual(self.ledger.inverter_kwh, 5)

    def test_stalled_grid_counter_and_reset_use_same_validated_flow(self):
        device = self.env['smartsolar.device'].create({
            'name': 'Lifetime GTI', 'device_guid': 'LIFETIME-GTI',
            'device_type': 'grid_tie_inverter', 'system_id': self.system.id})
        at = datetime(2026, 10, 14, 5)
        for offset, counter in [(0, 10), (60, 2), (120, 2)]:
            self.env['grid.tie.inverter'].create({
                'system_id': self.system.id, 'device_id': device.id,
                'device_guid': device.device_guid, 'record_date': at+timedelta(seconds=offset),
                'energy_total': counter, 'limiter_total': 0,
                'output_power': 200, 'limiter_power': 100})
        self.ledger._refresh_dates(['2026-10-14'], self.cutoff, self.tariff)
        self.assertGreater(self.ledger.grid_kwh, 0)
        self.assertGreater(self.ledger.inverter_kwh, 0)
        self.assertTrue(self.ledger.estimated_days)
        initial = self.ledger.grid_kwh
        self.ledger._refresh_dates(['2026-10-14'], self.cutoff, self.tariff)
        self.assertEqual(self.ledger.grid_kwh, initial)

    def test_purged_history_preserves_ledger_and_late_raw_marks_old_day(self):
        self.day('2026-10-02')
        self.refresh()
        empty = {'labels': ['2026-10-02'], 'inverter_kwh': [None], 'grid_kwh': [None],
                 'pv_kwh': [None], 'estimated': [True], 'coverage_pct': [0]}
        with patch.object(type(self.dashboard), 'get_energy_comparison', return_value=empty):
            self.ledger._refresh_dates(['2026-10-02'], self.cutoff, self.tariff)
        self.assertEqual(self.ledger.inverter_kwh, 5)
        self.ledger.write({'scanned_at': fields.Datetime.now(), 'pending_days': []})
        device = self.env['smartsolar.device'].create({
            'name': 'Late raw', 'device_guid': 'LIFETIME-LATE',
            'device_type': 'grid_tie_inverter', 'system_id': self.system.id})
        self.env['grid.tie.inverter'].create({
            'system_id': self.system.id, 'device_id': device.id,
            'device_guid': device.device_guid, 'record_date': datetime(2026, 10, 2, 5),
            'output_power': 200, 'limiter_power': 100})
        self.ledger._enqueue_changes(fields.Datetime.now())
        self.assertIn('2026-10-02', self.ledger.pending_days)
        self.assertIn('2026-10-01', self.ledger.pending_days)

    def test_different_systems_do_not_share_energy_or_tariff_allowance(self):
        other = self.env['smartsolar.system'].create({'name': 'Other', 'code': 'LIFETIME-OTHER'})
        ledger = self.env['smartsolar.lifetime'].create({'system_id': other.id})
        self.day()
        self.refresh()
        result = self.env['smartsolar.lifetime'].get_totals(other.id)
        self.assertIsNone(result['inverter_kwh'])
        self.assertEqual(ledger.inverter_kwh, 0)

    def test_invalid_existing_source_can_invalidate_an_old_day(self):
        self.day('2026-10-02')
        self.refresh()
        device = self.env['smartsolar.device'].create({
            'name': 'Invalid raw', 'device_guid': 'LIFETIME-INVALID',
            'device_type': 'grid_tie_inverter', 'system_id': self.system.id})
        self.env['grid.tie.inverter'].create({
            'system_id': self.system.id, 'device_id': device.id,
            'device_guid': device.device_guid, 'record_date': datetime(2026, 10, 2, 5),
            'output_power': -200, 'limiter_power': -100})
        self.ledger._refresh_dates(['2026-10-02'], self.cutoff, self.tariff)
        self.assertEqual(self.ledger.known_days, 0)
        self.assertIsNone(self.env['smartsolar.lifetime'].get_totals(self.system.id)['saved'])

    def test_bootstrap_batch_is_bounded_and_pending_is_persistent(self):
        self.ledger.pending_days = ['2026-10-%02d' % day for day in range(1, 15)]
        with patch.object(type(self.ledger), '_enqueue_changes'), \
             patch.object(type(self.ledger), '_refresh_dates') as refresh, \
             patch.object(fields.Datetime, 'now', return_value=self.cutoff):
            self.env['smartsolar.lifetime']._cron_refresh(batch_days=3)
        own = [call for call in refresh.call_args_list if '2026-10-14' in call.args[0]]
        self.assertEqual(len(own), 1)
        self.assertEqual(len(own[0].args[0]), 3)
        self.assertEqual(len(self.ledger.pending_days), 11)
        self.assertFalse(self.ledger.initialized)
