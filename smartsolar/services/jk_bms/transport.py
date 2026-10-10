"""Persistent BLE connection; only fixed zero-payload read requests exist."""
import asyncio
import logging
import time
from datetime import datetime, timezone
from .protocol import FrameAssembler, read_request, device_info, decode_telemetry

_logger = logging.getLogger(__name__)
SERVICE = '0000ffe0-0000-1000-8000-00805f9b34fb'
CHAR = '0000ffe1-0000-1000-8000-00805f9b34fb'


class CollectorLockLost(Exception):
    pass


async def cached_connected_device(selector):
    """BlueZ can retain a BLE link after SIGKILL, so no advertisement is emitted.

    Reuse only the exact configured, connected device with the expected service.
    No pairing/trust/configuration mutations are made.
    """
    if not selector:
        return None
    from dbus_fast import BusType, Message, MessageType
    from dbus_fast.aio import MessageBus
    from bleak.backends.device import BLEDevice
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        reply = await bus.call(Message(destination='org.bluez', path='/',
            interface='org.freedesktop.DBus.ObjectManager', member='GetManagedObjects'))
        if reply.message_type == MessageType.ERROR:
            return None
        for path, interfaces in reply.body[0].items():
            device = interfaces.get('org.bluez.Device1')
            if not device:
                continue
            props = {key: value.value for key, value in device.items()}
            if (str(props.get('Address', '')).lower() == selector.lower()
                    and props.get('Connected') and SERVICE in props.get('UUIDs', [])):
                _logger.info('Reusing connected BlueZ device after collector restart')
                return BLEDevice(props['Address'], props.get('Name'), {'path': path, 'props': props})
        return None
    finally:
        bus.disconnect()


async def discover(selector=None, timeout=15):
    from bleak import BleakScanner
    cached = await cached_connected_device(selector)
    if cached is not None:
        return cached
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    candidates = [(d, a) for d, a in found.values() if SERVICE in a.service_uuids]
    if selector:
        candidates = [(d, a) for d, a in candidates if selector.lower() in
                      (d.address.lower(), (a.local_name or d.name or '').lower())]
    if len(candidates) > 1:
        raise ValueError('Multiple FFE0 devices: configure JK_BMS_DEVICE explicitly')
    return candidates[0][0] if candidates else None


async def run(stop, publish, selector=None, read_interval=2, timeout=60, debug=False):
    from bleak import BleakClient
    retry = 1
    while not stop.is_set():
        try:
            device = await discover(selector)
            if device is None:
                raise TimeoutError('Selected BMS is not advertising')
            _logger.info('JK candidate discovered name=%s address=%s', device.name, device.address)
            disconnected = asyncio.Event()
            async with BleakClient(device, timeout=25,
                                   disconnected_callback=lambda _: disconnected.set()) as client:
                _logger.info('BLE connected')
                chars = [c for s in client.services if s.uuid == SERVICE
                         for c in s.characteristics if c.uuid == CHAR]
                notify = next(c for c in chars if 'notify' in c.properties)
                write = next(c for c in chars if 'write-without-response' in c.properties)
                assembler = FrameAssembler()
                info = {}
                latest = None
                last_seen = time.monotonic()
                publish_retry = read_interval
                next_publish = 0
                frames = asyncio.Queue(maxsize=8)
                def received(_, data):
                    # No Odoo/database work in the BLE callback, bounded memory.
                    for frame in assembler.feed(data):
                        if frames.full():
                            frames.get_nowait()
                        frames.put_nowait((frame, datetime.now(timezone.utc).isoformat()))
                await client.start_notify(notify, received)
                for command in (0x97, 0x96):
                    await client.write_gatt_char(write, read_request(command), response=False)
                    await asyncio.sleep(.5)
                while not stop.is_set() and not disconnected.is_set():
                    try:
                        frame, received_at = await asyncio.wait_for(frames.get(), timeout=1)
                    except asyncio.TimeoutError:
                        if time.monotonic() - last_seen > timeout:
                            raise TimeoutError('Telemetry deadline exceeded')
                        continue
                    if frame[4] == 3:
                        info = device_info(frame)
                        _logger.info('Device info model=%s hardware=%s firmware=%s',
                                     info['bms_model'], info['hardware'], info['firmware'])
                        if info['bms_model'] != 'JK_PB1A16S10P' or info['firmware'] != '19.10':
                            raise ValueError('Unverified model/firmware; refusing telemetry layout')
                    elif frame[4] == 2 and info:
                        if debug:
                            _logger.debug('RX telemetry %s', frame.hex(' '))
                        latest = decode_telemetry(frame, timestamp=received_at)
                        last_seen = time.monotonic()
                        retry = 1
                        if last_seen >= next_publish:
                            latest.update(info, device_identifier=device.address)
                            # Failure retains only newest state; next sample retries after Odoo recovers.
                            try:
                                await asyncio.to_thread(publish, latest)
                                publish_retry = read_interval
                                next_publish = last_seen + read_interval
                            except CollectorLockLost:
                                raise
                            except Exception as error:
                                next_publish = last_seen + publish_retry
                                publish_retry = min(publish_retry * 2, 30)
                                _logger.warning('Odoo publish failed reason=%s; retry newest state', type(error).__name__)
                _logger.warning('BLE disconnected or stopping')
        except CollectorLockLost:
            raise
        except Exception as error:
            _logger.warning('BLE reconnect reason=%s backoff=%ss', type(error).__name__, retry)
        try:
            await asyncio.wait_for(stop.wait(), timeout=retry)
        except asyncio.TimeoutError:
            pass
        retry = min(retry * 2, 60)
