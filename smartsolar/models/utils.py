# -*- coding: utf-8 -*-
import time


def detect_mqsolar_device_type(raw_data):
    """Return the SmartSolar Odoo device_type for an MQSolar websocket message."""
    payload = raw_data.get('payload') or {}
    topic = raw_data.get('topic') or ''

    if 'grid_tie_inverter' in topic or 'dc_voltage' in payload:
        return 'grid_tie_inverter'
    if 'mppt_charger' in topic or 'pv_voltage' in payload:
        return 'charge_power'
    return False


def _stream(name, value):
    return {
        'name': name,
        'value': 0.0 if value is None else value,
    }


def mqsolar_message_to_legacy_api_data(raw_data):
    """Convert the new MQSolar websocket payload to the module's legacy API shape."""
    if not isinstance(raw_data, dict):
        return {}

    payload = raw_data.get('payload') or {}
    device_id = raw_data.get('deviceId')
    if not device_id or not payload:
        return {}

    topic = raw_data.get('topic') or ''
    device_type = detect_mqsolar_device_type(raw_data)
    received_at = raw_data.get('_received_at') or time.time()

    if device_type == 'grid_tie_inverter':
        output_power = payload.get('output_power')
        limiter_power = payload.get('limmiter_power')
        total_power = payload.get('total_power')
        if total_power is None and output_power is not None and limiter_power is not None:
            try:
                total_power = float(output_power) + float(limiter_power)
            except (TypeError, ValueError):
                total_power = None
        streams = [
            _stream('dc_voltage', payload.get('dc_voltage')),
            _stream('ac_voltage', payload.get('ac_voltage')),
            _stream('output_power', output_power),
            _stream('limmiter_power', limiter_power),
            _stream('limmiter_today', payload.get('limmiter_today')),
            _stream('limmiter_total', payload.get('limmiter_total')),
            _stream('temperature', payload.get('temperature')),
            _stream('energy_today', payload.get('energy_today')),
            _stream('energy_total', payload.get('energy_total')),
            _stream('total_power', total_power),
            _stream('status', payload.get('status') or payload.get('inverter_status')),
        ]
        device_type_value = 1
    elif device_type == 'charge_power':
        streams = [
            _stream('pv_voltage', payload.get('pv_voltage')),
            _stream('pv_current', payload.get('pv_current')),
            _stream('bat_voltage', payload.get('bat_voltage')),
            _stream('bat_current', payload.get('bat_current')),
            _stream('charge_power', payload.get('charge_power')),
            _stream('today_kwh', payload.get('today_kwh')),
            _stream('total_kwh', payload.get('total_kwh')),
            _stream('temperature', payload.get('temperature')),
            _stream('status', payload.get('status')),
        ]
        device_type_value = 2
    else:
        streams = []
        device_type_value = 0

    return {
        'deviceGuid': str(device_id),
        'deviceType': device_type_value,
        'isOnline': True,
        'serverTime': received_at,
        'lastUpdated': received_at,
        'lastMessage': {
            'command': topic,
            'espId': str(device_id),
            'timeStamp': int(received_at),
            'firmwareVersion': payload.get('firmware_version') or payload.get('firmwareVersion') or '',
            'signalQuality': payload.get('signal_quality') or payload.get('signalQuality') or 0,
            'messagesCounter': payload.get('messages_counter') or payload.get('messagesCounter') or 0,
            'dataStreams': streams,
        },
    }
