# -*- coding: utf-8 -*-
{
    'name': 'Smart Solar Environment',
    'version': '19.0.1.0.0',
    'category': 'Custom',
    'summary': 'Thu thập dữ liệu môi trường (thời tiết) cho hệ thống Smart Solar',
    'description': """
        Smart Solar Environment
        =======================
        Module bổ sung cho `smartsolar`, thu thập dữ liệu môi trường từ Open-Meteo:
        * Cấu hình toạ độ (latitude/longitude) trên mỗi hệ thống.
        * Cron 5 phút/lần gọi API Open-Meteo (requests thuần, không cần API key).
        * Lưu đầy đủ dữ liệu current (nhiệt độ, độ ẩm, mây, mưa, áp suất...) và
          daily hôm nay (bình minh/hoàng hôn, bức xạ, UV, giờ nắng, weather_code...).
        * Diễn giải mã thời tiết WMO sang mô tả tiếng Việt.
        * Tổng hợp môi trường theo giờ/ngày (summary) và dọn dữ liệu raw cũ,
          dùng chung cron + chính sách giữ dữ liệu của module smartsolar.
    """,
    'author': 'Sangnk',
    'website': 'https://www.sangnk.vn',
    'depends': ['smartsolar'],
    'data': [
        'security/ir.model.access.csv',
        'views/smartsolar_environment_views.xml',
        'views/smartsolar_environment_summary_views.xml',
        'views/smartsolar_system_views.xml',
        'data/scheduled_action.xml',
    ],
    'external_dependencies': {
        'python': ['requests'],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
