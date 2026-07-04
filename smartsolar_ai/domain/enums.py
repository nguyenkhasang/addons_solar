# -*- coding: utf-8 -*-
"""Các Enum của tầng domain cho SmartSolar AI Tool Layer.

Đây là các Enum Python thuần, KHÔNG phụ thuộc Odoo, để tầng domain có thể
unit-test độc lập (không cần khởi động cả Odoo).

Vì sao tách riêng enum ra đây?
    Toàn bộ hệ thống dùng chung một "từ vựng" cố định: cách gộp dữ liệu, độ phân
    giải thời gian, loại metric, phương pháp phát hiện bất thường... Gom vào một
    chỗ giúp Tool/Service/Repository nói cùng ngôn ngữ, tránh dùng chuỗi tự do
    (magic string) rải rác gây sai chính tả, khó refactor.
"""
from enum import Enum


class Granularity(str, Enum):
    """Độ phân giải bucket thời gian cho một truy vấn chuỗi thời gian.

    ``AUTO`` để Repository TỰ chọn nguồn rẻ nhất (bảng raw hay bảng summary) dựa
    trên độ dài khoảng thời gian yêu cầu. AI không bao giờ phải tự chọn cái này.

    Kế thừa ``str`` để giá trị enum vừa là Enum vừa là chuỗi -> serialize JSON
    trực tiếp ra "auto"/"hour"... mà không cần convert thủ công.
    """
    AUTO = 'auto'
    RAW = 'raw'       # dữ liệu gốc 1 bản ghi / phút, từ bảng raw
    HOUR = 'hour'     # từ bảng *_summary, bucket_type='hour'
    DAY = 'day'       # từ bảng *_summary, bucket_type='day'


class AggregationType(str, Enum):
    """Cách gộp nhiều mẫu (sample) trong cùng một bucket thành một con số.

    Ví dụ: trong 1 giờ có 60 mẫu công suất -> AVG lấy trung bình, MAX lấy đỉnh...
    """
    AVG = 'avg'       # trung bình
    MAX = 'max'       # giá trị lớn nhất
    MIN = 'min'       # giá trị nhỏ nhất
    SUM = 'sum'       # tổng
    LAST = 'last'     # giá trị cuối cùng trong bucket
    FIRST = 'first'   # giá trị đầu tiên trong bucket


class MetricKind(str, Enum):
    """Bản chất vật lý của một metric — quyết định cách gộp / cách so sánh.

    - INSTANTANEOUS (tức thời): giá trị đo tại một thời điểm (công suất, điện áp,
      nhiệt độ). Muốn tính năng lượng trên một khoảng thì phải tích phân; giá trị
      đại diện thường là avg/max.
    - COUNTER (bộ đếm tăng dần): tổng tích lũy suốt đời thiết bị và chỉ tăng
      (energy_total). Năng lượng trong một khoảng = giá_trị_cuối - giá_trị_đầu.
    - DERIVED (dẫn xuất): tính ra từ các metric khác bằng công thức
      (self_consumption, grid_dependency...). Không có cột dữ liệu riêng.
    """
    INSTANTANEOUS = 'instantaneous'
    COUNTER = 'counter'
    DERIVED = 'derived'


class AnomalyMethod(str, Enum):
    """Phương pháp phát hiện điểm bất thường trong một chuỗi số."""
    ZSCORE = 'zscore'        # lệch bao nhiêu độ lệch chuẩn so với trung bình
    IQR = 'iqr'              # nằm ngoài khoảng tứ phân vị (chống nhiễu tốt hơn)
    THRESHOLD = 'threshold'  # vượt một ngưỡng tuyệt đối cho trước


class ForecastMethod(str, Enum):
    """Phương pháp dự báo ngắn hạn."""
    NAIVE_SEASONAL = 'naive_seasonal'   # trung bình cùng giờ của N ngày gần nhất
    LINEAR = 'linear'                   # ngoại suy tuyến tính (dự phòng)
