"""Offline reconnect and fail-closed tests without touching any Bluetooth adapter."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, main
from unittest.mock import patch, AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services'))
from jk_bms import transport
from jk_bms.protocol import read_request
RAW = bytes.fromhex((Path(__file__).parent / 'fixtures/jk_pb1a16s10p_19_10.hex').read_text())


def info_frame(model='JK_PB1A16S10P'):
    frame = bytearray(300); frame[:4] = bytes.fromhex('55aaeb90'); frame[4] = 3
    frame[6:6 + len(model)] = model.encode(); frame[30:35] = b'19.10'
    frame[299] = sum(frame[:299]) & 255
    return frame


class TestTransport(IsolatedAsyncioTestCase):
    async def test_cached_recovery_requires_exact_address_connected_and_service(self):
        from dbus_fast import MessageType
        variant = lambda value: SimpleNamespace(value=value)
        props = {'Address': variant('AA:BB:CC:DD:EE:FF'), 'Name': variant('JK cached'),
                 'Connected': variant(True), 'UUIDs': variant([transport.SERVICE])}
        bus = SimpleNamespace()
        bus.connect = AsyncMock(return_value=bus)
        bus.call = AsyncMock(return_value=SimpleNamespace(message_type=MessageType.METHOD_RETURN,
            body=[{'/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF': {'org.bluez.Device1': props}}]))
        from unittest.mock import Mock
        bus.disconnect = Mock()
        with patch('dbus_fast.aio.MessageBus', return_value=bus):
            device = await transport.cached_connected_device('aa:bb:cc:dd:ee:ff')
            self.assertEqual(device.address, 'AA:BB:CC:DD:EE:FF')
            self.assertEqual(device.details['path'], '/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF')
            self.assertIsNone(await transport.cached_connected_device('OTHER'))
            props['Connected'] = variant(False)
            self.assertIsNone(await transport.cached_connected_device('AA:BB:CC:DD:EE:FF'))
            props['Connected'] = variant(True); props['UUIDs'] = variant([])
            self.assertIsNone(await transport.cached_connected_device('AA:BB:CC:DD:EE:FF'))
        self.assertEqual(bus.disconnect.call_count, 4)

    async def test_disconnect_resubscribes_with_read_commands_only(self):
        stop = asyncio.Event(); clients = []; samples = []
        loop = asyncio.get_running_loop()
        char = SimpleNamespace(uuid=transport.CHAR, properties=['notify', 'write-without-response'])
        class Client:
            def __init__(self, device, timeout, disconnected_callback):
                self.disconnect = disconnected_callback; self.requests = []; clients.append(self)
                self.services = [SimpleNamespace(uuid=transport.SERVICE, characteristics=[char])]
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def start_notify(self, c, callback): self.callback = callback
            async def write_gatt_char(self, c, frame, response):
                self.requests.append(frame)
                if frame[4] == 0x97: self.callback(c, info_frame())
                if frame[4] == 0x96:
                    self.callback(c, RAW[:80]); self.callback(c, RAW[80:])
        def publish(state):
            samples.append(state)
            if len(samples) == 1: loop.call_soon_threadsafe(clients[-1].disconnect, None)
            else: loop.call_soon_threadsafe(stop.set)
        fake_bleak = SimpleNamespace(BleakClient=Client)
        device = SimpleNamespace(name='fixture', address='TEST')
        with patch.dict(sys.modules, {'bleak':fake_bleak}), patch.object(transport,'discover',AsyncMock(return_value=device)):
            await asyncio.wait_for(transport.run(stop, publish), timeout=10)
        self.assertEqual(len(clients), 2)
        self.assertEqual(len(samples), 2)
        for c in clients: self.assertEqual(c.requests,[read_request(0x97),read_request(0x96)])


if __name__ == '__main__': main()
