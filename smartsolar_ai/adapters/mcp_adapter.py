# -*- coding: utf-8 -*-
"""Adapter theo chuẩn MCP (Model Context Protocol).

Phơi bày CÙNG bộ tool đó dưới dạng MCP:
  - ``tools/list``: trả về danh sách tool kèm ``inputSchema``.
  - ``tools/call``: điều phối lời gọi tool.
Cũng chỉ đọc từ ``ToolRegistry.specs()`` — dùng chung một nguồn với OpenAIAdapter.

Điểm khác OpenAI: MCP bọc kết quả trong mảng ``content`` (kiểu 'text' chứa chuỗi
JSON của phong bì) và cờ ``isError``.
"""
from __future__ import annotations


class MCPAdapter:
    def __init__(self, registry):
        self._registry = registry

    def list_tools(self) -> dict:
        """Kết quả cho lời gọi MCP 'tools/list'."""
        return {
            'tools': [{
                'name': s['name'],
                'description': s['description'],
                'inputSchema': s['parameters'],
            } for s in self._registry.specs()]
        }

    def call_tool(self, name: str, arguments: dict = None) -> dict:
        """Kết quả cho 'tools/call'. Nội dung là phong bì JSON đóng thành text.

        ``isError`` phản ánh cờ ``ok`` của phong bì để client MCP biết thành/bại.
        ``default=str`` phòng khi lỡ có kiểu chưa serialize được (vd datetime).
        """
        import json
        envelope = self._registry.execute(name, arguments or {})
        return {
            'content': [{
                'type': 'text',
                'text': json.dumps(envelope, ensure_ascii=False, default=str),
            }],
            'isError': not envelope.get('ok', False),
        }
