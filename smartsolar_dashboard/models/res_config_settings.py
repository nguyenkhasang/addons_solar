# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError
from .electricity_tariff import validate_tariff


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    smartsolar_electricity_price = fields.Float(
        string="Electricity Price",
        config_parameter="smartsolar.electricity_price",
        default=2000.0,
    )
    smartsolar_tariff_mode = fields.Selection(
        [('tiered', 'Sinh hoạt 6 bậc'), ('flat', 'Một đơn giá')],
        string='Cách tính tiền điện', config_parameter='smartsolar.tariff_mode', default='tiered',
    )
    smartsolar_electricity_vat = fields.Float(
        string='Thuế GTGT (%)', config_parameter='smartsolar.electricity_vat', default=8.0,
    )
    smartsolar_billing_scope = fields.Selection(
        [('per_system', 'Mỗi hệ thống một công-tơ'), ('shared', 'Tất cả hệ thống chung một công-tơ')],
        string='Công-tơ khi xem tất cả hệ thống',
        config_parameter='smartsolar.billing_scope', default='per_system',
    )
    smartsolar_tier_1_price = fields.Float(
        string='Đơn giá bậc 1', config_parameter='smartsolar.tier_1_price', default=1984.0,
    )
    smartsolar_tier_2_price = fields.Float(
        string='Đơn giá bậc 2', config_parameter='smartsolar.tier_2_price', default=2050.0,
    )
    smartsolar_tier_3_price = fields.Float(
        string='Đơn giá bậc 3', config_parameter='smartsolar.tier_3_price', default=2380.0,
    )
    smartsolar_tier_4_price = fields.Float(
        string='Đơn giá bậc 4', config_parameter='smartsolar.tier_4_price', default=2998.0,
    )
    smartsolar_tier_5_price = fields.Float(
        string='Đơn giá bậc 5', config_parameter='smartsolar.tier_5_price', default=3350.0,
    )
    smartsolar_tier_6_price = fields.Float(
        string='Đơn giá bậc 6', config_parameter='smartsolar.tier_6_price', default=3460.0,
    )
    smartsolar_co2_factor = fields.Float(
        string="CO2 Factor",
        config_parameter="smartsolar.co2_factor",
        default=0.5,
    )
    smartsolar_temperature_alert = fields.Float(
        string="Temperature Alert",
        config_parameter="smartsolar.temperature_alert",
        default=60.0,
    )
    smartsolar_dashboard_refresh = fields.Integer(
        string="Dashboard Refresh",
        config_parameter="smartsolar.dashboard_refresh",
        default=60,
    )

    @api.constrains('smartsolar_tariff_mode', 'smartsolar_electricity_vat',
                    'smartsolar_electricity_price',
                    'smartsolar_tier_1_price', 'smartsolar_tier_2_price',
                    'smartsolar_tier_3_price', 'smartsolar_tier_4_price',
                    'smartsolar_tier_5_price', 'smartsolar_tier_6_price')
    def _check_electricity_tariff(self):
        for record in self:
            try:
                validate_tariff({
                    'mode': record.smartsolar_tariff_mode,
                    'vat_pct': record.smartsolar_electricity_vat,
                    'flat_price': record.smartsolar_electricity_price,
                    'rates': [record[f'smartsolar_tier_{i}_price'] for i in range(1, 7)],
                })
            except ValueError as error:
                raise ValidationError(str(error)) from error
