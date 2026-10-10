from datetime import timezone
from pathlib import Path
from odoo import fields
from odoo.tests import TransactionCase, tagged
from odoo.addons.smartsolar.services.jk_bms.protocol import decode_telemetry


@tagged('post_install', '-at_install', 'smartsolar_bms')
class TestBmsDashboard(TransactionCase):
    def test_snapshot_and_history_scope(self):
        system=self.env['smartsolar.system'].create({'name':'BMS dashboard test','code':'BMS-DASHBOARD'})
        battery=self.env['smartsolar.battery'].create({'name':'test','system_id':system.id,'device_identifier':'DASH-BMS'})
        user=self.env['res.users'].create({'name':'BMS dashboard collector','login':'bms-dash-test',
            'group_ids':[(6,0,[self.env.ref('base.group_user').id,self.env.ref('smartsolar.group_bms_collector').id])]})
        fixture=Path(__file__).resolve().parents[2]/'smartsolar/tests/fixtures/jk_pb1a16s10p_19_10.hex'
        state=decode_telemetry(bytes.fromhex(fixture.read_text()))
        state.update(device_identifier='DASH-BMS',bms_model='JK_PB1A16S10P',firmware='19.10',hardware='19A')
        battery.with_user(user)._ingest(state)
        dashboard=self.env['smartsolar.dashboard']
        snapshots=self.env['smartsolar.battery'].get_snapshots(system.id)
        self.assertEqual(len(snapshots),1)
        self.assertEqual(len(snapshots[0]['cells']),16)
        history=dashboard.get_bms_history('24h',system.id)
        self.assertEqual(history[0]['soc'],[34])
        self.assertAlmostEqual(history[0]['battery_temperature_1'][0],34.7)
        self.assertEqual(dashboard.get_bms_history('24h',999999),[])
