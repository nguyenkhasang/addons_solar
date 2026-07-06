# -*- coding: utf-8 -*-
"""Các Tool cụ thể — 9 tool "theo năng lực" (capability) mà AI được phép gọi.

Mỗi tool là một lớp bọc mỏng: phân tích tham số -> gọi MỘT service -> trả về dict
của DTO. Thêm metric mới KHÔNG BAO GIỜ đẻ thêm tool ở đây; metric chỉ là một tham
số truyền vào ``get_timeseries`` / ``get_aggregate`` / ...

Vì sao thiết kế theo NĂNG LỰC, không theo câu hỏi?
    Nếu tạo tool kiểu today_report(), compare_week()... thì mỗi câu hỏi mới lại
    phải viết tool mới -> không mở rộng được. Ngược lại, 9 tool tổng quát này ghép
    lại trả lời được vô số câu hỏi, còn AI (Planner) tự quyết định gọi tool nào,
    gọi bao nhiêu lần. Python không điều hướng — LLM mới là bộ lập kế hoạch.
"""
from __future__ import annotations

from .base_tool import Tool
from ..domain.enums import AggregationType, AnomalyMethod, ForecastMethod, Granularity
from ..domain.metric_registry import MetricRegistry
from ..domain.value_objects import TimeRange
from ..services.analytics_service import AnalyticsService
from ..services.anomaly_service import AnomalyService
from ..services.device_service import DeviceService
from ..services.forecast_service import ForecastService
from ..services.health_service import HealthService

# Các mảnh JSON-schema tái sử dụng (để không lặp mô tả tham số ở mọi tool).
_ISO = {'type': 'string',
        'description': (
            'Mốc thời gian. VỚI CÂU HỎI TƯƠNG ĐỐI (gần nhất, hôm nay, hôm qua, N '
            'ngày/giờ qua) HÃY DÙNG TOKEN, đừng tự tính giờ: '
            '"now" (bây giờ), "now-2h" (2 giờ trước), "now-30m", "now-7d" (7 ngày '
            'trước), "today" (00:00 hôm nay), "yesterday", "tomorrow" (dùng làm end '
            'cho trọn ngày hôm nay). '
            'Chỉ khi người dùng nêu NGÀY/GIỜ CỤ THỂ mới dùng ISO 8601 giờ Việt Nam '
            'KHÔNG kèm hậu tố múi giờ, vd "2026-07-02T00:00:00" (không viết Z hay '
            '+07:00). TUYỆT ĐỐI KHÔNG tự trừ 7 giờ — server tự quy đổi.')}
_DEVICE = {'type': 'integer', 'description': 'ID thiết bị (tùy chọn) để giới hạn truy vấn'}
_SYSTEM = {'type': 'integer', 'description': 'ID hệ thống (tùy chọn) để giới hạn truy vấn'}
_METRIC = {'type': 'string',
           'description': 'Khóa metric lấy từ list_metrics (vd output_power, bat_voltage)'}


class ListMetricsTool(Tool):
    name = 'list_metrics'
    description = ('Liệt kê mọi metric hệ thống có thể báo cáo, kèm đơn vị và loại. '
                   'Nên gọi ĐẦU TIÊN để biết có thể truy vấn những gì.')

    def parameters(self):
        return {'type': 'object', 'properties': {}, 'required': []}

    def run(self, **kwargs):
        return {'metrics': MetricRegistry.describe()}


class GetTimeseriesTool(Tool):
    name = 'get_timeseries'
    description = ('Chuỗi thời gian của một metric trên một khoảng. Thay cho các '
                   'endpoint theo từng metric: chỉ cần truyền khóa metric. Độ phân '
                   'giải được chọn tự động. NÊN dùng tool này (thay cho get_aggregate) '
                   'khi hỏi metric thời tiết trên nhiều ngày — nó tự đọc bảng tổng hợp '
                   'theo ngày; metric thời tiết chỉ trả điểm theo NGÀY, không theo giờ.')

    def parameters(self):
        return {
            'type': 'object',
            'properties': {
                'metric': _METRIC,
                'start': _ISO, 'end': _ISO,
                'aggregation': {'type': 'string',
                                'enum': [a.value for a in AggregationType],
                                'description': 'Cách gộp mẫu trong mỗi bucket: '
                                               'avg=trung bình, max=đỉnh, min=đáy, '
                                               'sum=tổng, last=giá trị cuối, '
                                               'first=giá trị đầu. Bỏ trống để dùng '
                                               'mặc định phù hợp theo metric.'},
                'interval': {'type': 'string',
                             'enum': [g.value for g in Granularity],
                             'description': "Độ phân giải bucket thời gian: "
                                            "auto=tự chọn tối ưu (nên dùng), "
                                            "raw=từng bản ghi gốc (~1 phút), "
                                            "hour=theo giờ, day=theo ngày. "
                                            "Mặc định 'auto'."},
                'device_id': _DEVICE, 'system_id': _SYSTEM,
            },
            'required': ['metric', 'start', 'end'],
        }

    def run(self, **kwargs):
        metric = self._require(kwargs, 'metric')
        tr = TimeRange.from_iso(self._require(kwargs, 'start'),
                                self._require(kwargs, 'end'))
        agg = kwargs.get('aggregation')
        gran = Granularity(kwargs.get('interval') or 'auto')
        svc = AnalyticsService(self.env)
        res = svc.get_timeseries(
            metric, tr,
            aggregation=AggregationType(agg) if agg else None,
            granularity=gran,
            device_id=self._opt_int(kwargs, 'device_id'),
            system_id=self._opt_int(kwargs, 'system_id'))
        return res.to_dict()


class GetAggregateTool(Tool):
    name = 'get_aggregate'
    description = ('Thống kê vô hướng (trung bình/nhỏ nhất/lớn nhất/năng lượng) cho '
                   'một hoặc nhiều metric trên một khoảng. Dùng cho báo cáo tổng kết. '
                   'Truyền NHIỀU metric cùng lúc trong "metrics" (vd '
                   '["pv_input","output_power","irradiance"]) thay vì gọi nhiều lần — '
                   'nhanh và nhất quán hơn. LƯU Ý: đọc dữ liệu CHI TIẾT (raw); với '
                   'metric thời tiết (chỉ giữ raw ~7 ngày) khoảng dài hơn có thể trả '
                   'count=0 — khi đó dùng get_timeseries. Luôn kiểm tra "count": '
                   'count=0 nghĩa là KHÔNG có dữ liệu, đừng coi 0 là giá trị đo được.')

    def parameters(self):
        return {
            'type': 'object',
            'properties': {
                'metrics': {'type': 'array', 'items': _METRIC,
                            'description': 'Một hoặc nhiều khóa metric'},
                'start': _ISO, 'end': _ISO,
                'device_id': _DEVICE, 'system_id': _SYSTEM,
            },
            'required': ['metrics', 'start', 'end'],
        }

    def run(self, **kwargs):
        metrics = self._require(kwargs, 'metrics')
        if isinstance(metrics, str):     # cho phép truyền 1 chuỗi thay vì mảng
            metrics = [metrics]
        tr = TimeRange.from_iso(self._require(kwargs, 'start'),
                                self._require(kwargs, 'end'))
        svc = AnalyticsService(self.env)
        return svc.get_aggregate(
            metrics, tr,
            device_id=self._opt_int(kwargs, 'device_id'),
            system_id=self._opt_int(kwargs, 'system_id')).to_dict()


class ComparePeriodsTool(Tool):
    name = 'compare_periods'
    description = ('So sánh các metric giữa HAI khoảng bất kỳ. Xử lý được "hôm nay '
                   'vs hôm qua", "3 ngày gần nhất vs cùng kỳ năm ngoái"... chỉ bằng '
                   'cách truyền 2 khoảng thời gian.')

    def parameters(self):
        return {
            'type': 'object',
            'properties': {
                'metrics': {'type': 'array', 'items': _METRIC},
                'a_start': _ISO, 'a_end': _ISO,
                'b_start': _ISO, 'b_end': _ISO,
                'device_id': _DEVICE, 'system_id': _SYSTEM,
            },
            'required': ['metrics', 'a_start', 'a_end', 'b_start', 'b_end'],
        }

    def run(self, **kwargs):
        metrics = self._require(kwargs, 'metrics')
        if isinstance(metrics, str):
            metrics = [metrics]
        ra = TimeRange.from_iso(self._require(kwargs, 'a_start'),
                                self._require(kwargs, 'a_end'))
        rb = TimeRange.from_iso(self._require(kwargs, 'b_start'),
                                self._require(kwargs, 'b_end'))
        svc = AnalyticsService(self.env)
        return svc.compare_periods(
            metrics, ra, rb,
            device_id=self._opt_int(kwargs, 'device_id'),
            system_id=self._opt_int(kwargs, 'system_id')).to_dict()


class GetDeviceStatusTool(Tool):
    name = 'get_device_status'
    description = 'Trạng thái online/offline hiện tại và thời lượng offline từng thiết bị.'

    def parameters(self):
        return {'type': 'object',
                'properties': {'device_id': _DEVICE, 'system_id': _SYSTEM},
                'required': []}

    def run(self, **kwargs):
        svc = DeviceService(self.env)
        return svc.get_device_status(
            device_id=self._opt_int(kwargs, 'device_id'),
            system_id=self._opt_int(kwargs, 'system_id')).to_dict()


class GetAlarmsTool(Tool):
    name = 'get_alarms'
    description = ('Lịch sử cảnh báo/sự kiện trên một khoảng (mất kết nối, trạng '
                   'thái bất thường).')

    def parameters(self):
        return {
            'type': 'object',
            'properties': {
                'start': _ISO, 'end': _ISO,
                'severity': {'type': 'string', 'enum': ['critical', 'warning', 'info']},
                'device_id': _DEVICE, 'system_id': _SYSTEM,
            },
            'required': ['start', 'end'],
        }

    def run(self, **kwargs):
        tr = TimeRange.from_iso(self._require(kwargs, 'start'),
                                self._require(kwargs, 'end'))
        svc = DeviceService(self.env)
        return svc.get_alarms(
            tr, severity=kwargs.get('severity'),
            device_id=self._opt_int(kwargs, 'device_id'),
            system_id=self._opt_int(kwargs, 'system_id')).to_dict()


class FindAnomaliesTool(Tool):
    name = 'find_anomalies'
    description = ('Phát hiện số đo bất thường của MỘT metric bằng zscore/iqr/'
                   'threshold. Dùng để trả lời "X có gì bất thường không?".')

    def parameters(self):
        return {
            'type': 'object',
            'properties': {
                'metric': _METRIC,
                'start': _ISO, 'end': _ISO,
                'method': {'type': 'string',
                           'enum': [m.value for m in AnomalyMethod]},
                'sensitivity': {'type': 'number',
                                'description': 'Hệ số nhân cho zscore/iqr, hoặc ngưỡng '
                                               'tuyệt đối cho threshold. Mặc định 2.0'},
                'device_id': _DEVICE, 'system_id': _SYSTEM,
            },
            'required': ['metric', 'start', 'end'],
        }

    def run(self, **kwargs):
        metric = self._require(kwargs, 'metric')
        tr = TimeRange.from_iso(self._require(kwargs, 'start'),
                                self._require(kwargs, 'end'))
        method = AnomalyMethod(kwargs.get('method') or 'zscore')
        sensitivity = float(kwargs.get('sensitivity') or 2.0)
        svc = AnomalyService(self.env)
        return svc.find_anomalies(
            metric, tr, method=method, sensitivity=sensitivity,
            device_id=self._opt_int(kwargs, 'device_id'),
            system_id=self._opt_int(kwargs, 'system_id')).to_dict()


class GetHealthScoreTool(Tool):
    name = 'get_health_score'
    description = ('Điểm sức khỏe tổng hợp 0-100 (độ sẵn sàng, nhiệt, sản xuất) '
                   'trên một khoảng.')

    def parameters(self):
        return {
            'type': 'object',
            'properties': {
                'start': _ISO, 'end': _ISO,
                'device_id': _DEVICE, 'system_id': _SYSTEM,
            },
            'required': ['start', 'end'],
        }

    def run(self, **kwargs):
        tr = TimeRange.from_iso(self._require(kwargs, 'start'),
                                self._require(kwargs, 'end'))
        svc = HealthService(self.env)
        return svc.get_health_score(
            tr,
            device_id=self._opt_int(kwargs, 'device_id'),
            system_id=self._opt_int(kwargs, 'system_id')).to_dict()


class ForecastTool(Tool):
    name = 'forecast'
    description = ('Dự báo ngắn hạn (vài giờ tới) cho metric có dữ liệu theo giờ, '
                   'vd công suất, điện áp, nhiệt độ thiết bị. Không dùng cho metric thời tiết.')

    def parameters(self):
        return {
            'type': 'object',
            'properties': {
                'metric': _METRIC,
                'horizon_hours': {'type': 'integer',
                                  'description': 'Số giờ dự báo tới'},
                'lookback_days': {'type': 'integer',
                                  'description': 'Cửa sổ lịch sử. Mặc định 7'},
                'device_id': _DEVICE, 'system_id': _SYSTEM,
            },
            'required': ['metric', 'horizon_hours'],
        }

    def run(self, **kwargs):
        metric = self._require(kwargs, 'metric')
        horizon = int(self._require(kwargs, 'horizon_hours'))
        # Chặn tại nguồn: forecast dựng hồ sơ theo GIỜ nên vô nghĩa với metric chỉ
        # có dữ liệu theo ngày (thời tiết). Trả lỗi rõ thay vì kết quả rác -> LLM
        # không cần "nhớ" quy tắc, tự khắc biết chuyển hướng.
        if MetricRegistry.get(metric).summary_bucket == 'day':
            raise ValueError(
                "Metric '%s' chỉ có dữ liệu theo ngày, không dự báo theo giờ được. "
                "Dùng get_timeseries để xem xu hướng theo ngày." % metric)
        svc = ForecastService(self.env)
        return svc.forecast(
            metric, horizon,
            method=ForecastMethod.NAIVE_SEASONAL,
            lookback_days=int(kwargs.get('lookback_days') or 7),
            device_id=self._opt_int(kwargs, 'device_id'),
            system_id=self._opt_int(kwargs, 'system_id')).to_dict()


# Thứ tự ở đây là thứ tự tool được trình bày cho AI.
ALL_TOOLS = [
    ListMetricsTool,
    GetTimeseriesTool,
    GetAggregateTool,
    ComparePeriodsTool,
    GetDeviceStatusTool,
    GetAlarmsTool,
    FindAnomaliesTool,
    GetHealthScoreTool,
    ForecastTool,
]
