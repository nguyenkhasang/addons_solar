# -*- coding: utf-8 -*-
"""Factory — đọc cấu hình Odoo và trả về đúng AIProvider.

Đây là NƠI DUY NHẤT được phép ánh xạ 'tên provider -> class'. Mọi nơi khác trong
hệ thống chỉ làm việc với interface AIProvider, không có if provider == ...

Thêm nhà cung cấp OpenAI-compatible mới trong tương lai = thêm 1 dòng vào
_PROVIDERS (class + base_url mặc định), không sửa business logic.
"""
from __future__ import annotations

from .base import AIProvider
from .openai_compatible import OpenAICompatibleProvider
from .ollama import OllamaProvider

# Bảng đăng ký provider: tên -> (class, base_url mặc định).
# NVIDIA / OpenRouter / LM Studio / OpenAI đều là OpenAI-compatible -> cùng class,
# chỉ khác base_url. Đổi/ thêm ở đây, không đụng nơi khác.
_PROVIDERS = {
    'ollama':     (OllamaProvider,           'http://localhost:11434'),
    'openai':     (OpenAICompatibleProvider, 'https://api.openai.com/v1'),
    'nvidia':     (OpenAICompatibleProvider, 'https://integrate.api.nvidia.com/v1'),
    'openrouter': (OpenAICompatibleProvider, 'https://openrouter.ai/api/v1'),
    'lmstudio':   (OpenAICompatibleProvider, 'http://localhost:1234/v1'),
}

_DEFAULT_PROVIDER = 'ollama'


def available_providers() -> list:
    """Danh sách tên provider hỗ trợ (dùng cho selection field trong Settings)."""
    return list(_PROVIDERS)


def get_provider(env) -> AIProvider:
    """Đọc cấu hình từ ir.config_parameter và dựng provider tương ứng.

    Config (generic, kèm fallback key cũ để không vỡ cấu hình đang có):
      smartsolar_ai.provider   -> tên provider (mặc định 'ollama')
      smartsolar_ai.base_url   -> ghi đè base_url; rỗng dùng mặc định của provider
      smartsolar_ai.api_key    -> cho provider cloud (nvidia/openrouter/openai)
      smartsolar_ai.model      -> tên model; fallback smartsolar_ai.ollama_model
      smartsolar_ai.timeout    -> giây (mặc định 120)
    """
    Param = env['ir.config_parameter'].sudo()

    name = (Param.get_param('smartsolar_ai.provider') or _DEFAULT_PROVIDER).strip().lower()
    provider_cls, default_url = _PROVIDERS.get(name, _PROVIDERS[_DEFAULT_PROVIDER])

    base_url = (Param.get_param('smartsolar_ai.base_url') or '').strip() or default_url
    api_key = (Param.get_param('smartsolar_ai.api_key') or '').strip()
    # model: key mới, fallback key cũ 'ollama_model' để tương thích ngược.
    model = ((Param.get_param('smartsolar_ai.model') or '').strip()
             or (Param.get_param('smartsolar_ai.ollama_model') or '').strip()
             or 'llama3.2:3b')
    timeout = int(Param.get_param('smartsolar_ai.timeout', 120) or 120)

    return provider_cls(base_url=base_url, api_key=api_key, model=model, timeout=timeout)
