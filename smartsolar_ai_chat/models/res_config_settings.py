# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    smartsolar_ai_provider = fields.Selection(
        selection="_smartsolar_ai_provider_selection",
        string="AI Provider",
        config_parameter="smartsolar_ai.provider",
        default="ollama",
        help="Nhà cung cấp AI. NVIDIA/OpenRouter/LM Studio/OpenAI đều dùng chuẩn "
             "OpenAI-compatible; Ollama có provider riêng (tự convert).",
    )
    smartsolar_ai_base_url = fields.Char(
        string="Base URL",
        config_parameter="smartsolar_ai.base_url",
        help="Để trống sẽ dùng URL mặc định của provider "
             "(vd Ollama http://localhost:11434, OpenAI https://api.openai.com/v1).",
    )
    smartsolar_ai_api_key = fields.Char(
        string="API Key",
        config_parameter="smartsolar_ai.api_key",
        help="Khóa API cho provider cloud (NVIDIA, OpenRouter, OpenAI). "
             "Ollama/LM Studio local thường không cần.",
    )
    smartsolar_ai_model = fields.Char(
        string="Model",
        config_parameter="smartsolar_ai.model",
        default="llama3.2:3b",
        help="Tên model. Phải hỗ trợ tool calling, vd: llama3.2:3b (Ollama), "
             "gpt-4o-mini (OpenAI), meta/llama-3.1-70b-instruct (NVIDIA).",
    )
    smartsolar_ai_max_tool_iterations = fields.Integer(
        string="Max Tool Iterations",
        config_parameter="smartsolar_ai.max_tool_iterations",
        default=5,
    )
    smartsolar_ai_history_limit = fields.Integer(
        string="History Limit",
        config_parameter="smartsolar_ai.history_limit",
        default=6,
        help="Số tin nhắn gần nhất trong kênh được nạp làm ngữ cảnh để AI nhớ hội "
             "thoại trước. 0 = tắt trí nhớ (mỗi câu hỏi độc lập). Đặt cao sẽ tốn "
             "nhiều token hơn.",
    )
    # Char (không phải Text) vì res.config.settings chỉ cho phép các type map 1-1 với
    # config_parameter (boolean/integer/float/char/selection/many2one/datetime).
    # Char không giới hạn độ dài nên prompt dài vẫn lưu được; view dùng widget="text"
    # để hiển thị dạng textarea nhiều dòng cho dễ nhập.
    smartsolar_ai_system_prompt = fields.Char(
        string="System Prompt",
        config_parameter="smartsolar_ai.system_prompt",
        help="Hướng dẫn vai trò/văn phong cho AI. Để trống sẽ dùng prompt mặc định. "
             "Lưu ý: cách gọi tool do spec tool quyết định, prompt chỉ định hướng hành vi.",
    )

    def _smartsolar_ai_provider_selection(self):
        """Lấy danh sách provider từ factory (thêm provider = tự có trong dropdown)."""
        from odoo.addons.smartsolar_ai_chat.providers.factory import available_providers
        labels = {
            'ollama': 'Ollama (local)',
            'openai': 'OpenAI',
            'nvidia': 'NVIDIA Build API',
            'openrouter': 'OpenRouter',
            'lmstudio': 'LM Studio (local)',
        }
        return [(name, labels.get(name, name.title())) for name in available_providers()]
