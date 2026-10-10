#!/usr/bin/env python3
"""JK BLE daemon using the project's Odoo registry; no HTTP/token endpoint."""
import argparse
import asyncio
import logging
import os
from pathlib import Path
import signal
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--odoo-dir', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--scan', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root / 'smartsolar' / 'services'))
    from jk_bms.transport import run, SERVICE, CollectorLockLost
    logging.basicConfig(level=logging.DEBUG if os.getenv('JK_BMS_DEBUG') == '1' else logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    initial_handlers = list(logging.getLogger().handlers)
    # Bleak debug logs can include raw device-info/password bytes.
    logging.getLogger('bleak').setLevel(logging.WARNING)
    logging.getLogger('dbus_fast').setLevel(logging.WARNING)
    if args.scan:
        async def scan():
            from bleak import BleakScanner
            for device, adv in (await BleakScanner.discover(timeout=25, return_adv=True)).values():
                print(device.address, adv.local_name or device.name, 'RSSI', adv.rssi,
                      'services', adv.service_uuids, 'JK candidate', SERVICE in adv.service_uuids)
        asyncio.run(scan())
        return
    sys.path.insert(0, str(Path(args.odoo_dir).resolve()))
    from odoo import api
    from odoo.tools import config
    from odoo.modules.module import initialize_sys_path
    from odoo.modules.registry import Registry
    config.parse_config(['-c', args.config, '--no-http'])
    # Odoo adds its journal handler; avoid duplicating every telemetry line.
    root_logger = logging.getLogger()
    for handler in initial_handlers:
        if len(root_logger.handlers) > 1 and handler in root_logger.handlers:
            root_logger.removeHandler(handler)
    initialize_sys_path()
    db = os.getenv('ODOO_DATABASE') or config['db_name']
    if isinstance(db, str):
        db = db.split(',')
    if not db or len(db) != 1:
        parser.error('Exactly one Odoo database required')
    uid = int(os.environ['ODOO_BMS_USER_ID'])
    battery_id = int(os.environ['JK_BMS_BATTERY_ID'])
    interval = float(os.getenv('READ_INTERVAL', '2'))
    if not 1 <= interval <= 5:
        parser.error('READ_INTERVAL must be 1–5 seconds')
    registry = Registry(db[0])
    logger = logging.getLogger('smartsolar.bms')
    # Session advisory lock: one receiver per battery, released on process exit.
    with registry.cursor() as lock_cr:
        lock_cr.execute('SELECT pg_try_advisory_lock(%s, %s)', [1936945002, battery_id])
        if not lock_cr.fetchone()[0]:
            parser.error('Another BMS collector owns this battery')
        lock_cr.commit()
        with registry.cursor() as cr:
            env = api.Environment(cr, uid, {})
            battery = env['smartsolar.battery'].browse(battery_id).exists()
            if not battery:
                parser.error('Configured battery does not exist')
            battery.check_access('write')
            selector = os.getenv('JK_BMS_DEVICE') or battery.device_identifier
        def publish(state):
            try:
                lock_cr.execute('SELECT 1')
                lock_cr.commit()
            except Exception:
                raise CollectorLockLost() from None
            with registry.cursor() as cr:
                env = api.Environment(cr, uid, {})
                env['smartsolar.battery'].browse(battery_id)._ingest(state)
            logger.info('Telemetry V=%.3f I=%.3f SOC=%.0f power=%.1f',
                        state['voltage'], state['current'], state['soc'], state['power'])
        async def serve():
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)
            await run(stop, publish, selector=selector, read_interval=interval,
                      debug=os.getenv('JK_BMS_DEBUG') == '1')
        try:
            asyncio.run(serve())
        finally:
            lock_cr.execute('SELECT pg_advisory_unlock(%s, %s)', [1936945002, battery_id])


if __name__ == '__main__':
    main()
