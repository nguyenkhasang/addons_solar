# -*- coding: utf-8 -*-
{
    'name': 'Smart Solar',
    'version': '19.0.1.0.0',
    'category': 'Custom',
    'summary': 'Quan ly he thong nang luong mat troi',
    'description': """
        Smart Solar
        ===========
        Module quan ly he thong nang luong mat troi:
        * He thong (SmartSolar System)
        * Thiet bi (SmartSolar Device)
        * Du lieu Charge Power
        * Du lieu Grid Tie Inverter
        * Tong hop du lieu theo gio / ngay
        * Dong bo WebSocket voi MQSolar Cloud
    """,
    'author': 'Sangnk',
    'website': 'https://www.sangnk.vn',
    'depends': ['base', 'mail', 'bus'],
    'data': [
        'security/ir.model.access.csv',
        'views/smartsolar_system_views.xml',
        'views/smartsolar_device_views.xml',
        'views/charge_power_views.xml',
        'views/grid_tie_inverter_views.xml',
        'views/charge_power_summary_views.xml',
        'views/grid_tie_inverter_summary_views.xml',
        'data/scheduled_action.xml',
    ],
    'external_dependencies': {
        'python': ['websocket'],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
