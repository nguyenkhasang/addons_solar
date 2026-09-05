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

import logging
import math
from datetime import timedelta

from ..domain.dto import AggregateResult, ComparisonResult, SeriesResult
from ..domain.enums import AggregationType, Granularity, MetricKind
from ..domain.metric_registry import MetricRegistry
from ..domain.value_objects import TimeRange
from ..repositories.metric_repository import MetricRepository

_logger = logging.getLogger(__name__)

_DEFAULT_MAX_POINTS = 240
_MAX_ALLOWED_POINTS = 500
_AUTO_RAW_MAX_HOURS = 6


# Bảng tra "nguồn năng lượng" cho metric dẫn xuất: ánh xạ tên biến phụ thuộc trong
# công thức -> khóa metric COUNTER thực tế để lấy số kWh. Toàn bộ logic năng lượng
# hybrid gom một chỗ tại đây, khớp với công thức khai báo trong metric_registry.
#
# Không ánh xạ placeholder sang counter khác. ``grid_export_energy`` không có
# công-tơ thật nên nằm trong ``_PLACEHOLDER_DEPS`` và KPI phụ thuộc nó sẽ
# unavailable. ``limiter_total`` được công bố để kiểm tra số đo thô, nhưng KPI
# phụ thuộc lưới vẫn ``supported=False`` tới khi xác minh được ý nghĩa cảm biến.
_ENERGY_SOURCES = {
    'pv_energy': 'pv_energy_total',
    'grid_import_energy': 'grid_import_energy_total',
    'load_energy': 'energy_exported_total',
}

# Các biến phụ thuộc KHÔNG có nguồn đo thật. KPI nào phụ thuộc chúng thì kết quả
# VÔ NGHĨA về mặt vật lý, nên ``_compute_derived`` trả value=None kèm 'note' —
# LLM sẽ nói "chưa đủ dữ liệu" chứ không báo số sai cho người dùng.
# Khi có cảm biến thật: sửa ánh xạ ở trên RỒI bỏ khóa tương ứng khỏi set này.
_PLACEHOLDER_DEPS = frozenset({'grid_export_energy'})


class AnalyticsService:
    def __init__(self, env):
        self._repo = MetricRepository(env)

    # ---- Chuỗi thời gian ---------------------------------------------------
    def get_timeseries(self, metric: str, time_range: TimeRange,
                       aggregation: AggregationType = None,
                       granularity: Granularity = Granularity.AUTO,
                       device_id=None, system_id=None,
                       max_points: int = _DEFAULT_MAX_POINTS) -> SeriesResult:
        """Lấy chuỗi thời gian, kèm trạng thái và giới hạn kích thước kết quả.

        Metric dẫn xuất chỉ có giá trị tổng hợp cho cả khoảng, không có chuỗi
        mịn; kết quả vì vậy tối đa một điểm với ``granularity='range'``.
        """
        if max_points is not None:
            max_points = int(max_points or _DEFAULT_MAX_POINTS)
            if max_points < 20 or max_points > _MAX_ALLOWED_POINTS:
                raise ValueError(
                    'max_points phải nằm trong khoảng 20..%d' % _MAX_ALLOWED_POINTS)
        spec = MetricRegistry.get(metric)
        if spec.kind == MetricKind.COUNTER and aggregation == AggregationType.SUM:
            raise ValueError(
                "Không dùng aggregation='sum' cho counter tích lũy '%s'; "
                "hãy dùng get_aggregate để lấy năng lượng trong khoảng." % metric)
        if spec.is_derived:
            agg = self.get_aggregate([metric], time_range, device_id, system_id)
            derived = agg.metrics[metric]
            available = derived.get('available', True)
            points = []
            if available:
                points.append({
                    't': time_range.end_local_iso(),
                    'v': derived['value'],
                })
            return SeriesResult(
                metric=metric, unit=spec.unit,
                aggregation='derived', granularity='range',
                range_local=[time_range.start_local_iso(), time_range.end_local_iso()],
                points=points, device_id=device_id,
                available=available, reason=derived.get('reason'),
                original_count=len(points), truncated=False,
            )

        # Dùng cách gộp thực tế do Repository quyết định để nhãn kết quả không
        # nói ``sum`` trong khi dữ liệu tức thời đã được bảo vệ bằng ``avg``.
        agg = self._repo.resolve_aggregation(spec, aggregation)
        query_granularity = granularity
        # AUTO cho LLM ưu tiên context gọn: quá 6 giờ thì dùng summary theo giờ.
        # Người gọi vẫn có thể yêu cầu raw rõ ràng; kết quả sau đó vẫn bị giới hạn
        # max_points để không làm tràn context model local.
        if (granularity == Granularity.AUTO and spec.summary_model
                and spec.summary_bucket != 'day'
                and time_range.duration > timedelta(hours=_AUTO_RAW_MAX_HOURS)):
            query_granularity = Granularity.HOUR
        resolved_gran = self._repo.resolve_granularity(
            spec, time_range, query_granularity)
        points = self._repo.fetch_series(
            spec, time_range, agg, query_granularity, device_id, system_id)
        original_count = len(points)
        if max_points is not None:
            points = self._downsample(points, max_points)
        available = bool(points)
        return SeriesResult(
            metric=metric, unit=spec.unit,
            aggregation=agg.value, granularity=resolved_gran.value,
            range_local=[time_range.start_local_iso(), time_range.end_local_iso()],
            points=[{'t': p.label_local(), 'v': round(p.value, 3)} for p in points],
            device_id=device_id, available=available,
            reason=None if available else 'Không có dữ liệu trong khoảng yêu cầu.',
            original_count=original_count,
            truncated=original_count > len(points),
        )

    @staticmethod
    def _downsample(points, max_points):
        """Rút đều chuỗi, giữ điểm đầu/cuối và không vượt ``max_points``."""
        if len(points) <= max_points:
            return points
        if max_points == 1:
            return [points[-1]]
        last = len(points) - 1
        indexes = [round(i * last / (max_points - 1)) for i in range(max_points)]
        return [points[i] for i in indexes]

    # ---- Thống kê vô hướng -------------------------------------------------
    def get_aggregate(self, metrics, time_range: TimeRange,
                      device_id=None, system_id=None) -> AggregateResult:
        """Tính thống kê vô hướng cho một hay nhiều metric trên một khoảng.

        Phân nhánh theo loại metric:
          - DERIVED: tính bằng công thức (qua ``_compute_derived``).
          - COUNTER: trả 'energy' (kWh tích lũy trong khoảng) + 'last'.
          - INSTANTANEOUS: trả avg/min/max/last.

        Hàm này KHÔNG nhận tham số cách gộp: mỗi loại metric trả về đúng bộ
        thống kê hợp ngữ nghĩa của nó (nên không có chuyện "sum công suất").

        Hai nhánh đo trực tiếp (COUNTER, INSTANTANEOUS) kèm 'count' — count=0
        nghĩa là không có dữ liệu đo, KHÔNG phải giá trị 0. Nhánh DERIVED không
        có 'count' vì không đọc bản ghi thô; thiếu dữ liệu ở đó biểu thị bằng
        value=None kèm ``available=False`` và lý do có cấu trúc.
        """
        result = {}
        for metric in metrics:
            spec = MetricRegistry.get(metric)
            if spec.is_derived:
                result[metric] = self._compute_derived(
                    spec, time_range, device_id, system_id)
            elif spec.kind == MetricKind.COUNTER:
                energy = self._repo.fetch_energy_result(
                    spec, time_range, device_id, system_id)
                stats = self._repo.fetch_scalar(
                    spec, time_range, device_id, system_id)
                available = energy['available'] or bool(stats['count'])
                result[metric] = {
                    'unit': spec.unit,
                    'energy': round(energy['value'], 3) if energy['available'] else None,
                    'last': round(stats['last'], 3) if stats['count'] else None,
                    # Với khoảng dài raw có thể đã dọn nhưng summary vẫn còn. Dùng
                    # count summary làm tín hiệu availability thay vì trả count=0 giả.
                    'count': stats['count'] or energy['count'],
                    'available': available,
                    'source': energy['source'],
                    'reason': None if available else 'Không có dữ liệu trong khoảng yêu cầu.',
                }
            else:
                stats = self._repo.fetch_scalar(
                    spec, time_range, device_id, system_id)
                available = bool(stats['count'])
                result[metric] = {
                    'unit': spec.unit,
                    'avg': round(stats['avg'], 3) if available else None,
                    'min': round(stats['min'], 3) if available else None,
                    'max': round(stats['max'], 3) if available else None,
                    'last': round(stats['last'], 3) if available else None,
                    'count': stats['count'], 'available': available,
                    'reason': None if available else 'Không có dữ liệu trong khoảng yêu cầu.',
                }
        return AggregateResult(
            range_local=[time_range.start_local_iso(), time_range.end_local_iso()],
            metrics=result, device_id=device_id,
        )

    def _compute_derived(self, spec, time_range, device_id, system_id) -> dict:
        """Tính metric dẫn xuất, bảo toàn trạng thái thiếu cảm biến/dữ liệu."""
        if not spec.supported:
            return {
                'unit': spec.unit,
                'value': None,
                'available': False,
                'reason': spec.note or 'Metric chưa được hệ thống hỗ trợ.',
                'inputs': {},
                'missing_inputs': list(spec.depends_on),
            }

        context = {}
        input_status = {}
        missing_sensor = []
        missing_data = []
        for dep in spec.depends_on:
            source_metric = _ENERGY_SOURCES.get(dep)
            if dep in _PLACEHOLDER_DEPS:
                missing_sensor.append(dep)
                input_status[dep] = {
                    'available': False,
                    'source_metric': None,
                    'reason': 'Chưa có cảm biến/bộ đếm thật.',
                }
                continue
            if not source_metric or not MetricRegistry.exists(source_metric):
                missing_sensor.append(dep)
                input_status[dep] = {
                    'available': False,
                    'source_metric': source_metric,
                    'reason': 'Chưa cấu hình nguồn dữ liệu.',
                }
                continue

            src_spec = MetricRegistry.get(source_metric)
            energy = self._repo.fetch_energy_result(
                src_spec, time_range, device_id, system_id)
            input_status[dep] = dict(energy, source_metric=source_metric)
            if not energy['available']:
                missing_data.append(dep)
                continue
            context[dep] = energy['value']

        missing = missing_sensor + missing_data
        if missing:
            if missing_sensor:
                reason = 'Chưa có bộ đếm thật cho: %s.' % ', '.join(missing_sensor)
            else:
                reason = (
                    'Không có dữ liệu trong khoảng yêu cầu cho: %s.'
                    % ', '.join(missing_data))
            return {
                'unit': spec.unit,
                'value': None,
                'available': False,
                'reason': reason,
                'inputs': context,
                'input_status': input_status,
                'missing_inputs': missing,
            }

        try:
            value = float(spec.formula(context))
            if not math.isfinite(value):
                raise ValueError('Kết quả không hữu hạn')
        except Exception:  # noqa: BLE001 - đổi lỗi công thức thành trạng thái có cấu trúc
            _logger.exception('Không thể tính derived metric %s', spec.key)
            return {
                'unit': spec.unit,
                'value': None,
                'available': False,
                'reason': 'Không thể tính metric do lỗi công thức.',
                'inputs': context,
                'input_status': input_status,
                'missing_inputs': [],
            }

        warning = spec.note or None
        if spec.unreliable and not warning:
            warning = (
                'Số liệu tham khảo: giả định dòng năng lượng chưa được xác minh.')
        return {
            'unit': spec.unit,
            'value': round(value, 3),
            'available': True,
            'reason': warning,
            'inputs': context,
            'input_status': input_status,
            'missing_inputs': [],
        }

    # ---- So sánh 2 kỳ ------------------------------------------------------
    def compare_periods(self, metrics, range_a: TimeRange, range_b: TimeRange,
                        device_id=None, system_id=None) -> ComparisonResult:
        """So sánh các metric giữa 2 khoảng thời gian BẤT KỲ.

        Nhờ nhận 2 khoảng tùy ý, một hàm này trả lời được vô số câu: hôm nay vs
        hôm qua, tuần này vs tuần trước, "3 ngày gần nhất vs cùng kỳ năm ngoái"...

        Trường delta được đặt tên CHỈ RÕ CHIỀU để LLM khỏi đảo ngược:
          - a / b                : giá trị bên A / bên B (đã qua _representative)
          - a_minus_b            : A - B (dương = A lớn hơn B)
          - pct_change_a_vs_b    : (A - B) / B * 100 (dương = A lớn hơn B)
        Giữ cả tên trường ngắn ``abs/pct`` của API hiện tại và tên tường minh
        ``a_minus_b/pct_change_a_vs_b`` để tương thích với thay đổi local.
        """
        agg_a = self.get_aggregate(metrics, range_a, device_id, system_id)
        agg_b = self.get_aggregate(metrics, range_b, device_id, system_id)
        deltas = {}
        for metric in metrics:
            stats_a = agg_a.metrics[metric]
            stats_b = agg_b.metrics[metric]
            a = self._representative(stats_a)
            b = self._representative(stats_b)
            if a is None or b is None:
                reason = (stats_a.get('reason') or stats_b.get('reason')
                          or 'Không đủ dữ liệu để so sánh.')
                deltas[metric] = {
                    'a': a, 'b': b, 'abs': None, 'pct': None,
                    'a_minus_b': None, 'pct_change_a_vs_b': None,
                    'available': False,
                    'reason': reason, 'note': reason,
                }
                continue
            abs_delta = a - b
            pct = (abs_delta / b * 100.0) if b else None
            rounded_abs = round(abs_delta, 3)
            rounded_pct = round(pct, 2) if pct is not None else None
            deltas[metric] = {
                'a': round(a, 3), 'b': round(b, 3),
                'abs': rounded_abs, 'pct': rounded_pct,
                'a_minus_b': rounded_abs,
                'pct_change_a_vs_b': rounded_pct,
                'available': True, 'reason': None, 'note': None,
            }
        return ComparisonResult(
            metrics=list(metrics),
            period_a=agg_a.to_dict(), period_b=agg_b.to_dict(), deltas=deltas,
        )

    @staticmethod
    def _representative(metric_stats: dict):
        """Chọn một số đại diện; thiếu dữ liệu luôn được giữ là ``None``."""
        if metric_stats.get('available') is False:
            return None
        for key in ('energy', 'value', 'avg', 'last'):
            value = metric_stats.get(key)
            if value is not None:
                return float(value)
        return None
