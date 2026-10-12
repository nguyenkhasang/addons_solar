from datetime import timedelta
from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'smartsolar_env_retention')
class TestEnvironmentRetention(TransactionCase):
    def test_raw_and_daily_retention_are_independent_of_energy(self):
        system = self.env['smartsolar.system'].create({'name': 'Weather retention test', 'code': 'ENV-RETENTION'})
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('smartsolar.env_raw_retention_days', 30)
        params.set_param('smartsolar.env_daily_retention_days', 30)
        params.set_param('smartsolar.daily_retention_days', 0)
        now = fields.Datetime.now()
        raw = self.env['smartsolar.environment']
        daily = self.env['smartsolar.environment.summary']
        old_raw = raw.create({'system_id': system.id, 'record_date': now - timedelta(days=32)})
        new_raw = raw.create({'system_id': system.id, 'record_date': now - timedelta(days=29)})
        old_daily = daily.create({'system_id': system.id, 'bucket_start': now - timedelta(days=32)})
        new_daily = daily.create({'system_id': system.id, 'bucket_start': now - timedelta(days=29)})
        self.env['smartsolar.system']._cron_purge_old_data()
        self.assertFalse(old_raw.exists())
        self.assertFalse(old_daily.exists())
        self.assertTrue(new_raw.exists())
        self.assertTrue(new_daily.exists())
        self.assertEqual(params.get_param('smartsolar.daily_retention_days'), '0')
