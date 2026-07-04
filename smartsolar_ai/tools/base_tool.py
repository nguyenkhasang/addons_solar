# -*- coding: utf-8 -*-
"""Lớp Tool cơ sở + "phong bì" (envelope) kết quả dùng chung.

Tool là gì trong kiến trúc này?
    Một lớp bọc MỎNG, ỔN ĐỊNH quanh một Service. Nhiệm vụ của Tool:
      - Khai báo JSON-schema cho tham số (dùng chung cho adapter OpenAI & MCP).
      - Kiểm tra / ép kiểu tham số thô (raw kwargs) thành đối tượng domain có kiểu.
      - Gọi ĐÚNG MỘT hàm service.
      - Bọc DTO kết quả vào phong bì chuẩn.

    Tool KHÔNG chứa business logic, KHÔNG chạm ORM. Nhờ vậy chữ ký tool ổn định —
    LLM, REST, MCP đều thấy cùng một hợp đồng, không phụ thuộc chi tiết bên trong.

Phong bì chuẩn (mọi tool trả về):
    {ok: bool, data: {...}|null, meta: {...}, error: {code, message}|null}
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

from ..domain.value_objects import UTC7


def now_local_iso() -> str:
    """Thời điểm hiện tại dạng chuỗi ISO giờ UTC+7 (dùng cho meta.generated_at)."""
    return datetime.now(timezone.utc).astimezone(UTC7).isoformat()


def ok(data: dict, meta: dict = None) -> dict:
    """Tạo phong bì THÀNH CÔNG."""
    return {'ok': True, 'data': data, 'meta': meta or {}, 'error': None}


def err(message: str, code: str = 'error', meta: dict = None) -> dict:
    """Tạo phong bì LỖI (kèm mã lỗi để bên gọi phân loại)."""
    return {'ok': False, 'data': None, 'meta': meta or {},
            'error': {'code': code, 'message': message}}


class Tool(ABC):
    """Tool trừu tượng. Lớp con đặt ``name``/``description`` và cài đặt
    ``parameters`` + ``run``."""

    name: str = ''          # tên tool, LLM dùng để gọi
    description: str = ''    # mô tả cho LLM biết khi nào nên dùng tool này

    def __init__(self, env):
        self.env = env

    @abstractmethod
    def parameters(self) -> dict:
        """Trả về một JSON-schema mô tả các tham số tool chấp nhận."""

    @abstractmethod
    def run(self, **kwargs) -> dict:
        """Thực thi tool. Trả về phần ``data`` (KHÔNG phải cả phong bì)."""

    def execute(self, **kwargs) -> dict:
        """Kiểm tra + chạy + bọc phong bì. KHÔNG BAO GIỜ ném lỗi ra ngoài.

        Đây là "lưới an toàn": mọi ngoại lệ được biến thành phong bì lỗi với mã
        phân loại rõ ràng, để adapter (và LLM) luôn nhận về JSON hợp lệ, không bao
        giờ bị crash giữa chừng:
          - KeyError    -> unknown_metric (gọi metric không tồn tại)
          - ValueError  -> bad_request    (thiếu/sai tham số)
          - còn lại     -> internal_error
        """
        try:
            data = self.run(**kwargs)
            # Cho phép run() gắn kèm meta riêng qua khóa '_meta'.
            meta = data.pop('_meta', {}) if isinstance(data, dict) else {}
            meta.setdefault('tool', self.name)
            meta.setdefault('generated_at', now_local_iso())
            return ok(data, meta)
        except KeyError as e:
            return err(str(e), code='unknown_metric')
        except ValueError as e:
            return err(str(e), code='bad_request')
        except Exception as e:  # noqa: BLE001 - tool tuyệt đối không được làm sập adapter
            return err(str(e), code='internal_error')

    # ---- Tiện ích xử lý tham số dùng chung --------------------------------
    @staticmethod
    def _require(kwargs, key):
        """Lấy tham số bắt buộc; thiếu hoặc rỗng -> ValueError (thành bad_request)."""
        if key not in kwargs or kwargs[key] in (None, ''):
            raise ValueError("Thiếu tham số bắt buộc '%s'" % key)
        return kwargs[key]

    @staticmethod
    def _opt_int(kwargs, key):
        """Lấy tham số số nguyên tùy chọn; không có -> None."""
        v = kwargs.get(key)
        return int(v) if v not in (None, '') else None
