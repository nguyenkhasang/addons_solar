"""Persistent receive loop; Odoo transactions live in the supplied callbacks."""
import json
import logging
import time

_logger = logging.getLogger(__name__)
LOCK_NAMESPACE = 1936945001
RAW_SAMPLE_SECONDS = 5


class RawSampleSchedule:
    """Advance only after a successful commit; bus frames are never throttled."""
    def __init__(self):
        self.last_saved = {}

    def due(self, device_guid, now):
        return now - self.last_saved.get(device_guid, float('-inf')) >= RAW_SAMPLE_SECONDS

    def committed(self, device_guid, now):
        self.last_saved[device_guid] = now


class ListenerLockLost(Exception):
    """The worker must exit and reacquire its database ownership lock."""


def listen(system_id, stop, load_config, publish, *, connect=None,
           clock=time.monotonic, received_clock=time.time, timeout_errors=None):
    if connect is None or timeout_errors is None:
        import websocket
        connect = connect or websocket.create_connection
        timeout_errors = timeout_errors or (websocket.WebSocketTimeoutException,)
    retry = 1
    while not stop.is_set():
        ws = None
        try:
            config = load_config()
            if not config:
                return
            ws = connect(config['url'], timeout=10)
            ws.send(json.dumps({'topic': 'subscribe', 'payload': {'devices': config['devices']}}))
            ws.settimeout(5)
            _logger.info('MQSolar listener system=%s connected devices=%s', system_id, len(config['devices']))
            last_data = last_ping = last_config = clock()
            while not stop.is_set():
                now = clock()
                if now - last_config >= 30:
                    current = load_config()
                    if current != config:
                        break  # reload token/device selection without a service restart
                    last_config = now
                if now - last_ping >= 20:
                    ws.ping()
                    last_ping = now
                if now - last_data >= 60:
                    raise TimeoutError('No device data within receive deadline')
                try:
                    read_started = clock()
                    raw = ws.recv()
                except timeout_errors:
                    continue
                if not raw:
                    raise ConnectionError('WebSocket closed')
                try:
                    data = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if not isinstance(data, dict) or str(data.get('deviceId') or '') not in config['devices']:
                    continue
                received = clock()
                if received - last_data >= 3:
                    _logger.info('MQSolar listener system=%s receive_gap=%.3fs recv_wait=%.3fs',
                                 system_id, received - last_data, received - read_started)
                data['_received_at'] = received_clock()
                published = publish(data)
                finished = clock()
                if finished - received >= 1:
                    _logger.warning('MQSolar listener system=%s publish_duration=%.3fs',
                                    system_id, finished - received)
                if published:
                    last_data = clock()
                    retry = 1
        except ListenerLockLost:
            raise
        except Exception as error:
            # Exception strings may contain the URL/token: never log them.
            _logger.warning('MQSolar listener system=%s reconnect reason=%s delay=%ss',
                            system_id, type(error).__name__, retry)
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
        if stop.is_set() or stop.wait(retry):
            return
        retry = min(retry * 2, 30)
