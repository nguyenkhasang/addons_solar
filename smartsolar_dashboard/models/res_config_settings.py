# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    smartsolar_electricity_price = fields.Float(
        string="Electricity Price",
        config_parameter="smartsolar.electricity_price",
        default=2000.0,
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
