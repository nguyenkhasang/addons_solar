# -*- coding: utf-8 -*-
"""Các Value Object (đối tượng giá trị) bất biến dùng chung toàn bộ Tool Layer.

Value Object = đối tượng chỉ mang giá trị, không có định danh (id), và BẤT BIẾN
(immutable — tạo xong không sửa được, nhờ ``frozen=True``). Ưu điểm: an toàn khi
truyền qua nhiều tầng, không sợ bị sửa ngầm, dễ test.

File này cố ý KHÔNG import Odoo để có thể test chạy độc lập.

Bối cảnh múi giờ (rất quan trọng):
    - AI/người dùng luôn nói theo giờ Việt Nam (UTC+7).
    - Odoo/PostgreSQL lại LƯU datetime dạng UTC "naive" (không gắn tzinfo).
    => Mọi mốc thời gian AI gửi vào đều được hiểu là UTC+7, rồi quy đổi sang
       UTC naive để truy vấn. Khi trả kết quả ra thì đổi ngược lại UTC+7 cho AI đọc.
    Toàn bộ quy tắc đổi múi giờ gói gọn ở đây, các tầng khác không phải lo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Múi giờ Việt Nam. Mọi chuỗi ISO AI gửi lên, nếu không kèm offset, mặc định là giờ này.
UTC7 = timezone(timedelta(hours=7))

# Token thời gian TƯƠNG ĐỐI mà AI được phép gửi thay cho mốc tuyệt đối.
# Mục đích: AI KHÔNG phải tự biết "bây giờ là mấy giờ" và KHÔNG tự trừ múi giờ —
# server tự tính từ thời điểm hiện tại (UTC+7) rồi mới quy về UTC. Nhờ vậy tránh
# lỗi "trừ 7 giờ hai lần" khi AI vừa tự đổi sang UTC vừa để tool đổi thêm lần nữa.
#   now                -> thời điểm hiện tại
#   now-2h / now-30m   -> lùi 2 giờ / 30 phút
#   now-7d             -> lùi 7 ngày
#   today              -> 00:00 hôm nay (giờ VN)
#   yesterday          -> 00:00 hôm qua (giờ VN)
#   tomorrow           -> 00:00 ngày mai (giờ VN) — hữu ích cho end của "hôm nay"
_RE_NOW_DELTA = re.compile(r'^now\s*-\s*(\d+)\s*([mhd])$', re.IGNORECASE)
_UNIT_KEY = {'m': 'minutes', 'h': 'hours', 'd': 'days'}


@dataclass(frozen=True)
class TimeRange:
    """Một khoảng thời gian nửa mở [start, end) lưu ở dạng UTC naive.

    "Nửa mở" nghĩa là bao gồm mốc đầu, KHÔNG bao gồm mốc cuối — tránh đếm trùng
    khi ghép nhiều khoảng liền nhau (vd 00:00-01:00 và 01:00-02:00 không đè nhau).

    Cách tạo chuẩn: dùng ``TimeRange.from_iso(start, end)`` với chuỗi ISO 8601
    theo giờ UTC+7 mà AI cung cấp.
    """
    start_utc: datetime
    end_utc: datetime

    def __post_init__(self):
        # Chặn khoảng thời gian ngược/rỗng ngay từ lúc tạo -> lỗi sớm, rõ ràng.
        if self.start_utc >= self.end_utc:
            raise ValueError('TimeRange: start phải nhỏ hơn end')

    @staticmethod
    def _resolve_relative(text: str):
        """Thử phân giải một TOKEN thời gian tương đối thành datetime UTC naive.

        Trả về datetime (UTC naive) nếu ``text`` là token hợp lệ (now, now-2h,
        today, yesterday, tomorrow...), hoặc None nếu không phải token tương đối
        (để bên gọi rơi về nhánh parse ISO tuyệt đối).

        MẤU CHỐT chống lệch giờ: mốc gốc là ``datetime.now(UTC7)`` — tức thời điểm
        hiện tại ĐÃ ở giờ Việt Nam. Server tự trừ khoảng và tự quy về UTC đúng MỘT
        lần. AI không cần biết giờ hiện tại, không tự làm phép trừ nào.
        """
        s = (text or '').strip().lower()
        if not s or not (s.startswith('now') or s in ('today', 'yesterday', 'tomorrow')):
            return None

        now_local = datetime.now(UTC7)

        if s == 'now':
            local = now_local
        elif s in ('today', 'yesterday', 'tomorrow'):
            midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            offset_days = {'today': 0, 'yesterday': -1, 'tomorrow': 1}[s]
            local = midnight + timedelta(days=offset_days)
        else:
            m = _RE_NOW_DELTA.match(s)
            if not m:
                # Bắt đầu bằng 'now' nhưng sai cú pháp -> báo lỗi rõ thay vì âm thầm sai.
                raise ValueError(
                    "Token thời gian '%s' không hợp lệ. Dùng: now, now-2h, now-30m, "
                    "now-7d, today, yesterday, tomorrow." % text)
            amount = int(m.group(1))
            unit = _UNIT_KEY[m.group(2).lower()]
            local = now_local - timedelta(**{unit: amount})

        # Quy về UTC naive để khớp định dạng Odoo lưu trong DB.
        return local.astimezone(timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _parse_local(value) -> datetime:
        """Phân tích một mốc thời gian thành datetime UTC naive để truy vấn DB.

        Ưu tiên TOKEN TƯƠNG ĐỐI (now / now-2h / today / yesterday...) — server tự
        tính, tránh việc AI tự trừ múi giờ. Nếu không phải token thì parse như ISO
        8601 tuyệt đối; chuỗi không kèm múi giờ được coi là giờ Việt Nam (UTC+7).

        Chấp nhận cả datetime object lẫn chuỗi. Ký tự 'Z' (Zulu = UTC) được đổi
        thành '+00:00' cho ``fromisoformat`` hiểu được.
        """
        if isinstance(value, datetime):
            dt = value
        else:
            # 1) Token tương đối -> đã là UTC naive, trả luôn.
            relative = TimeRange._resolve_relative(value)
            if relative is not None:
                return relative
            # 2) ISO tuyệt đối.
            text = str(value).strip().replace('Z', '+00:00')
            dt = datetime.fromisoformat(text)
        # Nếu chuỗi không kèm múi giờ -> coi như giờ Việt Nam.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC7)
        # Quy về UTC rồi bỏ tzinfo để khớp định dạng Odoo lưu (UTC naive).
        return dt.astimezone(timezone.utc).replace(tzinfo=None)

    @classmethod
    def from_iso(cls, start, end) -> 'TimeRange':
        """Factory: tạo TimeRange từ hai chuỗi ISO (giờ UTC+7) của AI."""
        return cls(cls._parse_local(start), cls._parse_local(end))

    @property
    def duration(self) -> timedelta:
        """Độ dài khoảng thời gian."""
        return self.end_utc - self.start_utc

    @property
    def days(self) -> float:
        """Độ dài tính bằng ngày (số thực). Repository dùng để tự chọn granularity."""
        return self.duration.total_seconds() / 86400.0

    def start_local_iso(self) -> str:
        """Mốc đầu, quy về chuỗi ISO giờ UTC+7 để trả cho AI đọc."""
        return self.start_utc.replace(tzinfo=timezone.utc).astimezone(UTC7).isoformat()

    def end_local_iso(self) -> str:
        """Mốc cuối, quy về chuỗi ISO giờ UTC+7 để trả cho AI đọc."""
        return self.end_utc.replace(tzinfo=timezone.utc).astimezone(UTC7).isoformat()


@dataclass(frozen=True)
class DataPoint:
    """Một mẫu dữ liệu (thời điểm, giá trị).

    ``ts_utc`` là thời điểm dạng UTC naive; ``value`` là số đo tại thời điểm đó.
    Repository trả về danh sách DataPoint; Service sẽ đổi nhãn thời gian sang
    UTC+7 khi đóng gói kết quả.
    """
    ts_utc: datetime
    value: float

    def label_local(self) -> str:
        """Nhãn thời gian dạng chuỗi ISO giờ UTC+7 (để hiển thị / cho AI đọc)."""
        return self.ts_utc.replace(tzinfo=timezone.utc).astimezone(UTC7).isoformat()
