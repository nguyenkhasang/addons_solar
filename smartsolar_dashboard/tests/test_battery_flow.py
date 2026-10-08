from datetime import timedelta
from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'smartsolar_dashboard')
class TestBatteryFlow(TransactionCase):
    def setUp(self):
        super().setUp()
        self.system = self.env['smartsolar.system'].create({'name': 'Battery balance', 'code': 'BALANCE-TEST'})
        self.mppt = self.device('charge_power', 'BALANCE-MPPT')
        self.gti = self.device('grid_tie_inverter', 'BALANCE-GTI')
        self.at = fields.Datetime.now().replace(second=0, microsecond=0) - timedelta(minutes=5)
        self.dashboard = self.env['smartsolar.dashboard']

    def device(self, kind, guid):
        return self.env['smartsolar.device'].create({'name': guid, 'device_guid': guid,
            'system_id': self.system.id, 'device_type': kind})

    def charger(self, at=None, voltage=50, current=10, device=None):
        device = device or self.mppt
        return self.env['charge.power'].create({'device_id': device.id, 'device_guid': device.device_guid,
            'system_id': self.system.id, 'record_date': at or self.at,
            'bat_voltage': voltage, 'bat_current': current, 'pv_voltage': 100, 'pv_current': 6,
            'charge_power': 600})

    def inverter(self, at=None, watts=200, device=None):
        device = device or self.gti
        return self.env['grid.tie.inverter'].create({'device_id': device.id, 'device_guid': device.device_guid,
            'system_id': self.system.id, 'record_date': at or self.at, 'output_power': watts})

    def result(self):
        return self.dashboard.get_battery_flow_series('1h', self.system.id)

    def test_signed_power_uses_same_row_product_not_product_of_averages(self):
        self.charger(voltage=50, current=2)
        self.inverter(watts=200)
        self.charger(self.at+timedelta(seconds=20), voltage=60, current=4)
        self.inverter(self.at+timedelta(seconds=20), watts=200)
        result = self.result()
        self.assertEqual(result['mppt_output_power'], [170])
        self.assertEqual(result['net_power'], [-30])
        self.assertAlmostEqual(result['net_current'][0], -0.667, places=3)
        self.assertEqual(result['paired_count'], [2])

    def test_missing_nearby_inverter_stays_null_not_zero_or_pv(self):
        self.charger()
        self.inverter(self.at-timedelta(seconds=20))
        result = self.result()
        self.assertEqual(result['mppt_output_power'], [500])
        self.assertEqual(result['net_power'], [None])
        self.assertEqual(result['net_current'], [None])
        self.assertEqual(result['paired_count'], [0])

    def test_multiple_inverters_require_all_devices_and_sum_their_power(self):
        second = self.device('grid_tie_inverter', 'BALANCE-GTI-SECOND')
        self.charger()
        self.inverter(watts=200)
        self.assertEqual(self.result()['net_power'], [None])
        self.inverter(watts=150, device=second)
        self.assertEqual(self.result()['net_power'], [150])

    def test_multiple_chargers_do_not_subtract_inverter_twice(self):
        second = self.device('charge_power', 'BALANCE-MPPT-SECOND')
        self.charger(voltage=50, current=10)
        self.charger(voltage=50, current=5, device=second, at=self.at+timedelta(seconds=1))
        self.inverter(watts=200)
        result = self.result()
        self.assertEqual(result['net_power'], [550])
        self.assertEqual(result['mppt_output_power'], [750])
        self.assertEqual(result['net_current'], [None])

    def test_efficiency_uses_dc_output_not_duplicate_pv_input(self):
        self.charger()
        self.inverter()
        result = self.dashboard.get_pv_efficiency_series('1h', self.system.id)
        self.assertEqual(result['pv_input_power'], [600])
        self.assertEqual(result['charge_power'], [500])
        self.assertAlmostEqual(result['efficiency'][0], 83.3, places=1)

    def test_summary_only_history_does_not_invent_net_current_or_efficiency(self):
        hour = self.at.replace(minute=0)
        self.env['charge.power.summary'].create({'device_id': self.mppt.id, 'system_id': self.system.id,
            'device_guid': self.mppt.device_guid, 'bucket_start': hour, 'bucket_type': 'hour',
            'sample_count': 60, 'bat_voltage_avg': 50, 'bat_current_avg': 10,
            'charge_power_avg': 600, 'pv_input_power_avg': 600})
        self.env.flush_all()
        self.assertFalse(self.dashboard.get_battery_flow_series('1week', self.system.id)['labels'])
        result = self.dashboard.get_pv_efficiency_series('1week', self.system.id)
        self.assertEqual(result['charge_power'], [None])
        self.assertEqual(result['efficiency'], [None])

    def test_system_filter_does_not_borrow_other_inverter(self):
        self.charger()
        other = self.env['smartsolar.system'].create({'name': 'Other bank', 'code': 'BALANCE-OTHER'})
        device = self.env['smartsolar.device'].create({'name': 'Other GTI', 'device_guid': 'BALANCE-OTHER-GTI',
            'system_id': other.id, 'device_type': 'grid_tie_inverter'})
        self.env['grid.tie.inverter'].create({'device_id': device.id, 'device_guid': device.device_guid,
            'system_id': other.id, 'record_date': self.at, 'output_power': 999})
        self.assertEqual(self.result()['net_power'], [None])

    def test_current_power_window_uses_utc_in_local_postgresql_timezone(self):
        self.inverter(fields.Datetime.now()-timedelta(seconds=30), watts=183)
        self.env.flush_all()
        self.env.cr.execute("SET LOCAL TIME ZONE 'Asia/Ho_Chi_Minh'")
        self.assertEqual(self.dashboard._get_current_total_power(self.system.id), 183)
