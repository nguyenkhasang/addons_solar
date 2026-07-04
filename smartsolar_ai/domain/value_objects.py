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

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Múi giờ Việt Nam. Mọi chuỗi ISO AI gửi lên, nếu không kèm offset, mặc định là giờ này.
UTC7 = timezone(timedelta(hours=7))


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
    def _parse_local(value) -> datetime:
        """Phân tích một chuỗi ISO 8601 (mặc định UTC+7 nếu không có múi giờ)
        thành datetime UTC naive để truy vấn DB.

        Chấp nhận cả datetime object lẫn chuỗi. Ký tự 'Z' (Zulu = UTC) được đổi
        thành '+00:00' cho ``fromisoformat`` hiểu được.
        """
        if isinstance(value, datetime):
            dt = value
        else:
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
