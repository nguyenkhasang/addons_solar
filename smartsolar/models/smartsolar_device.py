# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _

from .utils import (
    detect_mqsolar_device_type,
    mqsolar_message_to_legacy_api_data,
)

_logger = logging.getLogger(__name__)


class SmartSolarDevice(models.Model):
    _name = 'smartsolar.device'
    _description = 'Thiet bi Smart Solar'
    _order = 'device_guid'

    device_guid = fields.Char(string='Device GUID', required=True, index=True)
    device_type = fields.Selection([
        ('grid_tie_inverter', 'Grid Tie Inverter'),
        ('charge_power', 'Charge Power'),
    ], string='Loai thiet bi', default='grid_tie_inverter')
    name = fields.Char(string='Ten thiet bi')
    active = fields.Boolean(string='Kich hoat', default=True)

    system_id = fields.Many2one('smartsolar.system', string='He thong', ondelete='cascade')
    company_id = fields.Many2one('res.company', string='Cong ty', related='system_id.company_id', store=True)

    is_online = fields.Boolean(string='Trang thai online', default=False)
    firmware_version = fields.Char(string='Phien ban firmware')
    last_sync_date = fields.Datetime(string='Lan dong bo cuoi')
    last_sync_status = fields.Selection([
        ('success', 'Thanh cong'),
        ('auth_failed', 'Loi xac thuc'),
        ('network_error', 'Loi mang'),
        ('api_error', 'Loi API'),
    ], string='Trang thai sync cuoi', readonly=True)
    last_sync_error = fields.Char(string='Loi sync cuoi', readonly=True)

    charge_power_ids = fields.One2many('charge.power', 'device_id', string='Lich su Charge Power')
    charge_power_count = fields.Integer(string='So luong Charge Power', compute='_compute_data_count')
    grid_tie_inverter_ids = fields.One2many('grid.tie.inverter', 'device_id', string='Lich su Grid Tie Inverter')
    grid_tie_inverter_count = fields.Integer(string='So luong Grid Tie Inverter', compute='_compute_data_count')

    _sql_constraints = [
        ('device_guid_unique', 'unique(device_guid)', 'Device GUID phai la duy nhat!')
    ]

    @api.depends('charge_power_ids', 'grid_tie_inverter_ids')
    def _compute_data_count(self):
        for record in self:
            record.charge_power_count = len(record.charge_power_ids)
            record.grid_tie_inverter_count = len(record.grid_tie_inverter_ids)

    @api.depends('device_guid', 'name')
    def _compute_display_name(self):
        for record in self:
            if record.name:
                record.display_name = f"{record.name} ({record.device_guid or ''})"
            else:
                record.display_name = record.device_guid or ''

    def _record_sync_result(self, status, error=None):
        try:
            self.write({
                'last_sync_status': status,
                'last_sync_error': (error or '')[:500] if error else False,
            })
        except Exception:
            _logger.exception('Khong ghi duoc last_sync_status cho device %s', self.device_guid)

    def _sync_data_from_api(self):
        """Backward-compatible entry point. Data is now synced only through MQSolar websocket."""
        self.ensure_one()
        if not self.system_id:
            self._record_sync_result('api_error', 'Thieu he thong')
            return False
        return self.system_id._sync_devices_from_mqsolar_websocket(devices=self)

    def _sync_data_from_mqsolar_message(self, raw_data):
        self.ensure_one()
        try:
            api_data = mqsolar_message_to_legacy_api_data(raw_data)
            if not api_data:
                self._record_sync_result('api_error', 'Payload websocket khong hop le')
                return False

            detected_type = detect_mqsolar_device_type(raw_data)
            if detected_type and self.device_type != detected_type:
                self.device_type = detected_type

            target_type = detected_type or self.device_type
            if target_type == 'grid_tie_inverter':
                self.env['grid.tie.inverter'].create_from_api_data(
                    api_data,
                    device_guid=self.device_guid,
                    system_id=self.system_id.id if self.system_id else None,
                    device_id=self.id,
                )
            elif target_type == 'charge_power':
                self.env['charge.power'].create_from_api_data(
                    api_data,
                    device_guid=self.device_guid,
                    system_id=self.system_id.id if self.system_id else None,
                    device_id=self.id,
                )
            else:
                self._record_sync_result('api_error', 'Khong nhan dien duoc loai thiet bi')
                return False

            last_message = api_data.get('lastMessage', {}) or {}
            self.write({
                'is_online': True,
                'firmware_version': last_message.get('firmwareVersion', ''),
                'last_sync_date': fields.Datetime.now(),
                'last_sync_status': 'success',
                'last_sync_error': False,
            })
            return True
        except Exception as e:
            _logger.error('Error syncing MQSolar websocket device %s: %s', self.device_guid, str(e), exc_info=True)
            self._record_sync_result('api_error', str(e))
            return False

    def _build_realtime_payload(self, api_data, device_type):
        """Xây dựng payload gọn để push lên bus.bus cho realtime chart."""
        from datetime import timezone, timedelta
        _UTC7 = timezone(timedelta(hours=7))
        import time as _time

        last_message = api_data.get('lastMessage', {}) or {}
        streams = {s.get('name'): s.get('value', 0) for s in last_message.get('dataStreams', [])}
        received_at = api_data.get('serverTime') or _time.time()
        try:
            from datetime import datetime
            ts = datetime.fromtimestamp(received_at, tz=timezone.utc).astimezone(_UTC7)
            label = ts.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            label = ''

        base = {
            'device_guid': self.device_guid,
            'device_id': self.id,
            'system_id': self.system_id.id if self.system_id else 0,
            'device_type': device_type,
            'label': label,
            'temperature': streams.get('temperature', 0),
        }

        if device_type == 'grid_tie_inverter':
            base.update({
                'output_power': streams.get('output_power', 0),
                'limiter_power': streams.get('limmiter_power', 0),
                'dc_voltage': streams.get('dc_voltage', 0),
                'ac_voltage': streams.get('ac_voltage', 0),
                'total_power': streams.get('total_power', 0),
            })
        elif device_type == 'charge_power':
            base.update({
                'charge_power': streams.get('charge_power', 0),
                'pv_voltage': streams.get('pv_voltage', 0),
                'pv_current': streams.get('pv_current', 0),
                'bat_voltage': streams.get('bat_voltage', 0),
                'bat_current': streams.get('bat_current', 0),
                'pv_input_power': round(
                    streams.get('pv_voltage', 0) * streams.get('pv_current', 0), 2
                ),
            })
        return base

    def action_sync_data(self):
        self.ensure_one()
        result = self._sync_data_from_api()

        if result:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Thanh cong'),
                    'message': _('Dong bo du lieu thanh cong!'),
                    'type': 'success',
                    'sticky': False,
                }
            }
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Loi'),
                'message': _('Co loi xay ra khi dong bo du lieu. Vui long kiem tra log.'),
                'type': 'danger',
                'sticky': True,
            }
        }

    def action_view_charge_power(self):
        self.ensure_one()
        return {
            'name': _('Lich su Charge Power'),
            'type': 'ir.actions.act_window',
            'res_model': 'charge.power',
            'view_mode': 'list,form,graph,pivot',
            'domain': [('device_id', '=', self.id)],
            'context': {'default_device_id': self.id},
        }

    def action_view_grid_tie_inverter(self):
        self.ensure_one()
        return {
            'name': _('Lich su Grid Tie Inverter'),
            'type': 'ir.actions.act_window',
            'res_model': 'grid.tie.inverter',
            'view_mode': 'list,form,graph,pivot',
            'domain': [('device_id', '=', self.id)],
            'context': {'default_device_id': self.id},
        }

    @api.model
    def _sync_all_devices(self):
        devices = self.search([('active', '=', True)])
        for device in devices:
            try:
                device._sync_data_from_api()
            except Exception as e:
                _logger.error('Error syncing device %s: %s', device.device_guid, str(e), exc_info=True)
        return True
