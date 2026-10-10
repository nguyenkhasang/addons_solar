#!/usr/bin/env python3
"""Run with the same Odoo Python/config as the web service; no HTTP/cron workers."""
import argparse
import logging
from pathlib import Path
import signal
import sys
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--odoo-dir', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--database')
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.odoo_dir).resolve()))
    from odoo import api, SUPERUSER_ID
    from odoo.tools import config
    from odoo.modules.module import initialize_sys_path
    from odoo.modules.registry import Registry
    runtime = Path(args.odoo_dir) / '.odoo-runtime'
    runtime.mkdir(exist_ok=True)
    config.parse_config(['-c', args.config, '--no-http', '--logfile', str(runtime / 'listener.log')],
                        setup_logging=True)
    initialize_sys_path()
    dbnames = [args.database] if args.database else config['db_name']
    if isinstance(dbnames, str):
        dbnames = dbnames.split(',')
    if not dbnames or len(dbnames) != 1:
        parser.error('Configure exactly one database or pass --database')
    registry = Registry(dbnames[0])
    from odoo.addons.smartsolar.services.websocket_listener import listen, LOCK_NAMESPACE, ListenerLockLost, RawSampleSchedule
    logger = logging.getLogger('smartsolar.listener')
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    def worker(system_id):
        try:
            with registry.cursor() as lock_cr:
                lock_cr.execute('SELECT pg_try_advisory_lock(%s, %s)', [LOCK_NAMESPACE, system_id])
                if not lock_cr.fetchone()[0]:
                    return
                lock_cr.commit()  # session lock survives; do not leave an idle transaction
                sample_schedule = RawSampleSchedule()

                def check_lock():
                    try:
                        lock_cr.execute('SELECT 1')
                        lock_cr.commit()
                    except Exception:
                        raise ListenerLockLost() from None

                def load_config():
                    check_lock()
                    with registry.cursor() as cr:
                        env = api.Environment(cr, SUPERUSER_ID, {})
                        system = env['smartsolar.system'].browse(system_id).exists()
                        if not system or not system.active or not system.mqsolar_cloud_token:
                            return None
                        devices = system.device_ids.filtered(lambda d: d.active and d.device_guid)
                        if not devices:
                            return None
                        return {'url': system._build_mqsolar_ws_url(),
                                'devices': sorted(str(d.device_guid) for d in devices)}

                def publish(data):
                    check_lock()
                    guid = str(data.get('deviceId') or '')
                    now = time.monotonic()
                    saved = False
                    with registry.cursor() as cr:
                        env = api.Environment(cr, SUPERUSER_ID, {})
                        device = env['smartsolar.device'].search([
                            ('system_id', '=', system_id), ('device_guid', '=', guid),
                            ('active', '=', True)], limit=1)
                        if not device:
                            return False
                        if sample_schedule.due(guid, now):
                            try:
                                with cr.savepoint():
                                    saved = device._save_mqsolar_raw_sample(data)
                            except Exception as error:
                                logger.warning('MQSolar raw sample system=%s rejected reason=%s',
                                               system_id, type(error).__name__)
                        published = device._send_mqsolar_realtime(data)
                    # Cursor commits before advancing the sample schedule.
                    if saved:
                        sample_schedule.committed(guid, now)
                    return published

                try:
                    listen(system_id, stop, load_config, publish)
                finally:
                    lock_cr.execute('SELECT pg_advisory_unlock(%s, %s)', [LOCK_NAMESPACE, system_id])
        except Exception as error:
            logger.warning('MQSolar worker system=%s stopped reason=%s', system_id, type(error).__name__)

    workers = {}
    while not stop.is_set():
        with registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            ids = env['smartsolar.system'].search([
                ('active', '=', True), ('device_ids', '!=', False)]).ids
        for system_id in ids:
            if system_id not in workers or not workers[system_id].is_alive():
                thread = threading.Thread(target=worker, args=(system_id,), name=f'mqsolar-{system_id}', daemon=True)
                thread.start()
                workers[system_id] = thread
        stop.wait(15)
    for thread in workers.values():
        thread.join(timeout=15)


if __name__ == '__main__':
    main()
