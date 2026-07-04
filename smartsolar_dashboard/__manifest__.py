# -*- coding: utf-8 -*-
{
    'name': 'Smart Solar Dashboard',
    'version': '19.0.1.0.0',
    'category': 'Custom',
    'summary': 'Dashboard tổng quan cho hệ thống Smart Solar',
    'description': """
        Smart Solar Dashboard
        =====================
        Module bổ sung cho `smartsolar`, cung cấp dashboard tổng quan với:
        * KPI hệ thống (công suất, hiệu suất, sản lượng, doanh thu, CO₂)
        * Biểu đồ Charge Power, Grid Tie Inverter theo thời gian
        * Biểu đồ sản lượng, nhiệt độ, phân bổ năng lượng
        * System Overview topology diagram (realtime)
        * Trạng thái thiết bị thời gian thực
        * Hỗ trợ time range: Live / 1H / 6H / 12H / 24H / 1 tuần / 1 tháng
        * Dark / Light mode, auto-refresh
    """,
    'author': 'Sangnk',
    'website': 'https://www.sangnk.vn',
    'depends': ['base', 'web', 'bus', 'smartsolar'],
    'data': [
        'security/smartsolar_security.xml',
        'views/dashboard_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'smartsolar_dashboard/static/src/components/**/*.js',
            'smartsolar_dashboard/static/src/components/**/*.xml',
            'smartsolar_dashboard/static/src/components/**/*.scss',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
