# -*- coding: utf-8 -*-
"""DTO — Data Transfer Object (đối tượng truyền dữ liệu) mà tầng Service trả về.

DTO là gì và vì sao cần?
    Service không trả recordset của Odoo hay dict lộn xộn ra ngoài, mà trả về các
    DTO có cấu trúc rõ ràng. Điều này (1) cắt đứt phụ thuộc vào ORM ở tầng trên,
    (2) đảm bảo output ổn định, (3) tập trung việc "đóng gói JSON" vào một chỗ.

Mỗi DTO có hàm ``to_dict()`` sinh ra cấu trúc JSON SẠCH:
    chỉ gồm số và chuỗi — KHÔNG markdown, KHÔNG HTML, KHÔNG câu chữ giải thích.
    LLM sẽ đọc JSON này và tự viết báo cáo bằng ngôn ngữ tự nhiên.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class SeriesResult:
    """Kết quả một chuỗi thời gian của MỘT metric (dùng để vẽ biểu đồ / phân tích xu hướng)."""
    metric: str                         # khóa metric
    unit: str                           # đơn vị
    aggregation: str                    # cách gộp đã dùng (avg/max/...)
    granularity: str                    # độ phân giải thực tế (raw/hour/day)
    range_local: List[str]              # [đầu, cuối] theo giờ UTC+7
    points: List[dict]                  # [{"t": iso_utc7, "v": số}, ...]
    device_id: Optional[int] = None     # None = toàn hệ thống

    def to_dict(self) -> dict:
        return {
            'metric': self.metric,
            'unit': self.unit,
            'aggregation': self.aggregation,
            'granularity': self.granularity,
            'range': self.range_local,
            'device_id': self.device_id,
            'count': len(self.points),
            'points': self.points,
        }


@dataclass
class AggregateResult:
    """Thống kê dạng vô hướng (một con số) cho một hoặc nhiều metric trên một khoảng.

    Dùng cho báo cáo tổng kết: "hôm nay sản xuất bao nhiêu kWh, đỉnh bao nhiêu W".
    """
    range_local: List[str]
    metrics: dict                       # {khóa_metric: {avg,min,max,sum,last,unit}}
    device_id: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            'range': self.range_local,
            'device_id': self.device_id,
            'metrics': self.metrics,
        }


@dataclass
class ComparisonResult:
    """Kết quả so sánh 2 khoảng thời gian bất kỳ (vd hôm nay vs hôm qua)."""
    metrics: List[str]
    period_a: dict                      # {"range":[...], "metrics":{...}}
    period_b: dict
    deltas: dict                        # {metric: {abs: chênh tuyệt đối, pct: chênh %}}

    def to_dict(self) -> dict:
        return {
            'metrics': self.metrics,
            'period_a': self.period_a,
            'period_b': self.period_b,
            'deltas': self.deltas,
        }


@dataclass
class AnomalyResult:
    """Kết quả phát hiện bất thường của một metric trên một khoảng."""
    metric: str
    unit: str
    method: str                         # phương pháp đã dùng (zscore/iqr/threshold)
    sensitivity: float                  # độ nhạy
    range_local: List[str]
    baseline: dict                      # thông số nền: {mean, std, q1, q3, ...}
    events: List[dict]                  # [{"t": iso, "v": số, "score": mức lệch}]

    def to_dict(self) -> dict:
        return {
            'metric': self.metric,
            'unit': self.unit,
            'method': self.method,
            'sensitivity': self.sensitivity,
            'range': self.range_local,
            'baseline': self.baseline,
            'event_count': len(self.events),
            'events': self.events,
        }


@dataclass
class HealthResult:
    """Điểm sức khỏe tổng hợp 0..100 của hệ thống trên một khoảng."""
    range_local: List[str]
    score: float                        # điểm tổng hợp 0..100
    components: dict                    # {tên thành phần: {score, weight, detail}}
    device_id: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            'range': self.range_local,
            'device_id': self.device_id,
            'score': self.score,
            'components': self.components,
        }


@dataclass
class ForecastResult:
    """Kết quả dự báo ngắn hạn cho một metric."""
    metric: str
    unit: str
    method: str
    horizon_hours: int                  # số giờ dự báo tới
    points: List[dict]                  # [{"t": iso, "v": số}]
    device_id: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            'metric': self.metric,
            'unit': self.unit,
            'method': self.method,
            'horizon_hours': self.horizon_hours,
            'device_id': self.device_id,
            'points': self.points,
        }


@dataclass
class DeviceStatusResult:
    """Trạng thái online/offline của các thiết bị.

    ``to_dict`` tự tính sẵn tổng số / số online / số offline để AI khỏi phải đếm.
    """
    devices: List[dict]                 # [{id, name, type, online, offline_minutes, last_sync}]

    def to_dict(self) -> dict:
        online = sum(1 for d in self.devices if d.get('online'))
        return {
            'total': len(self.devices),
            'online': online,
            'offline': len(self.devices) - online,
            'devices': self.devices,
        }


@dataclass
class AlarmResult:
    """Danh sách cảnh báo/sự kiện trên một khoảng."""
    range_local: List[str]
    alarms: List[dict]

    def to_dict(self) -> dict:
        return {
            'range': self.range_local,
            'count': len(self.alarms),
            'alarms': self.alarms,
        }
