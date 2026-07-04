# -*- coding: utf-8 -*-
{
    'name': 'Smart Solar AI Tools',
    'version': '19.0.1.0.0',
    'category': 'Custom',
    'summary': 'AI Tool Layer (Function/Tool Calling) cho hệ thống giám sát điện mặt trời',
    'description': """
        Smart Solar AI Tools
        ====================
        Tầng Python Tool Layer cho AI Agent (Tool/Function Calling).

        Kiến trúc:
        User -> AI Planner (Ollama/LM Studio/OpenAI) -> Tool Layer
             -> Business Service -> Repository -> Odoo ORM -> PostgreSQL -> JSON

        - LLM KHÔNG truy cập DB, KHÔNG sinh SQL.
        - LLM chỉ gọi các Tool đã định nghĩa (capability-based, metric-agnostic).
        - Cùng Tool expose qua: Python, REST, OpenAI function-calling, MCP.
        - Thêm metric mới = thêm 1 entry trong MetricRegistry, không sửa Tool/Service.
    """,
    'author': 'Sangnk',
    'website': 'https://www.sangnk.vn',
    'depends': ['smartsolar'],
    'data': [],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
