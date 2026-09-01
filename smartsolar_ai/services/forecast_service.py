# -*- coding: utf-8 -*-
"""ForecastService — dự báo ngắn hạn cho bất kỳ metric nào.

Phương pháp mặc định là "naive-seasonal" (mùa vụ ngây thơ): với mỗi giờ tương lai,
lấy trung bình của CHÍNH giờ đó trong N ngày gần nhất. Ví dụ dự báo công suất lúc
13h ngày mai = trung bình công suất lúc 13h của 7 ngày qua.

Ưu điểm: thuần thống kê, không cần thư viện ML ngoài, và metric-agnostic. Mô hình
tốt hơn (ARIMA, ML...) có thể thay vào sau mà GIỮ NGUYÊN chữ ký hàm — Tool phía
trên không phải sửa.
"""
from __future__ import annotations

from datetime import timedelta, timezone

from odoo import fields

from ..domain.dto import ForecastResult
from ..domain.enums import AggregationType, ForecastMethod, Granularity, MetricKind
from ..domain.metric_registry import MetricRegistry
from ..domain.value_objects import TimeRange, UTC7
from .analytics_service import AnalyticsService


class ForecastService:
    _MIN_SAMPLES_PER_HOUR = 2

    def __init__(self, env):
        self._env = env
        self._analytics = AnalyticsService(env)

    def forecast(self, metric: str, horizon_hours: int,
                 method: ForecastMethod = ForecastMethod.NAIVE_SEASONAL,
                 lookback_days: int = 7,
                 device_id=None, system_id=None) -> ForecastResult:
        """Dự báo ``horizon_hours`` giờ tới cho một metric.

        Quy trình:
          1. Lấy lịch sử ``lookback_days`` ngày gần nhất, gộp theo giờ.
          2. Dựng "hồ sơ mùa vụ" (profile): trung bình giá trị theo từng giờ 0..23
             (giờ được đọc từ nhãn UTC+7 của dữ liệu lịch sử).
          3. Với mỗi giờ tương lai, tra profile theo giờ-đồng-hồ (UTC+7) tương ứng.
        """
        spec = MetricRegistry.get(metric)
        if not spec.supported:
            return ForecastResult(
                metric=metric, unit=spec.unit, method=method.value,
                horizon_hours=horizon_hours, points=[], device_id=device_id,
                available=False, reason=spec.note or 'Metric chưa được hỗ trợ.')
        if spec.kind != MetricKind.INSTANTANEOUS:
            return ForecastResult(
                metric=metric, unit=spec.unit, method=method.value,
                horizon_hours=horizon_hours, points=[], device_id=device_id,
                available=False,
                reason='Forecast chỉ hỗ trợ metric tức thời, không hỗ trợ %s.'
                       % spec.kind.value)
        if horizon_hours < 1 or horizon_hours > 48:
            raise ValueError('horizon_hours phải nằm trong khoảng 1..48')
        if lookback_days < 2 or lookback_days > 30:
            raise ValueError('lookback_days phải nằm trong khoảng 2..30')

        now = fields.Datetime.now()  # UTC naive
        lookback = TimeRange(now - timedelta(days=lookback_days), now)

        # Lịch sử theo giờ để dựng hồ sơ mùa vụ.
        series = self._analytics.get_timeseries(
            metric, lookback, aggregation=AggregationType.AVG,
            granularity=Granularity.HOUR, device_id=device_id, system_id=system_id,
            max_points=None)
        if not series.available:
            return ForecastResult(
                metric=metric, unit=spec.unit, method=method.value,
                horizon_hours=horizon_hours, points=[], device_id=device_id,
                available=False,
                reason=series.reason or 'Không có dữ liệu lịch sử để dự báo.',
                sample_count=0)

        # Gom trung bình theo giờ-đồng-hồ (0..23). Nhãn 't' dạng ISO UTC+7 nên ký
        # tự vị trí 11-12 chính là 2 chữ số giờ (vd '2026-07-02T13:00:00+07:00').
        by_hour = {}
        for p in series.points:
            hour = int(p['t'][11:13]) if len(p['t']) >= 13 else 0
            by_hour.setdefault(hour, []).append(p['v'])
        future = []
        cursor = now  # UTC naive
        for _ in range(horizon_hours):
            cursor = cursor + timedelta(hours=1)
            local_dt = cursor.replace(tzinfo=timezone.utc).astimezone(UTC7)
            future.append(local_dt)

        missing_hours = sorted({dt.hour for dt in future
                                if len(by_hour.get(dt.hour, []))
                                < self._MIN_SAMPLES_PER_HOUR})
        if missing_hours:
            return ForecastResult(
                metric=metric, unit=spec.unit, method=method.value,
                horizon_hours=horizon_hours, points=[], device_id=device_id,
                available=False,
                reason=('Không đủ dữ liệu lịch sử cho các giờ: %s; cần ít nhất %d '
                        'mẫu mỗi giờ.'
                        % (', '.join('%02d:00' % h for h in missing_hours),
                           self._MIN_SAMPLES_PER_HOUR)),
                sample_count=len(series.points))

        profile = {h: (sum(values) / len(values)) for h, values in by_hour.items()}
        points = [
            {'t': dt.isoformat(), 'v': round(profile[dt.hour], 3)}
            for dt in future
        ]
        target_counts = [len(by_hour[dt.hour]) for dt in future]
        confidence = min(100.0, min(target_counts) / lookback_days * 100.0)

        return ForecastResult(
            metric=metric, unit=spec.unit, method=method.value,
            horizon_hours=horizon_hours, points=points, device_id=device_id,
            available=True, reason=None, sample_count=len(series.points),
            confidence_pct=round(confidence, 1),
        )
