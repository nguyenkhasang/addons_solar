# -*- coding: utf-8 -*-
import logging
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class SmartSolarDashboardController(http.Controller):

    @http.route('/smartsolar/dashboard/data', type='json', auth='user', methods=['POST'])
    def dashboard_data(self, time_range='24h', system_id=None, **kwargs):
        try:
            sid = int(system_id) if system_id else None
        except (TypeError, ValueError):
            sid = None
        return request.env['smartsolar.dashboard'].get_dashboard_data(time_range=time_range, system_id=sid)

    @http.route('/smartsolar/dashboard/kpi', type='json', auth='user', methods=['POST'])
    def dashboard_kpi(self, system_id=None, **kwargs):
        try:
            sid = int(system_id) if system_id else None
        except (TypeError, ValueError):
            sid = None
        return request.env['smartsolar.dashboard'].get_overview_kpi(system_id=sid)

    @http.route('/smartsolar/realtime/poll', type='json', auth='user', methods=['POST'])
    def realtime_poll(self, last_id=0, system_id=None, timeout=25, **kwargs):
        try:
            sid = int(system_id) if system_id else None
        except (TypeError, ValueError):
            sid = None

        channel = f'smartsolar.realtime.{sid}' if sid else 'smartsolar.realtime.all'

        notifications = request.env['bus.bus']._poll(
            [channel],
            last=int(last_id or 0),
        )

        new_last_id = last_id
        messages = []
        for notif in notifications:
            new_last_id = notif['id']
            if notif.get('message', {}).get('type') == 'smartsolar_data':
                messages.append(notif['message']['payload'])

        return {
            'messages': messages,
            'last_id': new_last_id,
        }
