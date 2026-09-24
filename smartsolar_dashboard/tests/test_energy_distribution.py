# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'smartsolar_dashboard')
class TestEnergyDistribution(TransactionCase):

    def test_distribution_uses_selected_range_delta_and_percentages(self):
        system = self.env['smartsolar.system'].create({
            'name': 'Distribution test',
            'code': 'DISTRIBUTION-TEST',
            'timezone': 'Asia/Ho_Chi_Minh',
        })
        device = self.env['smartsolar.device'].create({
            'name': 'Distribution GTI',
            'device_guid': 'DISTRIBUTION-GTI',
            'device_type': 'grid_tie_inverter',
            'system_id': system.id,
        })
        now = fields.Datetime.now()
        samples = [
            (now - timedelta(minutes=61), 10.0, 20.0),
            # Missing legacy keys used to be persisted as zero. It must not be
            # interpreted as a real reset followed by a full lifetime delta.
            (now - timedelta(minutes=55), 0.0, 0.0),
            (now - timedelta(minutes=50), 12.0, 25.0),
            (now - timedelta(minutes=10), 16.0, 30.0),
        ]
        for at, limiter_total, energy_total in samples:
            self.env['grid.tie.inverter'].create({
                'device_guid': device.device_guid,
                'device_id': device.id,
                'system_id': system.id,
                'record_date': at,
                'limiter_total': limiter_total,
                'energy_total': energy_total,
            })

        result = self.env['smartsolar.dashboard'].get_energy_distribution(
            time_range='1h', system_id=system.id)

        self.assertEqual(
            result['labels'], ['Điện inverter cấp tải', 'Điện lấy lưới'])
        self.assertAlmostEqual(result['data'][0], 6.0, places=3)
        self.assertAlmostEqual(result['data'][1], 10.0, places=3)
        self.assertEqual(result['percentages'], [37.5, 62.5])
        self.assertEqual(result['unit'], 'kWh')
        self.assertEqual(result['time_range'], '1h')
