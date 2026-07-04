# -*- coding: utf-8 -*-
"""Các điểm vào REST + JSON-RPC cho tầng AI Tool.

Cùng một bộ tool được phơi bày theo 3 cách, tất cả xuất phát từ MỘT registry:
  GET/POST /solar/ai/tools           -> khám phá (đặc tả cho OpenAI + MCP)
  POST     /solar/ai/tool/<name>     -> chạy một tool đơn lẻ (REST thuần)
  POST     /solar/ai/openai/dispatch -> điều phối tool_call của OpenAI
  POST     /solar/ai/mcp             -> MCP tools/list & tools/call

Nhờ vậy Web, mobile, hay bất kỳ client HTTP nào cũng dùng được các tool này —
LLM chỉ là MỘT trong số những bên gọi. Toàn bộ route yêu cầu đăng nhập
(auth='user') để tôn trọng quyền/record rules của Odoo.
"""
from __future__ import annotations

import json

from odoo import http
from odoo.http import request

from ..tools.registry import ToolRegistry
from ..adapters.openai_adapter import OpenAIAdapter
from ..adapters.mcp_adapter import MCPAdapter


def _registry():
    """Tạo registry gắn với env của request hiện tại (đúng user/company/quyền)."""
    return ToolRegistry(request.env)


class SolarAIController(http.Controller):

    @http.route('/solar/ai/tools', type='json', auth='user', methods=['POST'])
    def list_tools(self, **kw):
        """Khám phá: trả về đặc tả tool cho cả OpenAI, MCP, và dạng thô."""
        reg = _registry()
        return {
            'openai': OpenAIAdapter(reg).tool_specs(),
            'mcp': MCPAdapter(reg).list_tools(),
            'raw': reg.specs(),
        }

    @http.route('/solar/ai/tool/<string:name>', type='json', auth='user',
                methods=['POST'])
    def execute_tool(self, name, **kw):
        """Chạy một tool theo tên (REST thuần).

        Chấp nhận tham số theo 2 kiểu: bọc trong 'arguments', hoặc truyền phẳng
        trực tiếp (vd {metric:.., start:.., end:..}).
        """
        arguments = kw.get('arguments')
        if arguments is None:
            arguments = {k: v for k, v in kw.items() if k != 'arguments'}
        return _registry().execute(name, arguments)

    @http.route('/solar/ai/openai/dispatch', type='json', auth='user',
                methods=['POST'])
    def openai_dispatch(self, tool_call=None, **kw):
        """Nhận một tool_call của OpenAI và điều phối về đúng tool."""
        return OpenAIAdapter(_registry()).dispatch_tool_call(tool_call or kw)

    @http.route('/solar/ai/mcp', type='json', auth='user', methods=['POST'])
    def mcp(self, method=None, params=None, **kw):
        """Điểm vào JSON-RPC theo chuẩn MCP: hỗ trợ 'tools/list' và 'tools/call'."""
        adapter = MCPAdapter(_registry())
        if method == 'tools/list':
            return adapter.list_tools()
        if method == 'tools/call':
            params = params or {}
            return adapter.call_tool(params.get('name'), params.get('arguments'))
        return {'error': "Phương thức MCP không hỗ trợ: '%s'" % method}
