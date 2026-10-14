"""The settings form and listener use the same stored raw interval."""
from odoo.tests import TransactionCase, tagged
from odoo.exceptions import ValidationError


@tagged('post_install', '-at_install', 'smartsolar_dashboard')
class TestRawSampleSettings(TransactionCase):
    def test_default_and_saved_interval_are_visible_to_ingestion(self):
        param = self.env['ir.config_parameter'].sudo()
        param.search([('key', '=', 'smartsolar.raw_sample_seconds')]).unlink()
        settings = self.env['res.config.settings'].create({})
        self.assertEqual(settings.smartsolar_raw_sample_seconds, 10)
        settings.smartsolar_raw_sample_seconds = 30
        settings.set_values()
        self.assertEqual(param.get_param('smartsolar.raw_sample_seconds'), '30')
        self.assertEqual(self.env['smartsolar.device']._get_raw_sample_interval_seconds(), 30)
        self.assertEqual(self.env['res.config.settings'].create({}).smartsolar_raw_sample_seconds, 30)

    def test_nonpositive_interval_cannot_be_saved(self):
        for value in (0, -1):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                self.env['res.config.settings'].create({'smartsolar_raw_sample_seconds': value})

    def test_settings_view_contains_raw_interval(self):
        view = self.env.ref('smartsolar_dashboard.res_config_settings_view_form_smartsolar')
        self.assertIn('name="smartsolar_raw_sample_seconds"', view.arch_db)
