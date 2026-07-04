# -*- coding: utf-8 -*-
"""Adapter theo chuẩn OpenAI function-calling.

Nhiệm vụ: sinh ra mảng ``tools`` cho chat API của OpenAI / Ollama / LM Studio, và
điều phối một tool_call từ LLM ngược về registry. Nó CHỈ đọc từ
``ToolRegistry.specs()`` nên không bao giờ phải sửa khi tool/metric thay đổi.

Đây là chỗ cô lập "giao thức LLM": đổi sang LLM khác chuẩn OpenAI -> không sửa gì;
làm adapter cho một chuẩn hoàn toàn khác -> thêm file adapter mới, business logic
không đụng tới.
"""
from __future__ import annotations

import json


class OpenAIAdapter:
    def __init__(self, registry):
        self._registry = registry

    def tool_specs(self) -> list:
        """Trả về mảng 'tools' theo định dạng OpenAI (mỗi phần tử type=function)."""
        return [{
            'type': 'function',
            'function': {
                'name': s['name'],
                'description': s['description'],
                'parameters': s['parameters'],
            },
        } for s in self._registry.specs()]

    def dispatch_tool_call(self, tool_call: dict) -> dict:
        """Thực thi MỘT đối tượng tool_call của OpenAI, trả về phong bì kết quả.

        Chấp nhận cả 2 dạng: {'function': {'name','arguments'}} hoặc {'name','arguments'}.
        Trường ``arguments`` có thể là chuỗi JSON (OpenAI gửi kiểu này) hoặc dict —
        parse an toàn, JSON hỏng thì coi như không có tham số.
        """
        fn = tool_call.get('function', tool_call)
        name = fn.get('name')
        raw_args = fn.get('arguments', {})
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args or '{}')
            except json.JSONDecodeError:
                raw_args = {}
        return self._registry.execute(name, raw_args)
