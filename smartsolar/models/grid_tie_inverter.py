# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from datetime import datetime


class GridTieInverter(models.Model):
    _name = 'grid.tie.inverter'
    _description = 'Dữ liệu Grid Tie Inverter từ thiết bị'
    _order = 'server_time desc, create_date desc'

    # Thông tin thiết bị
    device_guid = fields.Char(string='Device GUID', required=True, index=True)
    device_type = fields.Integer(string='Loại thiết bị')
    is_online = fields.Boolean(string='Trạng thái online', default=False)

    # Thời gian
    server_time = fields.Float(string='Server Time', digits=(16, 6))
    last_updated = fields.Float(string='Last Updated', digits=(16, 6))
    timestamp = fields.Integer(string='Timestamp')
    record_date = fields.Datetime(string='Ngày ghi nhận', default=fields.Datetime.now, index=True)

    # Thông tin từ lastMessage
    command = fields.Char(string='Command')
    esp_id = fields.Char(string='ESP ID')
    firmware_version = fields.Char(string='Phiên bản firmware')
    signal_quality = fields.Integer(string='Chất lượng tín hiệu')
    messages_counter = fields.Integer(string='Bộ đếm tin nhắn')

    # Dữ liệu từ dataStreams - Điện áp
    dc_voltage = fields.Float(string='Điện áp DC (V)', digits=(16, 3))
    ac_voltage = fields.Float(string='Điện áp AC (V)', digits=(16, 3))

    # Dữ liệu công suất và năng lượng
    output_power = fields.Float(string='Công suất đầu ra (W)', digits=(16, 3))
    limiter_power = fields.Float(string='Công suất giới hạn (W)', digits=(16, 3))
    total_power = fields.Float(string='Tổng công suất (W)', digits=(16, 3))

    # Dữ liệu năng lượng
    energy_today = fields.Float(string='Năng lượng hôm nay (kWh)', digits=(16, 3))
    energy_total = fields.Float(string='Tổng năng lượng (kWh)', digits=(16, 3))
    limiter_today = fields.Float(string='Giới hạn hôm nay (kWh)', digits=(16, 3))
    limiter_total = fields.Float(string='Tổng giới hạn (kWh)', digits=(16, 3))

    # Dữ liệu khác
    temperature = fields.Float(string='Nhiệt độ (°C)', digits=(16, 1))
    status = fields.Char(string='Trạng thái Inverter')

    # Quan hệ
    system_id = fields.Many2one('smartsolar.system', string='Hệ thống', ondelete='cascade')
    device_id = fields.Many2one('smartsolar.device', string='Thiết bị', ondelete='cascade')
    company_id = fields.Many2one('res.company', string='Công ty', related='system_id.company_id', store=True)

    # Computed fields
    server_time_datetime = fields.Datetime(string='Server Time (Datetime)', compute='_compute_server_time_datetime', store=False)

    @api.depends('server_time')
    def _compute_server_time_datetime(self):
        for record in self:
            if record.server_time:
                try:
                    record.server_time_datetime = datetime.fromtimestamp(record.server_time)
                except (ValueError, OSError):
                    record.server_time_datetime = False
            else:
                record.server_time_datetime = False

    @api.depends('device_guid', 'record_date')
    def _compute_display_name(self):
        for record in self:
            name = record.device_guid or 'N/A'
            if record.record_date:
                name += f" - {record.record_date.strftime('%Y-%m-%d %H:%M:%S')}"
            record.display_name = name

    @api.model
    def create_from_api_data(self, api_data, device_guid=None, system_id=None, device_id=None):
        last_message = api_data.get('lastMessage', {})
        data_streams = last_message.get('dataStreams', [])

        stream_dict = {item.get('name'): item.get('value') for item in data_streams}

        server_time = api_data.get('serverTime', 0)
        try:
            record_date = datetime.utcfromtimestamp(server_time) if server_time else datetime.utcnow()
        except (ValueError, OSError, OverflowError):
            record_date = datetime.utcnow()

        values = {
            'device_guid': device_guid or api_data.get('deviceGuid', ''),
            'device_type': api_data.get('deviceType', 0),
            'is_online': api_data.get('isOnline', False),
            'server_time': server_time,
            'last_updated': api_data.get('lastUpdated', 0),
            'record_date': record_date,
            'command': last_message.get('command', ''),
            'esp_id': last_message.get('espId', ''),
            'timestamp': last_message.get('timeStamp', 0),
            'firmware_version': last_message.get('firmwareVersion', ''),
            'signal_quality': last_message.get('signalQuality', 0),
            'messages_counter': last_message.get('messagesCounter', 0),
            'dc_voltage': stream_dict.get('dc_voltage', 0.0),
            'ac_voltage': stream_dict.get('ac_voltage', 0.0),
            'output_power': stream_dict.get('output_power', 0.0),
            'limiter_power': stream_dict.get('limmiter_power', stream_dict.get('limiter_power', 0.0)),
            'temperature': stream_dict.get('temperature', 0.0),
            'energy_today': stream_dict.get('energy_today', 0.0),
            'energy_total': stream_dict.get('energy_total', 0.0),
            'total_power': stream_dict.get('total_power', 0.0),
            'limiter_today': stream_dict.get('limmiter_today', stream_dict.get('limiter_today', 0.0)),
            'limiter_total': stream_dict.get('limmiter_total', stream_dict.get('limiter_total', 0.0)),
            'status': str(stream_dict.get('status', '')) if stream_dict.get('status') is not None else '',
            'system_id': system_id,
            'device_id': device_id,
        }

        return self.create(values)
