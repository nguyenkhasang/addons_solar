"""PV day boundaries, retained summaries and system/device aggregation."""
from datetime import datetime

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'smartsolar_dashboard')
class TestDailyPvEnergy(TransactionCase):
    def setUp(self):
        super().setUp()
        self.system = self.env['smartsolar.system'].create({
            'name': 'PV chart test', 'code': 'PV-CHART-TEST', 'timezone': 'Asia/Ho_Chi_Minh'})
        self.dashboard = self.env['smartsolar.dashboard']
        self.start = datetime(2026, 10, 6, 17)  # Oct 7 local midnight
        self.end = datetime(2026, 10, 8, 17)

    def device(self, guid):
        return self.env['smartsolar.device'].create({
            'name': guid, 'device_guid': guid, 'device_type': 'charge_power',
            'system_id': self.system.id})

    def sample(self, device, at, kwh):
        return self.env['charge.power'].create({
            'device_id': device.id, 'device_guid': device.device_guid,
            'system_id': self.system.id, 'record_date': at, 'today_kwh': kwh})

    def test_daily_register_sums_devices_and_keeps_missing_days(self):
        a, b = self.device('PV-CHART-A'), self.device('PV-CHART-B')
        self.sample(a, datetime(2026, 10, 7, 3), 2)
        self.sample(a, datetime(2026, 10, 7, 8), 4)
        self.sample(b, datetime(2026, 10, 7, 8), 3)
        result = self.dashboard._get_daily_pv_energy(self.start, self.end, self.system.id)
        self.assertEqual(result, {'2026-10-07': 7})

    def test_partial_first_day_subtracts_seed_and_missing_seed_stays_unknown(self):
        device = self.device('PV-CHART-PARTIAL')
        self.sample(device, datetime(2026, 10, 7, 3), 2)
        self.sample(device, datetime(2026, 10, 7, 8), 5)
        result = self.dashboard._get_daily_pv_energy(datetime(2026, 10, 7, 4), self.end, self.system.id)
        self.assertEqual(result['2026-10-07'], 3)
        self.sample(self.device('PV-CHART-NO-SEED'), datetime(2026, 10, 7, 8), 1)
        result = self.dashboard._get_daily_pv_energy(datetime(2026, 10, 7, 4), self.end, self.system.id)
        self.assertIsNone(result['2026-10-07'])

    def test_confirmed_summary_survives_raw_retention_without_double_counting(self):
        device = self.device('PV-CHART-SUMMARY')
        self.env['charge.power.summary'].create({
            'device_id': device.id, 'system_id': self.system.id,
            'device_guid': device.device_guid, 'bucket_type': 'day',
            'bucket_start': self.start, 'energy_kwh': 7, 'quality_version': 4,
            'counter_quality': {'total_kwh': {'reliable': True}}})
        self.sample(device, datetime(2026, 10, 7, 8), 5)
        result = self.dashboard._get_daily_pv_energy(self.start, self.end, self.system.id)
        self.assertEqual(result['2026-10-07'], 7)
