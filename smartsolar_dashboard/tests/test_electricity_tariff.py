"""Tier boundaries, VAT and monthly avoided-cost regression tests."""
from unittest import TestCase
from types import SimpleNamespace
from ..models.electricity_tariff import DEFAULT_RATES, electricity_bill, validate_tariff
from ..models.smartsolar_dashboard import SmartSolarDashboard


class TestElectricityTariff(TestCase):
    def tariff(self, **changes):
        return dict(mode='tiered', flat_price=3000, vat_pct=8, rates=DEFAULT_RATES, **changes)

    def test_every_tier_boundary_and_fractional_kwh(self):
        tariff = self.tariff()
        for kwh, expected in [(0, 0), (50, 99200), (100, 201700),
                              (200, 439700), (300, 739500), (400, 1074500),
                              (401, 1077960), (500, 1420500), (50.5, 100225)]:
            with self.subTest(kwh=kwh):
                self.assertEqual(electricity_bill(kwh, tariff)['subtotal'], expected)
        self.assertEqual(electricity_bill(100, tariff),
                         dict(subtotal=201700, tax=16136, total=217836))

    def test_custom_rates_flat_price_and_zero_vat(self):
        tariff = self.tariff()
        tariff.update(rates=[1, 2, 3, 4, 5, 6], vat_pct=0)
        self.assertEqual(electricity_bill(450, tariff)['total'], 1650)
        tariff.update(mode='flat', flat_price=2000, vat_pct=10)
        self.assertEqual(electricity_bill(12.5, tariff)['total'], 27500)
        self.assertIsNone(electricity_bill(None, tariff))

    def test_half_dong_rounds_up(self):
        tariff = self.tariff()
        tariff.update(mode='flat', flat_price=1, vat_pct=0)
        self.assertEqual(electricity_bill(0.5, tariff)['total'], 1)

    def test_invalid_configuration_is_rejected(self):
        for patch in [dict(vat_pct=-1), dict(vat_pct=101), dict(vat_pct=float('nan')),
                      dict(rates=[1]*5), dict(rates=[-1]*6), dict(flat_price=-1),
                      dict(mode='unknown')]:
            tariff = self.tariff()
            tariff.update(patch)
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                validate_tariff(tariff)

    def daily(self, inverter, grid):
        return dict(labels=['2026-10-01', '2026-10-02'],
                    inverter_kwh=inverter, grid_kwh=grid,
                    total_kwh=[a+b if a is not None and b is not None else None
                               for a, b in zip(inverter, grid)],
                    estimated=[False, False], notes=[None, None], coverage_pct=[100, 100])

    def test_savings_is_avoided_top_tiers_not_bill_for_solar_alone(self):
        daily = self.daily([50, 50], [150, 150])
        result = SmartSolarDashboard._build_energy_widgets(daily, [80, 80], 3000, tariff=self.tariff())
        self.assertEqual(result['payable'], 798660)
        self.assertEqual(result['without_solar'], 1160460)
        self.assertEqual(result['saved'], 361800)
        self.assertNotEqual(result['saved'], electricity_bill(100, self.tariff())['total'])

    def test_missing_branch_does_not_invent_savings(self):
        daily = self.daily([100, None], [50, 200])
        result = SmartSolarDashboard._build_energy_widgets(daily, [1, 2], 3000, tariff=self.tariff())
        self.assertEqual(result['billing_days'], 1)
        self.assertEqual(result['payable'], electricity_bill(50, self.tariff())['total'])
        self.assertEqual(result['saved'], electricity_bill(150, self.tariff())['total'] - result['payable'])
        daily = self.daily([None, None], [None, None])
        result = SmartSolarDashboard._build_energy_widgets(daily, [None, None], 3000, tariff=self.tariff())
        self.assertIsNone(result['saved'])
        self.assertIsNone(result['payable'])

    def test_month_tariff_is_not_reset_each_day(self):
        result = SmartSolarDashboard._build_energy_widgets(self.daily([0, 0], [50, 50]),
                                                          [0, 0], 3000, tariff=self.tariff())
        self.assertEqual(result['payable'], 217836)
        self.assertEqual(result['saved'], 0)

    def test_separate_meters_do_not_share_tier_allowances(self):
        bill = SmartSolarDashboard._build_energy_widgets(self.daily([0, 0], [50, 50]),
                                                        [0, 0], 3000, tariff=self.tariff())
        combined = SmartSolarDashboard._sum_meter_bills([bill, bill])
        self.assertEqual(combined['payable'], 435672)
        self.assertNotEqual(combined['payable'], electricity_bill(200, self.tariff())['total'])
        self.assertEqual(combined['meter_count'], 2)
        unknown = dict(bill, payable=None)
        self.assertIsNone(SmartSolarDashboard._sum_meter_bills([bill, unknown])['payable'])

    def test_settings_load_defaults_and_preserve_explicit_zero_rates_and_vat(self):
        stored = {}
        param = SimpleNamespace(get_param=lambda key, default: stored.get(key, default))
        param.sudo = lambda: param
        fake = SimpleNamespace(env={'ir.config_parameter': param})
        settings = SmartSolarDashboard._get_settings(fake)
        self.assertEqual(settings['tariff']['rates'], list(DEFAULT_RATES))
        self.assertEqual(settings['tariff']['vat_pct'], 8)
        self.assertEqual(settings['tariff']['mode'], 'tiered')
        stored.update({'smartsolar.tier_1_price': '0', 'smartsolar.electricity_vat': '0',
                       'smartsolar.tariff_mode': 'flat', 'smartsolar.billing_scope': 'shared'})
        settings = SmartSolarDashboard._get_settings(fake)
        self.assertEqual(settings['tariff']['rates'][0], 0)
        self.assertEqual(settings['tariff']['vat_pct'], 0)
        self.assertEqual(settings['tariff']['mode'], 'flat')
        self.assertEqual(settings['billing_scope'], 'shared')
