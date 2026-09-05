# -*- coding: utf-8 -*-
"""AnomalyService — phát hiện điểm bất thường (outlier) trên chuỗi của bất kỳ metric.

Metric-agnostic (không phụ thuộc metric cụ thể): chạy được cho công suất, nhiệt
độ, điện áp pin, hay bất kỳ metric nào thêm sau này — vì nó thao tác trên
SeriesResult do AnalyticsService tạo ra, chứ không tự truy vấn.

3 phương pháp:
  - ZSCORE: điểm lệch >= N độ lệch chuẩn so với trung bình. Hợp dữ liệu phân phối chuẩn.
  - IQR: điểm nằm ngoài [Q1 - k*IQR, Q3 + k*IQR]. Chống nhiễu/đuôi dày tốt hơn.
  - THRESHOLD: vượt một ngưỡng tuyệt đối do người gọi đặt (sensitivity = ngưỡng).
"""
from __future__ import annotations

import statistics

from ..domain.dto import AnomalyResult
from ..domain.enums import AnomalyMethod, Granularity
from ..domain.metric_registry import MetricRegistry
from ..domain.value_objects import TimeRange
from .analytics_service import AnalyticsService


class AnomalyService:
    def __init__(self, env):
        self._analytics = AnalyticsService(env)

    def find_anomalies(self, metric: str, time_range: TimeRange,
                       method: AnomalyMethod = AnomalyMethod.ZSCORE,
                       sensitivity: float = 2.0,
                       device_id=None, system_id=None) -> AnomalyResult:
        """Lấy chuỗi thời gian rồi chạy thuật toán phát hiện bất thường.

        Cần tối thiểu 3 điểm mới đủ để tính thống kê nền có ý nghĩa; ít hơn thì trả
        về danh sách sự kiện rỗng (không đủ dữ liệu để kết luận).
        """
        spec = MetricRegistry.get(metric)
        # Metric DERIVED không có chuỗi thô -> không dò được bất thường theo thời
        # gian. Chặn ở đây với hướng dẫn ĐÚNG NGỮ CẢNH: trước đây nó lặng lẽ trả
        # event_count=0 (LLM hiểu sai thành "không có bất thường").
        if spec.is_derived:
            raise ValueError(
                "Metric '%s' là DERIVED (tính theo công thức, không có chuỗi dữ "
                "liệu thô) nên không dò được bất thường theo thời gian. Hãy dò trên "
                "các metric NGUỒN của nó, vd pv_input, output_power, "
                "energy_exported_total." % metric)
        series = self._analytics.get_timeseries(
            metric, time_range, granularity=Granularity.AUTO,
            device_id=device_id, system_id=system_id, max_points=None)
        values = [p['v'] for p in series.points]
        events, baseline = [], {}

        if not series.available or len(values) < 3:
            reason = series.reason if not series.available else (
                'Không đủ dữ liệu: cần tối thiểu 3 điểm, hiện có %d.' % len(values))
            return AnomalyResult(
                metric=metric, unit=spec.unit, method=method.value,
                sensitivity=sensitivity,
                range_local=[time_range.start_local_iso(), time_range.end_local_iso()],
                baseline={}, events=[], available=False, reason=reason,
                sample_count=len(values), min_required=3,
            )

        if method == AnomalyMethod.ZSCORE:
            events, baseline = self._zscore(series.points, sensitivity)
        elif method == AnomalyMethod.IQR:
            events, baseline = self._iqr(series.points, sensitivity)
        else:  # THRESHOLD: sensitivity chính là ngưỡng tuyệt đối
            baseline = {'threshold': sensitivity}
            events = [
                {'t': p['t'], 'v': p['v'],
                 'score': round(p['v'] - sensitivity, 3),
                 'direction': 'above'}
                for p in series.points if p['v'] > sensitivity
            ]

        return AnomalyResult(
            metric=metric, unit=spec.unit, method=method.value,
            sensitivity=sensitivity,
            range_local=[time_range.start_local_iso(), time_range.end_local_iso()],
            baseline=baseline, events=events, available=True, reason=None,
            sample_count=len(values), min_required=3,
        )

    @staticmethod
    def _zscore(points, sensitivity):
        """Phương pháp Z-score. score = (giá trị - trung bình) / độ lệch chuẩn.

        std = 0 (dữ liệu phẳng) -> không có bất thường, tránh chia 0.
        Trả về (danh sách sự kiện, thông số nền {mean, std}). Mỗi event có
        field ``direction`` = 'above' (giá trị > mean) hoặc 'below' (giá trị < mean),
        để LLM biết hướng lệch khi viết nhận định.
        """
        values = [p['v'] for p in points]
        mean = statistics.fmean(values)
        std = statistics.pstdev(values)
        baseline = {'mean': round(mean, 3), 'std': round(std, 3)}
        if std == 0:
            return [], baseline
        events = []
        for p in points:
            z = (p['v'] - mean) / std
            if abs(z) >= sensitivity:
                direction = 'above' if p['v'] > mean else 'below'
                events.append({'t': p['t'], 'v': p['v'], 'score': round(z, 3),
                               'direction': direction})
        return events, baseline

    @staticmethod
    def _iqr(points, sensitivity):
        """Phương pháp IQR (khoảng tứ phân vị).

        Q1/Q3 là tứ phân vị 25%/75%; IQR = Q3 - Q1. Điểm bị coi là bất thường nếu
        nằm dưới (Q1 - k*IQR) hoặc trên (Q3 + k*IQR), với k = sensitivity.
        score = mức vượt ra ngoài biên gần nhất. Trường ``direction`` cho biết
        điểm nằm 'above' (trên Q3+upper fence) hay 'below' (dưới Q1-lower fence).
        """
        values = sorted(p['v'] for p in points)
        n = len(values)
        q1 = values[n // 4]
        q3 = values[(3 * n) // 4]
        iqr = q3 - q1
        lower = q1 - sensitivity * iqr
        upper = q3 + sensitivity * iqr
        baseline = {'q1': round(q1, 3), 'q3': round(q3, 3), 'iqr': round(iqr, 3),
                    'lower': round(lower, 3), 'upper': round(upper, 3)}
        events = [
            {'t': p['t'], 'v': p['v'],
             'score': round(max(p['v'] - upper, lower - p['v']), 3),
             'direction': 'above' if p['v'] > upper else 'below'}
            for p in points if p['v'] < lower or p['v'] > upper
        ]
        return events, baseline
