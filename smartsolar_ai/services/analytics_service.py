# -*- coding: utf-8 -*-
"""AnalyticsService — chuỗi thời gian, thống kê tổng hợp, và so sánh 2 kỳ.

Chỉ MỘT service này đã thay thế cho get_power / get_temperature / get_battery /
get_weather / compare_today / compare_week ... vì tất cả chúng bản chất là "lấy
thống kê/chuỗi của (các) metric trên một khoảng thời gian". Metric là THAM SỐ,
không phải tên hàm — nhờ vậy thêm metric mới không đẻ ra hàm mới.

Service chỉ chứa business logic, nhận tham số đã có kiểu (typed), trả về DTO.
Không chạm ORM (đã ủy quyền cho Repository).
"""
from __future__ import annotations

from ..domain.dto import AggregateResult, ComparisonResult, SeriesResult
from ..domain.enums import AggregationType, Granularity, MetricKind
from ..domain.metric_registry import MetricRegistry
from ..domain.value_objects import TimeRange
from ..repositories.metric_repository import MetricRepository

# Bảng tra "nguồn năng lượng" cho metric dẫn xuất: ánh xạ tên biến phụ thuộc trong
# công thức -> khóa metric COUNTER thực tế để lấy số kWh. Toàn bộ logic năng lượng
# hybrid gom một chỗ tại đây, khớp với công thức khai báo trong metric_registry.
#
# LƯU Ý (placeholder): DB hiện chưa có công-tơ riêng cho "điện lấy lưới" dạng kWh
# và "điện tải tiêu thụ". Hai dòng dưới đang trỏ TẠM. Khi bổ sung cảm biến thật,
# chỉ cần sửa ánh xạ ở đây + thêm MetricSpec -> các KPI dẫn xuất tự đúng.
_ENERGY_SOURCES = {
    'pv_energy': 'pv_energy_total',                 # PV nạp (MPPT) -> bộ đếm
    'grid_export_energy': 'energy_exported_total',  # hòa lưới (GTI) -> bộ đếm
    'grid_import_energy': 'energy_exported_total',  # TẠM: chưa có bộ đếm lấy lưới riêng
    'load_energy': 'pv_energy_total',               # TẠM: chưa có đo tải riêng
}


class AnalyticsService:
    def __init__(self, env):
        self._repo = MetricRepository(env)

    # ---- Chuỗi thời gian ---------------------------------------------------
    def get_timeseries(self, metric: str, time_range: TimeRange,
                       aggregation: AggregationType = None,
                       granularity: Granularity = Granularity.AUTO,
                       device_id=None, system_id=None) -> SeriesResult:
        """Lấy chuỗi thời gian của một metric trên một khoảng.

        Metric dẫn xuất không có cột thô nên không vẽ được chuỗi mịn; ta trả về
        một điểm vô hướng duy nhất (tại mốc cuối) thay vì báo lỗi, để AI vẫn đọc được.
        Metric thường: chọn cách gộp mặc định nếu không chỉ định, hỏi granularity
        thực tế đã dùng để ghi vào kết quả, rồi đổi nhãn thời gian sang UTC+7.
        """
        spec = MetricRegistry.get(metric)
        if spec.is_derived:
            agg = self.get_aggregate([metric], time_range, device_id, system_id)
            val = agg.metrics[metric]['value']
            return SeriesResult(
                metric=metric, unit=spec.unit,
                aggregation='derived', granularity='range',
                range_local=[time_range.start_local_iso(), time_range.end_local_iso()],
                points=[{'t': time_range.end_local_iso(), 'v': val}],
                device_id=device_id,
            )

        agg = aggregation or spec.default_aggregation
        resolved_gran = self._repo.resolve_granularity(spec, time_range, granularity)
        points = self._repo.fetch_series(
            spec, time_range, agg, granularity, device_id, system_id)
        return SeriesResult(
            metric=metric, unit=spec.unit,
            aggregation=agg.value, granularity=resolved_gran.value,
            range_local=[time_range.start_local_iso(), time_range.end_local_iso()],
            points=[{'t': p.label_local(), 'v': round(p.value, 3)} for p in points],
            device_id=device_id,
        )

    # ---- Thống kê vô hướng -------------------------------------------------
    def get_aggregate(self, metrics, time_range: TimeRange,
                      device_id=None, system_id=None) -> AggregateResult:
        """Tính thống kê vô hướng cho một hay nhiều metric trên một khoảng.

        Phân nhánh theo loại metric:
          - DERIVED: tính bằng công thức (qua ``_compute_derived``).
          - COUNTER: trả 'energy' (kWh tích lũy trong khoảng) + 'last'.
          - INSTANTANEOUS: trả avg/min/max/last.
        """
        result = {}
        for metric in metrics:
            spec = MetricRegistry.get(metric)
            if spec.is_derived:
                result[metric] = self._compute_derived(
                    spec, time_range, device_id, system_id)
            elif spec.kind == MetricKind.COUNTER:
                energy = self._repo.fetch_energy(
                    spec, time_range, device_id, system_id)
                stats = self._repo.fetch_scalar(
                    spec, time_range, device_id, system_id)
                result[metric] = {
                    'unit': spec.unit, 'energy': round(energy, 3),
                    'last': round(stats['last'], 3), 'count': stats['count'],
                }
            else:
                stats = self._repo.fetch_scalar(
                    spec, time_range, device_id, system_id)
                result[metric] = {
                    'unit': spec.unit,
                    'avg': round(stats['avg'], 3), 'min': round(stats['min'], 3),
                    'max': round(stats['max'], 3), 'last': round(stats['last'], 3),
                    'count': stats['count'],
                }
        return AggregateResult(
            range_local=[time_range.start_local_iso(), time_range.end_local_iso()],
            metrics=result, device_id=device_id,
        )

    def _compute_derived(self, spec, time_range, device_id, system_id) -> dict:
        """Tính một metric dẫn xuất.

        Bước 1: dựng "context" — lấy số kWh của từng biến phụ thuộc (dựa vào bảng
        tra ``_ENERGY_SOURCES``). Bước 2: gọi công thức đã khai báo trong registry.
        Bọc try/except: công thức lỗi -> trả 0 thay vì làm hỏng cả báo cáo.
        ``inputs`` được trả kèm để AI/dev soi được số liệu đầu vào (minh bạch).
        """
        context = {}
        for dep in spec.depends_on:
            source_metric = _ENERGY_SOURCES.get(dep)
            if source_metric and MetricRegistry.exists(source_metric):
                src_spec = MetricRegistry.get(source_metric)
                context[dep] = self._repo.fetch_energy(
                    src_spec, time_range, device_id, system_id)
            else:
                context[dep] = 0.0
        try:
            value = spec.formula(context)
        except Exception:
            value = 0.0
        return {'unit': spec.unit, 'value': round(value, 3), 'inputs': context}

    # ---- So sánh 2 kỳ ------------------------------------------------------
    def compare_periods(self, metrics, range_a: TimeRange, range_b: TimeRange,
                        device_id=None, system_id=None) -> ComparisonResult:
        """So sánh các metric giữa 2 khoảng thời gian BẤT KỲ.

        Nhờ nhận 2 khoảng tùy ý, một hàm này trả lời được vô số câu: hôm nay vs
        hôm qua, tuần này vs tuần trước, "3 ngày gần nhất vs cùng kỳ năm ngoái"...
        Với mỗi metric trả về chênh lệch tuyệt đối (abs) và phần trăm (pct).
        """
        agg_a = self.get_aggregate(metrics, range_a, device_id, system_id)
        agg_b = self.get_aggregate(metrics, range_b, device_id, system_id)
        deltas = {}
        for metric in metrics:
            a = self._representative(agg_a.metrics[metric])
            b = self._representative(agg_b.metrics[metric])
            abs_delta = a - b
            pct = (abs_delta / b * 100.0) if b else None  # b=0 -> pct không xác định
            deltas[metric] = {
                'a': round(a, 3), 'b': round(b, 3),
                'abs': round(abs_delta, 3),
                'pct': round(pct, 2) if pct is not None else None,
            }
        return ComparisonResult(
            metrics=list(metrics),
            period_a=agg_a.to_dict(), period_b=agg_b.to_dict(), deltas=deltas,
        )

    @staticmethod
    def _representative(metric_stats: dict) -> float:
        """Chọn MỘT con số đại diện để so sánh, tùy loại metric.

        Ưu tiên theo thứ tự: energy (counter) -> value (derived) -> avg -> last.
        Nhờ vậy compare_periods không cần biết metric thuộc loại nào.
        """
        for key in ('energy', 'value', 'avg', 'last'):
            if key in metric_stats:
                return float(metric_stats[key] or 0.0)
        return 0.0
