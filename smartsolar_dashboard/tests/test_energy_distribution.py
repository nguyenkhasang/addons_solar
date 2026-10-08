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

    def _distribution_fixture(self, offsets, output=180, grid=3, moving=False):
        system=self.env['smartsolar.system'].create({'name':'Pie power','code':'PIE-POWER'})
        device=self.env['smartsolar.device'].create({'name':'Pie GTI','device_guid':'PIE-GTI',
            'system_id':system.id,'device_type':'grid_tie_inverter'})
        now=fields.Datetime.now()
        for index, offset in enumerate(offsets):
            self.env['grid.tie.inverter'].create({'device_guid':device.device_guid,'device_id':device.id,
                'system_id':system.id,'record_date':now+timedelta(seconds=offset),
                'output_power':output,'limiter_power':grid,
                'limiter_total':1+index*0.00005 if moving else 0,
                'energy_total':2+index*0.003 if moving else 2,
                'counter_validity':{'limiter_total':True,'energy_total':True}})
        self.env.flush_all()
        return system,now

    def test_stalled_counter_cannot_claim_all_grid_while_inverter_supplies_load(self):
        system,now=self._distribution_fixture([-600,-540,-480,-420,-360,-300])
        result=self.env['smartsolar.dashboard'].get_energy_distribution('1h',system.id)
        self.assertTrue(result['estimated'])
        self.assertAlmostEqual(result['data'][0],0.015)
        self.assertAlmostEqual(result['data'][1],0.00025)
        self.assertEqual(result['percentages'],[98.4,1.6])
        self.assertGreater(result['coverage_pct'],0)
        self.assertLess(result['coverage_pct'],100)

    def test_counter_branch_scale_mismatch_falls_back_on_complete_power_coverage(self):
        from unittest.mock import patch
        system,now=self._distribution_fixture(list(range(-3600,1,60)),moving=True)
        with patch.object(fields.Datetime,'now',return_value=now):
            result=self.env['smartsolar.dashboard'].get_energy_distribution('1h',system.id)
        self.assertTrue(result['estimated'])
        self.assertEqual(result['coverage_pct'],100)
        self.assertEqual(result['percentages'],[98.4,1.6])
        self.assertIn('ánh xạ',result['reason'])

    def test_valid_zero_power_and_counters_do_not_make_a_grid_share(self):
        system,now=self._distribution_fixture([-120,-60],output=0,grid=0)
        result=self.env['smartsolar.dashboard'].get_energy_distribution('1h',system.id)
        self.assertEqual(result['data'],[0,0])
        self.assertEqual(result['percentages'],[0,0])
        self.assertFalse(result['estimated'])

    def test_outage_does_not_create_estimated_energy(self):
        system,now=self._distribution_fixture([-1000,-400])
        result=self.env['smartsolar.dashboard'].get_energy_distribution('1h',system.id)
        self.assertFalse(result['available'])
        self.assertEqual(result['data'],[None,None])

    def test_small_live_grid_energy_is_not_rounded_to_zero(self):
        system,now=self._distribution_fixture([-100,-40])
        result=self.env['smartsolar.dashboard'].get_energy_distribution('2min',system.id)
        self.assertAlmostEqual(result['data'][1],0.00005)
        self.assertGreater(result['percentages'][1],0)

    def test_durable_power_summary_can_estimate_distribution_without_raw(self):
        system,now=self._distribution_fixture([])
        device=self.env['smartsolar.device'].search([('system_id','=',system.id)],limit=1)
        at=(now-timedelta(days=2)).replace(hour=17,minute=0,second=0,microsecond=0)
        self.env['grid.tie.inverter.summary'].create({'bucket_type':'day','bucket_start':at,
            'device_id':device.id,'system_id':system.id,'quality_version':4,'sample_count':100,
            'power_energy':{'output_power':{'value':1.8,'covered_seconds':36000},
                            'grid_import_power':{'value':0.03,'covered_seconds':36000}},
            'counter_quality':{'limiter_total':{'reliable':False},'energy_total':{'reliable':False}}})
        self.env.flush_all()
        result=self.env['smartsolar.dashboard'].get_energy_distribution('3month',system.id)
        self.assertTrue(result['estimated'])
        self.assertEqual(result['data'],[1.8,0.03])
        self.assertEqual(result['percentages'],[98.4,1.6])
        self.assertEqual(result['source'],'summary_power')

    def test_grid_dependency_kpi_does_not_keep_the_rejected_counter_ratio(self):
        system,now=self._distribution_fixture([-600,-540,-480,-420,-360,-300])
        result=self.env['smartsolar.dashboard'].get_overview_kpi(system.id)
        self.assertTrue(result['grid_dependency_estimated'])
        self.assertEqual(result['grid_dependency_pct'],1.6)
        self.assertGreater(result['grid_dependency_coverage_pct'],0)
