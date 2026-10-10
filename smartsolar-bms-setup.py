#!/usr/bin/env python3
"""Provision a local BMS collector after deployment approval (no BLE commands)."""
import argparse
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--odoo-dir', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--device', required=True, help='Address discovered and verified through BLE')
    parser.add_argument('--system-id', type=int)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.odoo_dir).resolve()))
    from odoo import api, SUPERUSER_ID
    from odoo.tools import config
    from odoo.modules.module import initialize_sys_path
    from odoo.modules.registry import Registry
    config.parse_config(['-c', args.config, '--no-http'])
    initialize_sys_path()
    names = config['db_name']
    names = names.split(',') if isinstance(names, str) else names
    if not names or len(names) != 1:
        parser.error('Exactly one database required')
    with Registry(names[0]).cursor() as cr:
        env = api.Environment(cr, SUPERUSER_ID, {})
        system = env['smartsolar.system'].browse(args.system_id).exists() if args.system_id else env['smartsolar.system'].search([('active', '=', True)])
        if len(system) != 1:
            parser.error('Choose exactly one system with --system-id')
        user = env['res.users'].search([('login', '=', 'smartsolar-bms-collector')], limit=1)
        if not user:
            user = env['res.users'].with_context(no_reset_password=True).create({
                'name': 'SmartSolar BLE collector', 'login': 'smartsolar-bms-collector',
                'group_ids': [(6, 0, [env.ref('base.group_user').id, env.ref('smartsolar.group_bms_collector').id])],
                'company_id': system.company_id.id, 'company_ids': [(6, 0, [system.company_id.id])]})
        battery = env['smartsolar.battery'].search([('device_identifier', '=', args.device)], limit=1)
        if battery and battery.system_id != system:
            parser.error('Existing battery belongs to another system')
        if not battery:
            battery = env['smartsolar.battery'].create({'name': 'Pin LFP 16S 100Ah · JK BMS',
                'system_id': system.id, 'device_identifier': args.device, 'history_interval': 60})
        if not user.has_group('smartsolar.group_bms_collector'):
            parser.error('Existing collector user lacks required group; inspect permissions')
        runtime = Path(args.odoo_dir) / '.odoo-runtime'
        runtime.mkdir(exist_ok=True)
        path = runtime / 'jk-bms.env'
        if any(c not in '0123456789abcdefABCDEF:' for c in args.device):
            parser.error('Setup requires the verified MAC address')
        content = (f'JK_BMS_DEVICE={args.device}\nJK_BMS_BATTERY_ID={battery.id}\n'
                   f'ODOO_BMS_USER_ID={user.id}\nREAD_INTERVAL=2\n')
        # No credentials in this file; restrict it before writing configuration.
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        path.write_text(content)
        print(f'Battery={battery.id}; collector={user.id}; system={system.id}; config={path}')


if __name__ == '__main__':
    main()
