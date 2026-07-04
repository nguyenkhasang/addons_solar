# -*- coding: utf-8 -*-
"""Hàm hỗ trợ serialize JSON — đảm bảo output SẠCH, LLM đọc được.

Bảo đảm datetime/date/số thực được serialize ổn định và không có recordset của
Odoo lọt vào payload. Phong bì từ tool vốn đã là dict thuần; đây là "lưới an toàn"
cho tầng REST phòng trường hợp có kiểu lạ.
"""
from __future__ import annotations

import json
from datetime import date, datetime


class CleanJSONEncoder(json.JSONEncoder):
    """Encoder tùy biến: datetime/date -> chuỗi ISO; object có to_dict() -> gọi nó;
    còn lại -> ép về chuỗi (không bao giờ để vỡ vì kiểu lạ)."""

    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if hasattr(obj, 'to_dict'):
            return obj.to_dict()
        return str(obj)


def dumps(payload) -> str:
    """Serialize payload ra chuỗi JSON UTF-8 (giữ nguyên tiếng Việt có dấu)."""
    return json.dumps(payload, ensure_ascii=False, cls=CleanJSONEncoder)
