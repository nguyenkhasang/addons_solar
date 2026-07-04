# -*- coding: utf-8 -*-
from datetime import timedelta
from urllib.parse import urlencode, quote
import json
import logging
import time

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class SmartSolarSystem(models.Model):
    _name = 'smartsolar.system'
    _description = 'He thong nang luong mat troi'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name'

    name = fields.Char(string='Ten he thong', required=True, index=True, tracking=True)
    code = fields.Char(string='Ma he thong', required=True, index=True, tracking=True)
    active = fields.Boolean(string='Kich hoat', default=True)

    installation_date = fields.Date(string='Ngay lap dat')
    location = fields.Char(string='Vi tri lap dat')
    capacity = fields.Float(string='Cong suat (kW)', digits=(16, 2))
    description = fields.Text(string='Mo ta')

    state = fields.Selection([
        ('draft', 'Nhap'),
        ('installed', 'Da lap dat'),
        ('operating', 'Dang van hanh'),
        ('maintenance', 'Bao tri'),
        ('inactive', 'Ngung hoat dong'),
    ], string='Trang thai', default='draft', tracking=True)

    company_id = fields.Many2one('res.company', string='Cong ty', default=lambda self: self.env.company)
    user_id = fields.Many2one('res.users', string='Nguoi phu trach', default=lambda self: self.env.user)
    device_ids = fields.One2many('smartsolar.device', 'system_id', string='Thiet bi')
    device_count = fields.Integer(string='So luong thiet bi', compute='_compute_device_count')

    mqsolar_ws_url = fields.Char(
        string='MQSolar WebSocket URL',
        default='wss://api.manhquansolar.io.vn/ws',
        required=True,
        help='Endpoint websocket MQSolar. Token duoc them vao query string token=...',
    )
    mqsolar_cloud_token = fields.Char(string='MQSolar Cloud Token')
    mqsolar_ws_listen_seconds = fields.Integer(
        string='Thoi gian lang nghe WS (giay)',
        default=20,
        help='Moi lan sync se subscribe device va nghe websocket trong khoang thoi gian nay.',
    )

    _sql_constraints = [
        ('code_unique', 'unique(code)', 'Ma he thong phai la duy nhat!')
    ]

    @api.depends('device_ids')
    def _compute_device_count(self):
        for record in self:
            record.device_count = len(record.device_ids)

    def action_open_devices(self):
        self.ensure_one()
        return {
            'name': _('Thiet bi Smart Solar'),
            'type': 'ir.actions.act_window',
            'res_model': 'smartsolar.device',
            'view_mode': 'tree,form',
            'domain': [('system_id', '=', self.id)],
            'context': {'default_system_id': self.id},
        }

    @api.constrains('capacity')
    def _check_capacity(self):
        for record in self:
            if record.capacity and record.capacity <= 0:
                raise ValidationError(_('Cong suat phai lon hon 0!'))

    @api.depends('name', 'code')
    def _compute_display_name(self):
        for record in self:
            if record.code:
                record.display_name = f"[{record.code}] {record.name or ''}"
            else:
                record.display_name = record.name or ''

    def action_install(self):
        self.write({'state': 'installed'})

    def action_start_operating(self):
        self.write({'state': 'operating'})

    def action_maintenance(self):
        self.write({'state': 'maintenance'})

    def action_inactive(self):
        self.write({'state': 'inactive'})

    def action_draft(self):
        self.write({'state': 'draft'})

    def _build_mqsolar_ws_url(self):
        self.ensure_one()
        base_url = self.mqsolar_ws_url or 'wss://api.manhquansolar.io.vn/ws'
        token = self.mqsolar_cloud_token or ''

        if '{token}' in base_url:
            return base_url.format(token=quote(token, safe=''))
        if 'token=' in base_url:
            return base_url

        separator = '&' if '?' in base_url else '?'
        return f'{base_url}{separator}{urlencode({"token": token})}'

    def action_sync_mqsolar_websocket(self):
        self.ensure_one()
        result = self._sync_devices_from_mqsolar_websocket()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('MQSolar WebSocket'),
                'message': _('Dong bo WebSocket thanh cong') if result else _('Khong nhan duoc du lieu WebSocket'),
                'type': 'success' if result else 'warning',
                'sticky': False,
            }
        }

    def _sync_devices_from_mqsolar_websocket(self, devices=None):
        """Connect to MQSolar websocket once and create raw records for received messages."""
        self.ensure_one()
        devices = devices or self.device_ids.filtered(lambda d: d.active)
        devices = devices.filtered(lambda d: d.device_guid)
        if not devices:
            _logger.warning('System %s: khong co device active de subscribe websocket', self.name)
            return False

        if not self.mqsolar_cloud_token:
            for device in devices:
                device._record_sync_result('auth_failed', 'Thieu MQSolar Cloud Token')
            return False

        try:
            import websocket
        except ImportError:
            msg = 'Missing python dependency: websocket-client'
            _logger.error(msg)
            for device in devices:
                device._record_sync_result('api_error', msg)
            return False

        ws = None
        received_count = 0
        devices_by_guid = {str(device.device_guid): device for device in devices}
        device_guids = list(devices_by_guid.keys())
        listen_seconds = max(1, min(int(self.mqsolar_ws_listen_seconds or 20), 55))
        # Chỉ giữ message cuối cùng của mỗi device
        latest_messages = {}

        try:
            ws = websocket.create_connection(self._build_mqsolar_ws_url(), timeout=10)
            ws.send(json.dumps({
                'topic': 'subscribe',
                'payload': {
                    'devices': device_guids,
                },
            }))

            deadline = time.monotonic() + listen_seconds
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                ws.settimeout(max(1, min(5, remaining)))
                try:
                    message = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue

                if not message:
                    continue

                try:
                    data = json.loads(message)
                except ValueError:
                    _logger.warning('System %s: websocket message khong phai JSON', self.name)
                    continue

                device_guid = str(data.get('deviceId') or '')
                if not device_guid:
                    if data.get('ok') and data.get('subscribed'):
                        _logger.info('System %s: subscribed MQSolar devices %s', self.name, data.get('subscribed'))
                    continue

                if device_guid not in devices_by_guid:
                    _logger.info('System %s: bo qua deviceId khong co trong Odoo: %s', self.name, device_guid)
                    continue

                data['_received_at'] = time.time()
                latest_messages[device_guid] = data

                # Push lên bus ngay lập tức — dùng cursor riêng để commit ngay
                device = devices_by_guid[device_guid]
                try:
                    from .utils import mqsolar_message_to_legacy_api_data, detect_mqsolar_device_type
                    api_data = mqsolar_message_to_legacy_api_data(data)
                    target_type = detect_mqsolar_device_type(data) or device.device_type
                    if api_data and target_type:
                        payload = device._build_realtime_payload(api_data, target_type)
                        with self.env.registry.cursor() as bus_cr:
                            bus_env = self.env(cr=bus_cr)
                            bus_env['bus.bus']._sendone(
                                f'smartsolar.realtime.{self.id}',
                                'smartsolar_data',
                                payload,
                            )
                            bus_env['bus.bus']._sendone(
                                'smartsolar.realtime.all',
                                'smartsolar_data',
                                payload,
                            )
                except Exception as bus_err:
                    _logger.warning('Realtime bus push failed: %s', bus_err)

            # Tạo 1 record cho mỗi device sau khi hết thời gian lắng nghe
            for device_guid, data in latest_messages.items():
                device = devices_by_guid[device_guid]
                if device._sync_data_from_mqsolar_message(data):
                    received_count += 1

            return bool(received_count)

        except Exception as e:
            _logger.error('Error syncing MQSolar websocket system %s: %s', self.name, str(e), exc_info=True)
            for device in devices:
                device._record_sync_result('network_error', str(e))
            return False
        finally:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass

    @api.model
    def _sync_all_systems_data(self):
        systems = self.search([
            ('active', '=', True),
            ('device_ids', '!=', False),
        ])
        for system in systems:
            try:
                system._sync_devices_from_mqsolar_websocket()
            except Exception as e:
                _logger.error('Error syncing system %s (ID: %s): %s', system.name, system.id, str(e), exc_info=True)
        return True

    @api.model
    def _cron_aggregate_hourly(self):
        try:
            self.env['charge.power.summary']._aggregate_hourly()
        except Exception as e:
            _logger.error('Aggregate hourly charge.power failed: %s', e, exc_info=True)
        try:
            self.env['grid.tie.inverter.summary']._aggregate_hourly()
        except Exception as e:
            _logger.error('Aggregate hourly grid.tie.inverter failed: %s', e, exc_info=True)
        return True

    @api.model
    def _cron_aggregate_daily(self):
        try:
            self.env['charge.power.summary']._aggregate_daily()
        except Exception as e:
            _logger.error('Aggregate daily charge.power failed: %s', e, exc_info=True)
        try:
            self.env['grid.tie.inverter.summary']._aggregate_daily()
        except Exception as e:
            _logger.error('Aggregate daily grid.tie.inverter failed: %s', e, exc_info=True)
        return True

    @api.model
    def _cron_purge_old_data(self):
        ICP = self.env['ir.config_parameter'].sudo()
        now = fields.Datetime.now()

        def _to_int(value, default):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        raw_days = _to_int(ICP.get_param('smartsolar.raw_retention_days', '30'), 30)
        hourly_days = _to_int(ICP.get_param('smartsolar.hourly_retention_days', '365'), 365)
        daily_days = _to_int(ICP.get_param('smartsolar.daily_retention_days', '0'), 0)

        if raw_days > 0:
            cutoff = now - timedelta(days=raw_days)
            for table in ('charge_power', 'grid_tie_inverter'):
                self.env.cr.execute(
                    f"DELETE FROM {table} WHERE record_date < %s", [cutoff]
                )
                _logger.info('[purge] %s: deleted %s rows older than %s', table, self.env.cr.rowcount, cutoff)

        if hourly_days > 0:
            cutoff = now - timedelta(days=hourly_days)
            for table in ('charge_power_summary', 'grid_tie_inverter_summary'):
                self.env.cr.execute(
                    f"DELETE FROM {table} WHERE bucket_type = 'hour' AND bucket_start < %s",
                    [cutoff],
                )
                _logger.info('[purge] %s hourly: deleted %s rows older than %s', table, self.env.cr.rowcount, cutoff)

        if daily_days > 0:
            cutoff = now - timedelta(days=daily_days)
            for table in ('charge_power_summary', 'grid_tie_inverter_summary'):
                self.env.cr.execute(
                    f"DELETE FROM {table} WHERE bucket_type = 'day' AND bucket_start < %s",
                    [cutoff],
                )
                _logger.info('[purge] %s daily: deleted %s rows older than %s', table, self.env.cr.rowcount, cutoff)

        return True
