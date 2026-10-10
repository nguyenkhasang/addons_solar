from copy import deepcopy
from datetime import timedelta, timezone
from pathlib import Path
from odoo import fields
from odoo.tests import TransactionCase, tagged
from odoo.exceptions import AccessError, ValidationError
from ..services.jk_bms.protocol import decode_telemetry


@tagged('post_install', '-at_install', 'smartsolar_bms')
class TestBmsIngestion(TransactionCase):
    def setUp(self):
        super().setUp()
        self.system = self.env['smartsolar.system'].create({'name': 'BMS test', 'code': 'BMS-TEST'})
        self.battery = self.env['smartsolar.battery'].create({
            'name': 'BMS test', 'system_id': self.system.id, 'device_identifier': 'TEST-BMS'})
        self.collector = self.env['res.users'].create({
            'name': 'BMS test collector', 'login': 'bms-test-collector',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                 self.env.ref('smartsolar.group_bms_collector').id])]})
        raw = bytes.fromhex((Path(__file__).parent / 'fixtures/jk_pb1a16s10p_19_10.hex').read_text())
        self.state = decode_telemetry(raw)
        self.state.update(device_identifier='TEST-BMS', bms_model='JK_PB1A16S10P', firmware='19.10', hardware='19A')

    def sample(self, at):
        state = deepcopy(self.state)
        state['timestamp'] = at.replace(tzinfo=timezone.utc).isoformat()
        return state

    def test_latest_history_sampling_idempotency_and_power(self):
        now = fields.Datetime.now()
        b = self.battery.with_user(self.collector)
        for seconds in [-58, -55, -1]:
            self.assertTrue(b._ingest(self.sample(now + timedelta(seconds=seconds))))
        self.assertEqual(len(self.battery.telemetry_ids), 1)
        self.assertFalse(b._ingest(self.sample(now - timedelta(seconds=1))))
        self.assertTrue(b._ingest(self.sample(now + timedelta(seconds=2))))
        self.assertEqual(len(self.battery.telemetry_ids), 2)
        self.assertAlmostEqual(b.latest_state['power'], -199.0288)
        self.assertEqual(b._snapshot()['status'], 'online')

    def test_disable_history_keeps_latest_and_resumes_sampling(self):
        now = fields.Datetime.now()
        b = self.battery.with_user(self.collector)
        b._ingest(self.sample(now - timedelta(seconds=58)))
        history_at = self.battery.last_history_at
        self.battery.history_enabled = False
        b._ingest(self.sample(now + timedelta(seconds=2)))
        self.assertEqual(len(self.battery.telemetry_ids), 1)
        self.assertEqual(self.battery.last_history_at, history_at)
        self.assertEqual(self.battery.last_seen, now + timedelta(seconds=2))
        self.assertFalse(b._snapshot()['history_enabled'])
        self.assertEqual(b._snapshot()['status'], 'online')
        self.battery.history_enabled = True
        b._ingest(self.sample(now + timedelta(seconds=3)))
        self.assertEqual(len(self.battery.telemetry_ids), 2)

    def test_rejected_payload_and_permissions(self):
        b = self.battery.with_user(self.collector)
        for key, value in [('soc', 101), ('cells', [3.3]), ('device_identifier', 'OTHER')]:
            state = deepcopy(self.state); state[key] = value
            with self.assertRaises(ValidationError): b._ingest(state)
        reader = self.env['res.users'].create({'name':'BMS reader','login':'bms-test-reader',
            'group_ids':[(6,0,[self.env.ref('base.group_user').id])]})
        self.assertTrue(self.battery.with_user(reader)._snapshot())
        with self.assertRaises(AccessError): self.battery.with_user(reader)._ingest(self.state)
        with self.assertRaises(AccessError): self.env['smartsolar.battery'].with_user(self.collector).create({
            'name':'unauthorized', 'system_id': self.system.id, 'device_identifier':'NO'})
