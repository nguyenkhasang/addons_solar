# -*- coding: utf-8 -*-
"""Repository gốc. Giữ ``env`` của Odoo và các hàm tiện ích truy vấn dùng chung.

Repository là tầng DUY NHẤT được phép chạm vào ORM / SQL. Service phụ thuộc vào
Repository thông qua các hàm public của nó, KHÔNG bao giờ gọi thẳng model.

Vì sao phải cô lập ORM ở đây?
    Nếu mai này đổi cách lưu (đổi bảng, thêm cache, chuyển sang view SQL...), ta
    chỉ sửa Repository — Service/Tool/Adapter không hề hay biết. Đây là ranh giới
    bảo vệ business logic khỏi chi tiết lưu trữ.
"""
from __future__ import annotations

from datetime import datetime

from ..domain.value_objects import TimeRange


class BaseRepository:
    def __init__(self, env):
        # env là "cửa ngõ" vào ORM Odoo (self.env['model'], self.env.cr...).
        self._env = env

    @property
    def env(self):
        return self._env

    def _system_domain(self, system_id=None):
        """Sinh domain lọc theo hệ thống (rỗng nếu không truyền -> lấy tất cả)."""
        return [('system_id', '=', system_id)] if system_id else []

    def _range_domain(self, time_range: TimeRange, date_field='record_date'):
        """Sinh domain lọc theo khoảng thời gian nửa mở [start, end)."""
        return [
            (date_field, '>=', time_range.start_utc),
            (date_field, '<', time_range.end_utc),
        ]
