# -*- coding: utf-8 -*-
"""ToolRegistry — khởi tạo và điều phối bộ tool.

Đây là điểm DUY NHẤT mà các adapter (OpenAI / MCP / REST) nói chuyện tới. Thêm
một tool = thêm lớp của nó vào ``ALL_TOOLS``; registry, adapter và REST endpoint
tự động nhận ra — không phải sửa chúng.
"""
from __future__ import annotations

import logging

from .solar_tools import ALL_TOOLS

_logger = logging.getLogger('smartsolar_ai.tools')


class ToolRegistry:
    def __init__(self, env):
        self._env = env
        # Khởi tạo sẵn một thể hiện cho mỗi tool, tra theo tên.
        self._tools = {cls.name: cls(env) for cls in ALL_TOOLS}

    def names(self) -> list:
        """Danh sách tên tool đang có."""
        return list(self._tools)

    def get(self, name: str):
        """Lấy tool theo tên; không có -> KeyError kèm danh sách tool hợp lệ."""
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError("Tool '%s' không tồn tại. Có sẵn: %s"
                           % (name, ', '.join(self._tools)))
        return tool

    def execute(self, name: str, arguments: dict = None) -> dict:
        """Chạy một tool theo tên với dict tham số. Trả về PHONG BÌ chuẩn.

        Tool không tồn tại được biến thành phong bì lỗi 'unknown_tool' thay vì ném
        ngoại lệ -> bên gọi (LLM/HTTP) luôn nhận JSON hợp lệ.

        Ghi log mỗi lời gọi (tên tool + tham số + kết quả ok/lỗi) để theo dõi AI
        đang gọi tool nào khi làm việc. Bật xem: --log-handler smartsolar_ai.tools:INFO
        """
        _logger.info('SmartSolar AI: TOOL CALL -> %s | args=%s', name, arguments or {})
        try:
            tool = self.get(name)
        except KeyError as e:
            from .base_tool import err
            _logger.warning('SmartSolar AI: TOOL CALL <- %s | unknown_tool: %s', name, e)
            return err(str(e), code='unknown_tool')

        envelope = tool.execute(**(arguments or {}))
        if envelope.get('ok'):
            data = envelope.get('data') or {}
            # Tóm tắt gọn kết quả để log không phình (chỉ khóa cấp 1).
            summary = list(data.keys()) if isinstance(data, dict) else type(data).__name__
            _logger.info('SmartSolar AI: TOOL DONE <- %s | ok | data_keys=%s', name, summary)
        else:
            err_info = envelope.get('error') or {}
            _logger.warning('SmartSolar AI: TOOL FAIL <- %s | %s: %s', name,
                            err_info.get('code'), err_info.get('message'))
        return envelope

    def specs(self) -> list:
        """Đặc tả thô (name, description, parameters) — adapter tự định dạng lại.

        Cả OpenAIAdapter và MCPAdapter đều đọc TỪ ĐÂY, nên mô tả tool chỉ khai báo
        một lần, không lặp cho từng giao thức.
        """
        return [{
            'name': t.name,
            'description': t.description,
            'parameters': t.parameters(),
        } for t in self._tools.values()]
