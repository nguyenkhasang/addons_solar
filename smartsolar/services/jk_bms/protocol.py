"""JK02_32S layout verified with JK_PB1A16S10P 19A/19.10.
Reference: syssi/esphome-jk-bms/components/jk_bms_ble/jk_bms_ble.cpp.
No configuration command builder is exposed.
"""
import math
import struct
from datetime import datetime, timezone

HEADER = bytes.fromhex('55aaeb90')


def read_request(command):
    if command not in (0x96, 0x97):
        raise ValueError('Only read status/device-info requests are allowed')
    frame = bytearray.fromhex('aa5590eb') + bytearray([command]) + bytearray(15)
    frame[19] = sum(frame[:19]) & 255
    return bytes(frame)


class FrameAssembler:
    def __init__(self):
        self.buffer = bytearray()
        self.invalid_frames = 0

    def feed(self, packet):
        self.buffer.extend(packet)
        frames = []
        while True:
            start = self.buffer.find(HEADER)
            if start < 0:
                self.buffer[:] = self.buffer[-3:]
                break
            del self.buffer[:start]
            if len(self.buffer) < 300:
                break
            frame = bytes(self.buffer[:300])
            if sum(frame[:299]) & 255 != frame[299]:
                self.invalid_frames += 1
                del self.buffer[0]
                continue
            del self.buffer[:300]
            frames.append(frame)
        return frames


def device_info(frame):
    validate_frame(frame, 3)
    def text(start, end):
        return frame[start:end].split(b'\0')[0].decode('ascii', errors='replace')
    # Never expose passcodes or unfiltered device-info frames.
    return {'bms_model': text(6, 22), 'hardware': text(22, 30),
            'firmware': text(30, 38), 'ble_name': text(46, 62)}


def validate_frame(frame, kind):
    if len(frame) != 300 or frame[:4] != HEADER or frame[4] != kind:
        raise ValueError('Unexpected JK frame length/type')
    if sum(frame[:299]) & 255 != frame[299]:
        raise ValueError('JK frame checksum mismatch')


def decode_telemetry(frame, timestamp=None):
    validate_frame(frame, 2)
    def value(fmt, offset, scale=1):
        return struct.unpack_from('<' + fmt, frame, offset)[0] * scale
    mask = value('I', 70)
    cells = [value('H', 6 + i * 2, .001) for i in range(32) if mask & (1 << i)]
    # This deployment is deliberately fail-closed for the verified 16S pack/layout.
    if mask != 0xffff or len(cells) != 16:
        raise ValueError('Expected verified 16S JK02_32S layout')
    def temperature(offset):
        raw = value('h', offset)
        return None if raw in (-32768, 32767) else raw * .1
    state = {
        'timestamp': timestamp or datetime.now(timezone.utc).isoformat(),
        'voltage': value('I', 150, .001), 'current': value('i', 158, .001),
        'soc': frame[173], 'remaining_capacity': value('I', 174, .001),
        'nominal_capacity': value('I', 178, .001), 'cycle_count': value('I', 182),
        'cycle_capacity': value('I', 186, .001), 'cells': cells,
        'mos_temperature': temperature(144),
        'battery_temperature_1': temperature(162),
        'battery_temperature_2': temperature(164),
        'charge_mos': bool(frame[198]), 'discharge_mos': bool(frame[199]),
        'balancing': bool(frame[172]), 'balance_current': value('h', 170, .001),
        'alarm_bits': value('I', 166),
    }
    state['power'] = state['voltage'] * state['current']
    state.update(cell_min_voltage=min(cells), cell_max_voltage=max(cells),
                 cell_min_index=cells.index(min(cells)) + 1,
                 cell_max_index=cells.index(max(cells)) + 1,
                 cell_delta_voltage=max(cells) - min(cells),
                 average_cell_voltage=sum(cells) / len(cells))
    validate_state(state)
    return state


def validate_state(state):
    cells = state.get('cells')
    if not isinstance(cells, list) or len(cells) != 16:
        raise ValueError('Exactly 16 cell voltages required')
    for key, low, high in [('voltage', 20, 70), ('current', -500, 500),
                           ('soc', 0, 100), ('nominal_capacity', 1, 1000),
                           ('remaining_capacity', 0, 1000)]:
        x = state.get(key)
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or not low <= x <= high:
            raise ValueError('Invalid battery field: ' + key)
    if any(isinstance(c, bool) or not isinstance(c, (float, int)) or not math.isfinite(c) or not 1 <= c <= 4.5 for c in cells):
        raise ValueError('Invalid LFP cells')
    if abs(sum(cells) - state['voltage']) > 1:
        raise ValueError('Pack voltage inconsistent with cell sum/layout')
    for key in ('mos_temperature', 'battery_temperature_1', 'battery_temperature_2'):
        x = state.get(key)
        if x is not None and (not isinstance(x, (int, float)) or not math.isfinite(x) or not -50 <= x <= 150):
            raise ValueError('Invalid temperature')
    for key in ('cycle_count', 'cycle_capacity', 'alarm_bits'):
        x = state.get(key)
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0:
            raise ValueError('Invalid battery field: ' + key)
    x = state.get('balance_current')
    if not isinstance(x, (int, float)) or not math.isfinite(x) or abs(x) > 20:
        raise ValueError('Invalid balance current')
    for key in ('charge_mos', 'discharge_mos', 'balancing'):
        if type(state.get(key)) is not bool:
            raise ValueError('Invalid status: ' + key)
    stamp = datetime.fromisoformat(state['timestamp'])
    if stamp.tzinfo is None:
        raise ValueError('Timestamp must include timezone')
    return stamp.astimezone(timezone.utc).replace(tzinfo=None)


def connection_status(age, stale_seconds=10, offline_seconds=60):
    if age is None or age > offline_seconds:
        return 'offline'
    return 'stale' if age >= stale_seconds else 'online'
