# -*- coding: utf-8 -*-
"""MetricRegistry — "cuốn từ điển" duy nhất định nghĩa metric là gì.

★ ĐÂY LÀ ĐIỂM MỞ RỘNG CỦA TOÀN BỘ THIẾT KẾ ★

Muốn cho AI truy vấn được một đại lượng đo mới (irradiance/bức xạ, humidity/độ ẩm,
SOC/dung lượng pin, wind/gió...)?  -> Chỉ cần THÊM MỘT dòng MetricSpec vào đây.
KHÔNG phải sửa Service, KHÔNG phải sửa Tool, KHÔNG phải sửa Adapter.

Đây chính là hiện thân của nguyên tắc Open/Closed (mở để mở rộng, đóng để sửa đổi):
hệ thống mở rộng bằng cách thêm dữ liệu khai báo, không phải bằng cách sửa code logic.

Mỗi MetricSpec ánh xạ một "khóa metric" công khai (thứ AI dùng để gọi) tới:
  - Nguồn vật lý: đọc từ model/cột nào của bảng raw, và cột nào của bảng summary.
  - Loại metric (tức thời / bộ đếm / dẫn xuất).
  - Đơn vị + cách gộp mặc định để hiển thị.
  - Với metric DẪN XUẤT: công thức tính từ các metric khác.

Nhận thức "Hybrid" (2 thiết bị) nằm HOÀN TOÀN Ở ĐÂY, không rải ra chỗ khác:
    Ví dụ ``output_power`` đọc từ grid_tie_inverter (phần hòa lưới),
    còn ``pv_input`` đọc từ charge_power (phần PV nạp pin).
    Các metric dẫn xuất như ``self_consumption`` diễn đạt mối quan hệ hybrid bằng
    một công thức khai báo -> quy tắc "không đếm trùng" được định nghĩa ĐÚNG MỘT LẦN.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from .enums import AggregationType, MetricKind


@dataclass(frozen=True)
class MetricSpec:
    """Bản đặc tả (blueprint) cho một metric. Bất biến — chỉ đọc sau khi khai báo."""
    key: str                       # tên công khai, ổn định, AI dùng để gọi
    label: str                     # nhãn tiếng Việt cho người đọc (không dùng trong logic)
    unit: str                      # đơn vị: W, V, A, kWh, °C, %...
    kind: MetricKind               # tức thời / bộ đếm / dẫn xuất
    default_aggregation: AggregationType = AggregationType.AVG  # cách gộp mặc định

    # --- Nguồn dữ liệu (cho loại INSTANTANEOUS / COUNTER) ---
    raw_model: Optional[str] = None       # vd 'grid.tie.inverter'
    raw_field: Optional[str] = None       # vd 'output_power'
    summary_model: Optional[str] = None   # vd 'grid.tie.inverter.summary'
    summary_field: Optional[str] = None   # vd 'output_power_avg'
    summary_max_field: Optional[str] = None   # cột dùng khi gộp MAX trên bảng summary

    # Bảng nguồn có cột device_id để lọc theo thiết bị không? Đa số bảng thiết bị
    # có (True). Bảng cấp hệ thống như dữ liệu môi trường CHỈ có system_id -> False,
    # để Repository không sinh mệnh đề WHERE device_id trên cột không tồn tại.
    has_device: bool = True

    # --- Dành cho metric DẪN XUẤT (DERIVED) ---
    # formula(context: dict) -> float; các khóa trong context là tên metric phụ thuộc.
    formula: Optional[Callable[[dict], float]] = None
    depends_on: tuple = field(default_factory=tuple)

    @property
    def is_derived(self) -> bool:
        """True nếu đây là metric dẫn xuất (tính bằng công thức, không có cột riêng)."""
        return self.kind == MetricKind.DERIVED


def _safe_div(numerator, denominator, default=0.0):
    """Chia an toàn: mẫu số bằng 0 hoặc lỗi kiểu -> trả về giá trị mặc định.

    Cần thiết vì công thức KPI hay chia cho tổng sản lượng, mà ban đêm/không có
    dữ liệu thì tổng có thể bằng 0 -> tránh vỡ toàn bộ báo cáo chỉ vì 1 phép chia.
    """
    try:
        return numerator / denominator if denominator else default
    except (TypeError, ZeroDivisionError):
        return default


# --------------------------------------------------------------------------
# DANH MỤC METRIC (METRIC CATALOG)
# Thêm cảm biến vật lý mới hoặc KPI dẫn xuất mới TẠI ĐÂY — không đụng nơi khác.
# --------------------------------------------------------------------------
_METRICS = {

    # ---- Grid Tie Inverter: phía HÒA LƯỚI của chuỗi hybrid ----
    'output_power': MetricSpec(
        key='output_power', label='Công suất hòa lưới', unit='W',
        kind=MetricKind.INSTANTANEOUS, default_aggregation=AggregationType.AVG,
        raw_model='grid.tie.inverter', raw_field='output_power',
        summary_model='grid.tie.inverter.summary',
        summary_field='output_power_avg', summary_max_field='output_power_max',
    ),
    'grid_import_power': MetricSpec(
        key='grid_import_power', label='Công suất lấy lưới', unit='W',
        kind=MetricKind.INSTANTANEOUS,
        raw_model='grid.tie.inverter', raw_field='limiter_power',
        summary_model='grid.tie.inverter.summary', summary_field='limiter_power_avg',
    ),
    'dc_voltage': MetricSpec(
        key='dc_voltage', label='Điện áp DC', unit='V',
        kind=MetricKind.INSTANTANEOUS,
        raw_model='grid.tie.inverter', raw_field='dc_voltage',
        summary_model='grid.tie.inverter.summary',
        summary_field='dc_voltage_avg', summary_max_field='dc_voltage_max',
    ),
    'ac_voltage': MetricSpec(
        key='ac_voltage', label='Điện áp AC', unit='V',
        kind=MetricKind.INSTANTANEOUS,
        raw_model='grid.tie.inverter', raw_field='ac_voltage',
        summary_model='grid.tie.inverter.summary', summary_field='ac_voltage_avg',
    ),
    'inverter_temp': MetricSpec(
        key='inverter_temp', label='Nhiệt độ Inverter', unit='°C',
        kind=MetricKind.INSTANTANEOUS,
        raw_model='grid.tie.inverter', raw_field='temperature',
        summary_model='grid.tie.inverter.summary',
        summary_field='temperature_avg', summary_max_field='temperature_max',
    ),
    'energy_exported_total': MetricSpec(
        key='energy_exported_total', label='Tổng năng lượng hòa lưới', unit='kWh',
        kind=MetricKind.COUNTER, default_aggregation=AggregationType.LAST,
        raw_model='grid.tie.inverter', raw_field='energy_total',
        summary_model='grid.tie.inverter.summary', summary_field='energy_total_end',
    ),

    # ---- Charge Power / MPPT: phía PV NẠP + PIN ----
    'pv_input': MetricSpec(
        key='pv_input', label='Công suất PV nạp', unit='W',
        kind=MetricKind.INSTANTANEOUS,
        raw_model='charge.power', raw_field='charge_power',
        summary_model='charge.power.summary',
        summary_field='charge_power_avg', summary_max_field='charge_power_max',
    ),
    'pv_voltage': MetricSpec(
        key='pv_voltage', label='Điện áp PV', unit='V',
        kind=MetricKind.INSTANTANEOUS,
        raw_model='charge.power', raw_field='pv_voltage',
        summary_model='charge.power.summary',
        summary_field='pv_voltage_avg', summary_max_field='pv_voltage_max',
    ),
    'pv_current': MetricSpec(
        key='pv_current', label='Dòng điện PV', unit='A',
        kind=MetricKind.INSTANTANEOUS,
        raw_model='charge.power', raw_field='pv_current',
        summary_model='charge.power.summary', summary_field='pv_current_avg',
    ),
    'bat_voltage': MetricSpec(
        key='bat_voltage', label='Điện áp Pin', unit='V',
        kind=MetricKind.INSTANTANEOUS,
        raw_model='charge.power', raw_field='bat_voltage',
        summary_model='charge.power.summary', summary_field='bat_voltage_avg',
    ),
    'bat_current': MetricSpec(
        key='bat_current', label='Dòng điện Pin', unit='A',
        kind=MetricKind.INSTANTANEOUS,
        raw_model='charge.power', raw_field='bat_current',
        summary_model='charge.power.summary', summary_field='bat_current_avg',
    ),
    'charger_temp': MetricSpec(
        key='charger_temp', label='Nhiệt độ Bộ sạc', unit='°C',
        kind=MetricKind.INSTANTANEOUS,
        raw_model='charge.power', raw_field='temperature',
        summary_model='charge.power.summary',
        summary_field='temperature_avg', summary_max_field='temperature_max',
    ),
    'pv_energy_total': MetricSpec(
        key='pv_energy_total', label='Tổng năng lượng PV', unit='kWh',
        kind=MetricKind.COUNTER, default_aggregation=AggregationType.LAST,
        raw_model='charge.power', raw_field='total_kwh',
        summary_model='charge.power.summary', summary_field='total_kwh_end',
    ),

    # ---- Môi trường (thời tiết): nguồn smartsolar.environment ----
    # Bảng cấp HỆ THỐNG (chỉ có system_id, KHÔNG có device_id) -> has_device=False.
    # Không có bảng summary -> Repository tự đọc bảng raw theo record_date.
    # Dùng để đối chiếu điều kiện thời tiết với sản lượng/sạc (vd nắng tốt mà PV thấp).
    'irradiance': MetricSpec(
        key='irradiance', label='Bức xạ sóng ngắn (tổng ngày)', unit='MJ/m²',
        kind=MetricKind.INSTANTANEOUS, default_aggregation=AggregationType.MAX,
        raw_model='smartsolar.environment', raw_field='shortwave_radiation_sum',
        has_device=False,
    ),
    'cloud_cover': MetricSpec(
        key='cloud_cover', label='Mây che phủ', unit='%',
        kind=MetricKind.INSTANTANEOUS, default_aggregation=AggregationType.AVG,
        raw_model='smartsolar.environment', raw_field='cloud_cover',
        has_device=False,
    ),
    'ambient_temp': MetricSpec(
        key='ambient_temp', label='Nhiệt độ môi trường', unit='°C',
        kind=MetricKind.INSTANTANEOUS, default_aggregation=AggregationType.AVG,
        raw_model='smartsolar.environment', raw_field='temperature_2m',
        has_device=False,
    ),
    'humidity': MetricSpec(
        key='humidity', label='Độ ẩm tương đối', unit='%',
        kind=MetricKind.INSTANTANEOUS, default_aggregation=AggregationType.AVG,
        raw_model='smartsolar.environment', raw_field='relative_humidity_2m',
        has_device=False,
    ),
    'uv_index': MetricSpec(
        key='uv_index', label='Chỉ số UV (tối đa ngày)', unit='',
        kind=MetricKind.INSTANTANEOUS, default_aggregation=AggregationType.MAX,
        raw_model='smartsolar.environment', raw_field='uv_index_max',
        has_device=False,
    ),
    'sunshine_duration': MetricSpec(
        key='sunshine_duration', label='Thời lượng nắng', unit='giây',
        kind=MetricKind.INSTANTANEOUS, default_aggregation=AggregationType.MAX,
        raw_model='smartsolar.environment', raw_field='sunshine_duration',
        has_device=False,
    ),
    'wind_speed': MetricSpec(
        key='wind_speed', label='Tốc độ gió 10m', unit='km/h',
        kind=MetricKind.INSTANTANEOUS, default_aggregation=AggregationType.AVG,
        raw_model='smartsolar.environment', raw_field='wind_speed_10m',
        has_device=False,
    ),

    # ---- KPI DẪN XUẤT: quan hệ hybrid diễn đạt MỘT LẦN dưới dạng công thức ----
    # Ngữ nghĩa năng lượng: trong một khoảng, các cột energy_kwh đã là tích phân
    # năng lượng của từng bucket, nên các công thức này thao tác trên SUM(energy)
    # của cả khoảng — do AnalyticsService cung cấp làm "context" tính toán.
    'self_consumption_pct': MetricSpec(
        key='self_consumption_pct', label='Tỷ lệ tự dùng', unit='%',
        kind=MetricKind.DERIVED,
        depends_on=('pv_energy', 'grid_export_energy'),
        # Tự dùng = (PV sản xuất - phần xuất lưới) / PV sản xuất * 100
        formula=lambda c: _safe_div(
            (c.get('pv_energy', 0.0) - c.get('grid_export_energy', 0.0)),
            c.get('pv_energy', 0.0)) * 100.0,
    ),
    'grid_dependency_pct': MetricSpec(
        key='grid_dependency_pct', label='Phụ thuộc lưới', unit='%',
        kind=MetricKind.DERIVED,
        depends_on=('grid_import_energy', 'load_energy'),
        # Phụ thuộc lưới = điện lấy từ lưới / (lấy lưới + tự dùng) * 100
        formula=lambda c: _safe_div(
            c.get('grid_import_energy', 0.0),
            c.get('grid_import_energy', 0.0) + c.get('load_energy', 0.0)) * 100.0,
    ),
}


class MetricRegistry:
    """Bộ truy cập CHỈ-ĐỌC lên danh mục metric.

    Đóng gói ``_METRICS`` sau một lớp API tĩnh để: (1) không ai sửa được danh mục
    lúc chạy, (2) báo lỗi rõ ràng khi tra metric không tồn tại, (3) cung cấp hàm
    ``describe()`` cho AI tự khám phá metric nào đang có.
    """

    @staticmethod
    def get(key: str) -> MetricSpec:
        """Lấy MetricSpec theo khóa. Không tồn tại -> ném KeyError kèm gợi ý.

        Thông báo lỗi liệt kê luôn các metric hợp lệ để AI/dev tự sửa lại lời gọi.
        """
        spec = _METRICS.get(key)
        if spec is None:
            raise KeyError(
                "Metric '%s' không tồn tại. Các metric hợp lệ: %s"
                % (key, ', '.join(sorted(_METRICS)))
            )
        return spec

    @staticmethod
    def exists(key: str) -> bool:
        """Kiểm tra một khóa metric có trong danh mục không."""
        return key in _METRICS

    @staticmethod
    def all() -> dict:
        """Trả về bản sao toàn bộ danh mục (tránh lộ dict gốc cho bên ngoài sửa)."""
        return dict(_METRICS)

    @staticmethod
    def keys() -> list:
        """Danh sách khóa metric, đã sắp xếp."""
        return sorted(_METRICS)

    @staticmethod
    def describe() -> list:
        """Danh mục rút gọn cho tool ``list_metrics`` — để AI TỰ khám phá.

        Nhờ hàm này, khi bạn thêm một metric mới vào ``_METRICS``, AI lập tức
        "nhìn thấy" nó qua list_metrics mà không cần sửa bất cứ code nào khác.
        """
        out = []
        for key in sorted(_METRICS):
            spec = _METRICS[key]
            out.append({
                'key': spec.key,
                'label': spec.label,
                'unit': spec.unit,
                'kind': spec.kind.value,
                'default_aggregation': spec.default_aggregation.value,
                'derived': spec.is_derived,
                # False -> metric cấp hệ thống (vd môi trường), đừng truyền device_id.
                'has_device': spec.has_device,
            })
        return out
