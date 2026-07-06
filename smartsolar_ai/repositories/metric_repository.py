# -*- coding: utf-8 -*-
"""MetricRepository — MỘT cỗ máy truy vấn dùng chung cho MỌI metric.

Đây chính là thứ khiến ``get_power`` / ``get_temperature`` / ``get_battery`` gộp
lại thành một năng lực tái sử dụng duy nhất: đưa vào một MetricSpec, nó tự biết
đọc bảng/cột nào và chọn nguồn nào (raw hay summary) là RẺ NHẤT cho khoảng thời
gian yêu cầu.

KHÔNG có code riêng cho từng metric ở đây. Chỉ cần thêm metric vào registry là nó
lập tức truy vấn được qua các hàm này.

Lưu ý về hiệu năng:
    Tầng này dùng raw SQL với ``date_trunc`` để gom bucket. Đây là "ngoại lệ hiệu
    năng" mà kiến trúc CHO PHÉP ở tầng Repository — ORM thuần sẽ chậm khi gom hàng
    trăm nghìn bản ghi phút. Service phía trên vẫn hoàn toàn độc lập với SQL.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from odoo import fields

from .base_repository import BaseRepository
from ..domain.enums import AggregationType, Granularity, MetricKind
from ..domain.metric_registry import MetricSpec
from ..domain.value_objects import TimeRange, DataPoint

# Ngưỡng chọn granularity tự động (AUTO):
_RAW_MAX_DAYS = 2       # <= 2 ngày  -> dùng dữ liệu phút (bảng raw)
_HOUR_MAX_DAYS = 92     # <= ~3 tháng -> dùng summary theo giờ; dài hơn -> theo ngày

# Ánh xạ kiểu gộp của domain -> hàm gộp của PostgreSQL.
_PG_AGG = {
    AggregationType.AVG: 'AVG',
    AggregationType.MAX: 'MAX',
    AggregationType.MIN: 'MIN',
    AggregationType.SUM: 'SUM',
}


class MetricRepository(BaseRepository):

    # ---- Chọn độ phân giải -------------------------------------------------
    def resolve_granularity(self, spec: MetricSpec, time_range: TimeRange,
                            requested: Granularity) -> Granularity:
        """Quyết định độ phân giải thực tế.

        Nếu người gọi chỉ định cụ thể (không phải AUTO) thì tôn trọng. Nếu AUTO:
        khoảng càng dài thì bucket càng thô để trả ít điểm hơn -> nhanh, nhẹ, và
        biểu đồ vẫn đọc được. Metric không có bảng summary thì buộc dùng raw.
        """
        if requested != Granularity.AUTO:
            return requested
        if spec.summary_model is None:
            return Granularity.RAW
        days = time_range.days
        if days <= _RAW_MAX_DAYS:
            return Granularity.RAW
        # Bảng summary chỉ gộp tới ngày (vd môi trường): không có bucket 'hour' để
        # đọc -> nhảy thẳng lên DAY thay vì HOUR, nếu không sẽ truy vấn bucket rỗng.
        if spec.summary_bucket == 'day':
            return Granularity.DAY
        if days <= _HOUR_MAX_DAYS:
            return Granularity.HOUR
        return Granularity.DAY

    # ---- Chuỗi thời gian ---------------------------------------------------
    def fetch_series(self, spec: MetricSpec, time_range: TimeRange,
                     aggregation: AggregationType, granularity: Granularity,
                     device_id=None, system_id=None):
        """Trả về list[DataPoint] đã gom bucket theo granularity đã chọn.

        Là điểm vào chung: tự phân nhánh sang truy vấn bảng raw hay bảng summary.
        """
        gran = self.resolve_granularity(spec, time_range, granularity)

        if gran == Granularity.RAW:
            return self._fetch_series_raw(
                spec, time_range, aggregation, device_id, system_id)
        return self._fetch_series_summary(
            spec, time_range, aggregation, gran, device_id, system_id)

    def _bucket_expr(self, gran: Granularity, date_col: str) -> str:
        """Sinh biểu thức date_trunc tương ứng độ phân giải (phút/giờ/ngày)."""
        unit = {Granularity.HOUR: 'hour', Granularity.DAY: 'day'}.get(
            gran, 'minute')
        return "date_trunc('%s', %s)" % (unit, date_col)

    def _fetch_series_raw(self, spec, time_range, aggregation, device_id, system_id):
        """Truy vấn chuỗi thời gian từ BẢNG RAW, gom theo phút.

        Gom theo phút để giữ nguyên nhịp 1 bản ghi/phút của thiết bị. Tham số được
        truyền qua placeholder %s (KHÔNG nối chuỗi) -> chống SQL injection.
        """
        model = self.env[spec.raw_model]
        table = model._table
        field = spec.raw_field
        agg = _PG_AGG.get(aggregation, 'AVG')

        params = [time_range.start_utc, time_range.end_utc]
        where = ["record_date >= %s", "record_date < %s"]
        # Bảng cấp hệ thống (vd môi trường) không có cột device_id -> bỏ qua lọc device.
        if device_id and spec.has_device:
            where.append("device_id = %s")
            params.append(device_id)
        if system_id:
            where.append("system_id = %s")
            params.append(system_id)

        sql = """
            SELECT date_trunc('minute', record_date) AS bucket,
                   {agg}({field}) AS val
              FROM {table}
             WHERE {where}
          GROUP BY bucket
          ORDER BY bucket
        """.format(agg=agg, field=field, table=table, where=' AND '.join(where))
        self.env.cr.execute(sql, params)
        return [DataPoint(row[0], float(row[1] or 0.0)) for row in self.env.cr.fetchall()]

    def _fetch_series_summary(self, spec, time_range, aggregation, gran,
                              device_id, system_id):
        """Truy vấn chuỗi thời gian từ BẢNG SUMMARY (đã tổng hợp sẵn theo giờ).

        Bảng summary lưu sẵn theo giờ; nếu cần theo ngày thì gom (roll-up) tiếp.
        Chọn đúng cột summary theo kiểu gộp: MAX -> cột *_max, còn lại -> cột *_avg.
        """
        model = self.env[spec.summary_model]
        table = model._table
        if aggregation == AggregationType.MAX and spec.summary_max_field:
            col = spec.summary_max_field
            outer = 'MAX'
        elif aggregation == AggregationType.SUM:
            col = spec.summary_field
            outer = 'SUM'
        else:
            col = spec.summary_field
            outer = 'AVG'

        params = [time_range.start_utc, time_range.end_utc]
        where = ["bucket_start >= %s", "bucket_start < %s"]
        # Đọc từ bucket mịn nhất mà bảng summary này có (thiết bị: 'hour'; môi
        # trường: 'day'), rồi gom lên 'day' nếu cần. Không đòi 'hour' ở bảng chỉ-ngày.
        source_bucket = spec.summary_bucket
        where.append("bucket_type = %s")
        params.append(source_bucket)
        if device_id and spec.has_device:
            where.append("device_id = %s")
            params.append(device_id)
        if system_id:
            where.append("system_id = %s")
            params.append(system_id)

        bucket_expr = self._bucket_expr(gran, 'bucket_start')
        sql = """
            SELECT {bucket} AS bucket, {outer}({col}) AS val
              FROM {table}
             WHERE {where}
          GROUP BY bucket
          ORDER BY bucket
        """.format(bucket=bucket_expr, outer=outer, col=col, table=table,
                   where=' AND '.join(where))
        self.env.cr.execute(sql, params)
        return [DataPoint(row[0], float(row[1] or 0.0)) for row in self.env.cr.fetchall()]

    # ---- Thống kê vô hướng -------------------------------------------------
    def fetch_scalar(self, spec: MetricSpec, time_range: TimeRange,
                     device_id=None, system_id=None) -> dict:
        """Trả về {avg, min, max, sum, last, first, count} cho một metric trên khoảng.

        Riêng metric COUNTER: "năng lượng trong khoảng" = last - first, do hàm
        ``fetch_energy`` xử lý. Ở đây chỉ trả thống kê thô từ bảng raw.

        SQL dùng 2 subquery lấy first/last theo thời gian; mệnh đề WHERE lặp 3 lần
        nên tham số cũng nhân 3 (``params * 3``).
        """
        model = self.env[spec.raw_model]
        table = model._table
        f = spec.raw_field
        params = [time_range.start_utc, time_range.end_utc]
        where = ["record_date >= %s", "record_date < %s"]
        # Bảng cấp hệ thống (vd môi trường) không có cột device_id -> bỏ qua lọc device.
        if device_id and spec.has_device:
            where.append("device_id = %s")
            params.append(device_id)
        if system_id:
            where.append("system_id = %s")
            params.append(system_id)
        whr = ' AND '.join(where)

        sql = """
            SELECT AVG({f}) AS avg_v, MIN({f}) AS min_v, MAX({f}) AS max_v,
                   SUM({f}) AS sum_v,
                   (SELECT {f} FROM {table} WHERE {whr}
                     ORDER BY record_date DESC LIMIT 1) AS last_v,
                   (SELECT {f} FROM {table} WHERE {whr}
                     ORDER BY record_date ASC LIMIT 1) AS first_v,
                   COUNT(*) AS n
              FROM {table} WHERE {whr}
        """.format(f=f, table=table, whr=whr)
        self.env.cr.execute(sql, params * 3)
        row = self.env.cr.fetchone()
        if not row or row[6] == 0:
            # Không có dữ liệu -> trả về 0 hết thay vì None, để tầng trên khỏi phải kiểm None.
            return {'avg': 0.0, 'min': 0.0, 'max': 0.0, 'sum': 0.0,
                    'last': 0.0, 'first': 0.0, 'count': 0}
        return {
            'avg': float(row[0] or 0.0), 'min': float(row[1] or 0.0),
            'max': float(row[2] or 0.0), 'sum': float(row[3] or 0.0),
            'last': float(row[4] or 0.0), 'first': float(row[5] or 0.0),
            'count': int(row[6]),
        }

    def fetch_energy(self, spec: MetricSpec, time_range: TimeRange,
                     device_id=None, system_id=None) -> float:
        """Năng lượng (kWh) tích lũy TRONG khoảng, từ một metric kiểu COUNTER.

        Ưu tiên cột energy_kwh của bảng summary (đã là tích phân từng bucket) khi
        có sẵn — chính xác và nhanh. Nếu không có thì tính dự phòng bằng
        last - first trên bộ đếm thô (kẹp về >= 0 phòng khi bộ đếm bị reset).
        """
        if spec.summary_model:
            energy_col = 'energy_kwh'
            table = self.env[spec.summary_model]._table
            params = [time_range.start_utc, time_range.end_utc, spec.summary_bucket]
            where = ["bucket_start >= %s", "bucket_start < %s", "bucket_type = %s"]
            if device_id and spec.has_device:
                where.append("device_id = %s")
                params.append(device_id)
            if system_id:
                where.append("system_id = %s")
                params.append(system_id)
            sql = "SELECT SUM({col}) FROM {table} WHERE {whr}".format(
                col=energy_col, table=table, whr=' AND '.join(where))
            self.env.cr.execute(sql, params)
            val = self.env.cr.fetchone()[0]
            if val is not None:
                return float(val)
        stats = self.fetch_scalar(spec, time_range, device_id, system_id)
        return max(0.0, stats['last'] - stats['first'])
