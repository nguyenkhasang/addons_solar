# -*- coding: utf-8 -*-
{
    'name': 'Smart Solar AI Chat',
    'version': '19.0.1.0.0',
    'category': 'Custom',
    'summary': 'Trợ lý AI giám sát điện mặt trời ngay trong Discuss (chat Odoo)',
    'description': """
        Smart Solar AI Chat
        ===================
        Tận dụng Discuss (chat có sẵn của Odoo) làm giao diện cho AI Agent.

        - Tạo một "người" bot riêng: SmartSolar AI.
        - Khi user nhắn tin cho bot trong Discuss, một planner loop sẽ gọi LLM
          qua Provider Layer -> LLM tự quyết định gọi các Tool của smartsolar_ai
          -> tổng hợp JSON -> trả lời bằng ngôn ngữ tự nhiên.
        - Tái sử dụng toàn bộ Tool Layer của smartsolar_ai (không lặp logic).

        Provider Layer hỗ trợ đa nhà cung cấp (đổi bằng cấu hình, không sửa code):
          Ollama, OpenAI, NVIDIA Build API, OpenRouter, LM Studio, và mọi
          provider OpenAI-compatible khác.

        Cấu hình trong Settings > Smart Solar AI:
          smartsolar_ai.provider  (ollama|openai|nvidia|openrouter|lmstudio)
          smartsolar_ai.base_url  (rỗng = mặc định theo provider)
          smartsolar_ai.api_key   (cho provider cloud)
          smartsolar_ai.model     (phải hỗ trợ tool calling)
          smartsolar_ai.max_tool_iterations (mặc định 5)
    """,
    'author': 'Sangnk',
    'website': 'https://www.sangnk.vn',
    'depends': ['smartsolar_ai', 'mail'],
    'data': [
        'data/ai_chat_data.xml',
        'views/res_config_settings_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'smartsolar_ai_chat/static/src/scss/settings.scss',
        ],
    },
    'external_dependencies': {
        'python': ['requests'],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
