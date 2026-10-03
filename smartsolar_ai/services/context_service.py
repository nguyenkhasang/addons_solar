# -*- coding: utf-8 -*-
from ..domain.enums import Granularity
from ..domain.metric_registry import MetricRegistry
from ..repositories.context_repository import ContextRepository
from .analytics_service import AnalyticsService
from .device_service import DeviceService


class ContextService:
    def __init__(self, env):
        self._context = ContextRepository(env)
        self._analytics = AnalyticsService(env)
        self._devices = DeviceService(env)

    def get_context(self, system_id=None):
        result = self._context.fetch_systems(system_id)
        result['reporting_settings'] = self._context.fetch_reporting_settings()
        result['metric_groups'] = {
            'power': ['pv_input', 'output_power', 'grid_import_power'],
            'energy': ['pv_energy_total', 'energy_exported_total', 'grid_import_energy_total'],
            'battery': ['bat_voltage', 'bat_current'],
            'thermal': ['inverter_temp', 'charger_temp'],
            'weather': [m['key'] for m in MetricRegistry.describe() if not m['has_device']],
        }
        result['limitations'] = [
            'Chưa có SOC, dung lượng pin, loại hóa học pin hoặc ngưỡng vận hành đã xác minh '
            'trong catalog; không suy ra % pin hoặc thời gian cấp tải chỉ từ điện áp.',
            'Công suất định mức hệ thống là cấu hình quản trị, không phải công suất đo.',
            'Điện thu PV là nhánh nạp pin; điện hòa lưới là nhánh inverter cấp tải. '
            'Tổng tải suy ra từ điện hòa lưới + điện lấy lưới; không cộng PV lần nữa.',
        ]
        result['suggested_investigations'] = [
            'Đối chiếu công suất với điện năng cùng khoảng khi công-tơ không tăng.',
            'Kiểm tra freshness, thiết bị và cảnh báo trước khi kết luận số 0 là bình thường.',
            'So sánh các khoảng có cùng độ dài và giờ trong ngày để phân tích sản lượng.',
        ]
        return result

    def get_snapshot(self, metrics, time_range, system_id=None, device_id=None):
        result = self._analytics.get_aggregate(metrics, time_range, device_id, system_id).to_dict()
        result['devices'] = self._devices.get_device_status(device_id, system_id).to_dict()
        if any(m.get('quality', {}).get('device_count', 0) > 1 for m in result['metrics'].values()):
            devices = result['devices']['devices'][:20]
            result['per_device'] = []
            for device in devices:
                selected = [key for key in result['metrics']
                            if MetricRegistry.get(key).raw_model == device['type'].replace('_', '.')]
                if selected:
                    readings = self._analytics.get_aggregate(
                        selected, time_range, device['id'], system_id).to_dict()
                    result['per_device'].append({'device_id': device['id'], 'name': device['name'],
                                                 'metrics': readings['metrics']})
            result['per_device_truncated'] = (len(result['devices']['devices']) > 20
                                               or result['devices']['truncated'])
        result['interpretation'] = (
            'last là mẫu cuối trong cửa sổ; đọc last_observed_at và tuổi mẫu trước khi gọi là hiện tại. '
            'Thiết bị online không chứng minh giá trị đo hoặc công-tơ đang cập nhật đúng. '
            'avg/min/max phản ánh khoảng chọn, không phải giá trị hiện tại.')
        return result

    def get_trends(self, metrics, time_range, system_id=None, device_id=None,
                   interval='auto', max_points=60):
        # Validate metric list using the same contract as aggregate.
        aggregate = self._analytics.get_aggregate(metrics, time_range, device_id, system_id).to_dict()
        result = {'range': aggregate['range'], 'metrics': {}}
        for metric, stats in aggregate['metrics'].items():
            series = self._analytics.get_timeseries(
                metric, time_range, granularity=Granularity(interval),
                device_id=device_id, system_id=system_id, max_points=max_points).to_dict()
            result['metrics'][metric] = {'statistics': stats, 'series': series}
        result['interpretation'] = (
            'Chuỗi đã gom bucket/rút gọn không chứng minh mọi mẫu đều bình thường. '
            'Đọc granularity và truncated. Counter là chỉ số tích lũy; '
            'dùng statistics.energy cho sản lượng, không cộng các điểm counter.')
        return result
