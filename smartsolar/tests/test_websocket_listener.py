"""Persistent lifecycle proof with a deterministic socket and clock."""
import json
from types import SimpleNamespace
from unittest import TestCase
from ..services.websocket_listener import listen


class Clock:
    def __init__(self):
        self.now = 0
    def __call__(self):
        return self.now


class Stop:
    def __init__(self, stop_after_waits=None):
        self.stopped = False
        self.waits = []
        self.stop_after_waits = stop_after_waits
    def is_set(self):
        return self.stopped
    def set(self):
        self.stopped = True
    def wait(self, seconds):
        self.waits.append(seconds)
        if self.stop_after_waits and len(self.waits) >= self.stop_after_waits:
            self.set()
        return self.stopped


class Socket:
    def __init__(self, clock, frames, step=1):
        self.clock, self.frames, self.step = clock, iter(frames), step
        self.sent, self.pings, self.closed = [], 0, False
    def send(self, raw):
        self.sent.append(json.loads(raw))
    def settimeout(self, timeout):
        self.timeout = timeout
    def ping(self):
        self.pings += 1
    def recv(self):
        self.clock.now += self.step
        value = next(self.frames)
        if isinstance(value, Exception):
            raise value
        return value
    def close(self):
        self.closed = True


def frame(guid='a'):
    return json.dumps({'deviceId': guid, 'payload': {'pv_voltage': 100}})


class TestWebsocketListener(TestCase):
    def run_loop(self, stop, clock, connect, publish, config=None):
        listen(1, stop, config or (lambda: {'url': 'wss://test', 'devices': ['a']}),
               publish, connect=connect, clock=clock, received_clock=lambda: 123,
               timeout_errors=(TimeoutError,))

    def test_connection_survives_old_twenty_second_window_and_publishes_each_frame(self):
        clock, stop, published = Clock(), Stop(), []
        sock = Socket(clock, [frame()] * 80)
        connections = []
        def connect(*args, **kwargs):
            connections.append(args)
            return sock
        def publish(data):
            published.append((clock.now, data['_received_at']))
            if len(published) == 80:
                stop.set()
            return True
        self.run_loop(stop, clock, connect, publish)
        self.assertEqual(len(connections), 1)
        self.assertEqual(published, [(i, 123) for i in range(1, 81)])
        self.assertGreater(sock.pings, 0)
        self.assertTrue(sock.closed)
        self.assertEqual(sock.sent[0]['payload']['devices'], ['a'])

    def test_disconnect_reconnects_and_resubscribes(self):
        clock, stop = Clock(), Stop()
        first = Socket(clock, [frame(), ''])
        second = Socket(clock, [frame()])
        sockets = iter([first, second])
        count = []
        def publish(data):
            count.append(data)
            if len(count) == 2:
                stop.set()
            return True
        self.run_loop(stop, clock, lambda *a, **k: next(sockets), publish)
        self.assertEqual(stop.waits, [1])
        self.assertEqual(len(second.sent), 1)
        self.assertTrue(first.closed and second.closed)

    def test_connect_failures_back_off_without_logging_secret(self):
        clock, stop = Clock(), Stop(stop_after_waits=3)
        def fail(*args, **kwargs):
            raise ConnectionError('wss://example?token=SECRET')
        with self.assertLogs('odoo.addons.smartsolar.services.websocket_listener', level='WARNING') as log:
            self.run_loop(stop, clock, fail, lambda data: True)
        self.assertEqual(stop.waits, [1, 2, 4])
        self.assertNotIn('SECRET', '\n'.join(log.output))

    def test_idle_socket_is_reconnected_even_when_ping_succeeds(self):
        clock, stop = Clock(), Stop(stop_after_waits=1)
        sock = Socket(clock, [TimeoutError()] * 20, step=5)
        self.run_loop(stop, clock, lambda *a, **k: sock, lambda data: True)
        self.assertGreaterEqual(clock.now, 60)
        self.assertTrue(sock.closed)

    def test_malformed_ack_and_unsubscribed_device_are_ignored(self):
        clock, stop, seen = Clock(), Stop(), []
        sock = Socket(clock, ['bad', '[]', '{"ok":true}', frame('other'), frame()])
        def publish(data):
            seen.append(data['deviceId'])
            stop.set()
            return True
        self.run_loop(stop, clock, lambda *a, **k: sock, publish)
        self.assertEqual(seen, ['a'])

    def test_configuration_change_reopens_subscription(self):
        clock, stop = Clock(), Stop()
        first = Socket(clock, [frame()] * 4, step=10)
        second = Socket(clock, [frame('b')])
        sockets = iter([first, second])
        def config():
            return {'url': 'wss://test', 'devices': ['a' if clock.now < 30 else 'b']}
        def publish(data):
            if data['deviceId'] == 'b':
                stop.set()
            return True
        self.run_loop(stop, clock, lambda *a, **k: next(sockets), publish, config)
        self.assertEqual(second.sent[0]['payload']['devices'], ['b'])
        self.assertTrue(first.closed)

    def test_disabled_system_closes_socket(self):
        clock, stop = Clock(), Stop()
        sock = Socket(clock, [frame()] * 4, step=10)
        def config():
            return {'url': 'wss://test', 'devices': ['a']} if clock.now < 30 else None
        self.run_loop(stop, clock, lambda *a, **k: sock, lambda data: True, config)
        self.assertTrue(sock.closed)

    def test_lost_database_lock_closes_socket_and_exits_worker(self):
        from ..services.websocket_listener import ListenerLockLost
        clock, stop = Clock(), Stop()
        sock = Socket(clock, [frame()])
        def publish(data):
            raise ListenerLockLost()
        with self.assertRaises(ListenerLockLost):
            self.run_loop(stop, clock, lambda *a, **k: sock, publish)
        self.assertTrue(sock.closed)
        self.assertEqual(stop.waits, [])

    def test_realtime_message_uses_both_channels_and_existing_payload_builder(self):
        from ..models.smartsolar_device import SmartSolarDevice
        sent = []
        bus = SimpleNamespace(_sendone=lambda *args: sent.append(args))
        fake = SimpleNamespace(ensure_one=lambda: None, env={'bus.bus': bus},
                               device_type='charge_power', system_id=SimpleNamespace(id=7),
                               _build_realtime_payload=lambda data, kind: {'kind': kind})
        raw = {'topic': 'mppt_charger', 'deviceId': 'a', 'payload': {'pv_voltage': 100}}
        self.assertTrue(SmartSolarDevice._send_mqsolar_realtime(fake, raw))
        self.assertEqual([entry[0] for entry in sent], ['smartsolar.realtime.7', 'smartsolar.realtime.all'])
        self.assertTrue(all(entry[1] == 'smartsolar_data' for entry in sent))
        self.assertFalse(SmartSolarDevice._send_mqsolar_realtime(fake, {}))
        self.assertEqual(len(sent), 2)

    def test_raw_schedule_throttles_per_device_but_does_not_drop_live_frames(self):
        from ..services.websocket_listener import RawSampleSchedule
        schedule = RawSampleSchedule()
        saved, live = [], []
        for second in range(11):
            for device in ['a', 'b']:
                live.append((device, second))
                if schedule.due(device, second):
                    saved.append((device, second))
                    schedule.committed(device, second)
        self.assertEqual(len(live), 22)
        self.assertEqual(saved, [(d, s) for s in [0, 5, 10] for d in ['a', 'b']])

    def test_failed_commit_remains_due_for_retry(self):
        from ..services.websocket_listener import RawSampleSchedule
        schedule = RawSampleSchedule()
        self.assertTrue(schedule.due('a', 0))
        self.assertTrue(schedule.due('a', 1))  # prior transaction failed: no committed()
        schedule.committed('a', 1)
        self.assertFalse(schedule.due('a', 5.999))
        self.assertTrue(schedule.due('a', 6))
        self.assertTrue(schedule.due('b', 1))

    def test_receive_wait_and_publish_delay_are_measured_separately(self):
        clock, stop = Clock(), Stop()
        sock = Socket(clock, [frame()], step=9)
        def publish(data):
            clock.now += 4
            stop.set()
            return True
        with self.assertLogs('odoo.addons.smartsolar.services.websocket_listener', level='INFO') as logs:
            self.run_loop(stop, clock, lambda *a, **k: sock, publish)
        output = '\n'.join(logs.output)
        self.assertIn('receive_gap=9.000s recv_wait=9.000s', output)
        self.assertIn('publish_duration=4.000s', output)
