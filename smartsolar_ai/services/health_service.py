# -*- coding: utf-8 -*-
"""HealthService — điểm sức khỏe tổng hợp 0..100 của hệ thống trên một khoảng.

Điểm là tổ hợp CÓ TRỌNG SỐ của các thành phần được tính độc lập. Muốn thêm một
thành phần mới (vd điểm hiệu suất chuẩn hóa theo bức xạ khi có cảm biến irradiance)
thì chỉ việc thêm một hàm ``_*_component`` và cộng nó vào ``components`` — công
thức tổng hợp không đổi. Đây là cách mở rộng an toàn theo Open/Closed.

Mỗi thành phần trả về dict {score: 0..100, weight: trọng số, detail: mô tả ngắn}.
Điểm cuối = tổng(score * weight) / tổng(weight).
"""
from __future__ import annotations

from ..domain.dto import HealthResult
from ..domain.enums import AggregationType
from ..domain.metric_registry import MetricRegistry
from ..domain.value_objects import TimeRange
from ..repositories.device_repository import DeviceRepository
from ..repositories.metric_repository import MetricRepository


class HealthService:
    def __init__(self, env):
        self._metric_repo = MetricRepository(env)
        self._device_repo = DeviceRepository(env)

    def get_health_score(self, time_range: TimeRange,
                         device_id=None, system_id=None) -> HealthResult:
        """Tính điểm sức khỏe tổng hợp từ 3 thành phần: sẵn sàng, nhiệt, sản xuất.

        Trọng số hiện tại: availability 0.4, thermal 0.2, production 0.4.
        """
        components = {}

        components['availability'] = self._availability_component(system_id, device_id)
        components['thermal'] = self._thermal_component(time_range, device_id, system_id)
        components['production'] = self._production_component(time_range, device_id, system_id)

        total_weight = sum(c['weight'] for c in components.values())
        score = sum(c['score'] * c['weight'] for c in components.values())
        score = round(score / total_weight, 1) if total_weight else 0.0

        return HealthResult(
            range_local=[time_range.start_local_iso(), time_range.end_local_iso()],
            score=score, components=components, device_id=device_id,
        )

    def _availability_component(self, system_id, device_id):
        """Độ sẵn sàng = tỷ lệ thiết bị đang online (0..100). Trọng số 0.4."""
        devices = self._device_repo.fetch_devices(system_id)
        if device_id:
            devices = [d for d in devices if d['id'] == device_id]
        if not devices:
            return {'score': 0.0, 'weight': 0.4, 'detail': 'không có thiết bị'}
        online = sum(1 for d in devices if d['online'])
        score = online / len(devices) * 100.0
        return {'score': round(score, 1), 'weight': 0.4,
                'detail': '%d/%d online' % (online, len(devices))}

    def _thermal_component(self, time_range, device_id, system_id):
        """Điểm nhiệt: phạt khi inverter chạy nóng. 100đ ở <=45°C, 0đ ở >=75°C.

        Không có dữ liệu nhiệt -> mặc định 100 (không có bằng chứng quá nhiệt).
        Trọng số 0.2.
        """
        spec = MetricRegistry.get('inverter_temp')
        stats = self._metric_repo.fetch_scalar(spec, time_range, device_id, system_id)
        tmax = stats['max']
        if stats['count'] == 0:
            return {'score': 100.0, 'weight': 0.2, 'detail': 'không có dữ liệu'}
        score = max(0.0, min(100.0, (75.0 - tmax) / 30.0 * 100.0))
        return {'score': round(score, 1), 'weight': 0.2,
                'detail': 'nhiệt độ đỉnh %.1f°C' % tmax}

    def _production_component(self, time_range, device_id, system_id):
        """Điểm sản xuất: hệ có phát điện ổn định không.

        Ước lượng theo tỷ lệ công suất trung bình / công suất đỉnh (avg/max). Tỷ lệ
        cao nghĩa là chuỗi PV hoạt động đều. Không có dữ liệu -> 0đ. Trọng số 0.4.
        """
        spec = MetricRegistry.get('output_power')
        stats = self._metric_repo.fetch_scalar(spec, time_range, device_id, system_id)
        if stats['count'] == 0:
            return {'score': 0.0, 'weight': 0.4, 'detail': 'không có dữ liệu'}
        producing_score = min(100.0, stats['avg'] / max(stats['max'], 1.0) * 100.0)
        return {'score': round(producing_score, 1), 'weight': 0.4,
                'detail': 'TB %.0fW / đỉnh %.0fW' % (stats['avg'], stats['max'])}
