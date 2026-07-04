# -*- coding: utf-8 -*-
import logging
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class SmartSolarController(http.Controller):

    @http.route('/smartsolar/sync_device/<int:device_id>', type='json', auth='user', methods=['POST'])
    def sync_device(self, device_id, **kwargs):
        device = request.env['smartsolar.device'].browse(device_id)
        if not device.exists():
            return {'success': False, 'error': 'Device not found'}
        try:
            result = device._sync_data_from_api()
            return {'success': bool(result)}
        except Exception as e:
            _logger.error('Error syncing device %s: %s', device_id, str(e))
            return {'success': False, 'error': str(e)}
