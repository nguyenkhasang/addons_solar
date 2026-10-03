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
from ..domain.metric_registry import MetricSpec, MetricRegistry
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

    @staticmethod
    def _aggregation_expr(field: str, aggregation: AggregationType,
                          order_field: str) -> str:
        """Biểu thức SQL gộp đúng cả FIRST/LAST thay vì âm thầm rơi về AVG."""
        if aggregation == AggregationType.FIRST:
            return '(ARRAY_AGG(%s ORDER BY %s ASC))[1]' % (field, order_field)
        if aggregation == AggregationType.LAST:
            return '(ARRAY_AGG(%s ORDER BY %s DESC))[1]' % (field, order_field)
        sql_agg = _PG_AGG.get(aggregation)
        if sql_agg is None:
            raise ValueError("Kiểu aggregation không được hỗ trợ: %s" % aggregation)
        return '%s(%s)' % (sql_agg, field)

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

    # ---- Chọn cách gộp -----------------------------------------------------
    def resolve_aggregation(self, spec: MetricSpec,
                            requested: AggregationType = None) -> AggregationType:
        """Quyết định cách gộp THỰC TẾ sẽ dùng.

        Không chỉ định -> lấy mặc định của metric. SUM trên metric INSTANTANEOUS
        bị đổi về AVG: cộng các giá trị tức thời (W/V/A) ra con số không có đơn vị
        vật lý nào, LLM rất dễ tưởng đó là năng lượng tích lũy (kWh).

        Service gọi hàm này để ghi ĐÚNG nhãn 'aggregation' vào kết quả — cùng cơ
        chế với ``resolve_granularity``. Nếu chỉ đổi âm thầm bên trong truy vấn,
        kết quả sẽ mang nhãn 'sum' trong khi số liệu là AVG -> LLM diễn giải sai.
        """
        agg = requested or spec.default_aggregation
        if agg == AggregationType.SUM and spec.kind == MetricKind.INSTANTANEOUS:
            return AggregationType.AVG
        return agg

    # ---- Chuỗi thời gian ---------------------------------------------------
    def fetch_series(self, spec: MetricSpec, time_range: TimeRange,
                     aggregation: AggregationType, granularity: Granularity,
                     device_id=None, system_id=None):
        """Trả về list[DataPoint] đã gom bucket theo granularity đã chọn.

        Là điểm vào chung: tự phân nhánh sang truy vấn bảng raw hay bảng summary.
        Cách gộp đi qua ``resolve_aggregation`` (chặn SUM trên metric tức thời).
        """
        aggregation = self.resolve_aggregation(spec, aggregation)
        gran = self.resolve_granularity(spec, time_range, granularity)

        if gran == Granularity.RAW:
            return self._fetch_series_raw(
                spec, time_range, aggregation, device_id, system_id)
        if gran == Granularity.HOUR and spec.summary_bucket == 'hour':
            return self._fetch_series_hour_hybrid(
                spec, time_range, aggregation, device_id, system_id)
        return self._fetch_series_summary(
            spec, time_range, aggregation, gran, device_id, system_id)

    def _fetch_series_hour_hybrid(self, spec, time_range, aggregation,
                                  device_id, system_id):
        """Use summary for complete hours and raw for the two partial edges."""
        full_start = time_range.start_utc.replace(minute=0, second=0, microsecond=0)
        if full_start < time_range.start_utc:
            full_start += timedelta(hours=1)
        full_end = time_range.end_utc.replace(minute=0, second=0, microsecond=0)
        if full_start >= full_end:
            return self._fetch_series_raw(
                spec, time_range, aggregation, device_id, system_id,
                Granularity.HOUR)
        points = []
        if time_range.start_utc < min(full_start, time_range.end_utc):
            edge = TimeRange(time_range.start_utc, min(full_start, time_range.end_utc))
            points.extend(self._fetch_series_raw(
                spec, edge, aggregation, device_id, system_id, Granularity.HOUR))
        if full_start < full_end:
            middle = TimeRange(full_start, full_end)
            points.extend(self._fetch_series_summary(
                spec, middle, aggregation, Granularity.HOUR, device_id, system_id))
        right_start = max(full_end, time_range.start_utc)
        # If there is no complete hour, the left edge already covered everything.
        if full_start < full_end and right_start < time_range.end_utc:
            edge = TimeRange(right_start, time_range.end_utc)
            points.extend(self._fetch_series_raw(
                spec, edge, aggregation, device_id, system_id, Granularity.HOUR))
        return sorted(points, key=lambda point: point.ts_utc)

    def _bucket_expr(self, gran: Granularity, date_col: str) -> str:
        """Sinh biểu thức date_trunc tương ứng độ phân giải (phút/giờ/ngày)."""
        unit = {Granularity.HOUR: 'hour', Granularity.DAY: 'day'}.get(
            gran, 'minute')
        return "date_trunc('%s', %s)" % (unit, date_col)

    def _fetch_series_raw(self, spec, time_range, aggregation, device_id, system_id,
                          bucket_granularity=Granularity.RAW):
        """Truy vấn chuỗi thời gian từ BẢNG RAW, gom theo phút.

        Gom theo phút để giữ nguyên nhịp 1 bản ghi/phút của thiết bị. Tham số được
        truyền qua placeholder %s (KHÔNG nối chuỗi) -> chống SQL injection.
        """
        model = self.env[spec.raw_model]
        table = model._table
        field = spec.raw_field
        agg_expr = self._aggregation_expr(field, aggregation, 'record_date')
        bucket_expr = self._bucket_expr(bucket_granularity, 'record_date')

        params = [time_range.start_utc, time_range.end_utc]
        where = ["record_date >= %s", "record_date < %s"]
        # Bảng cấp hệ thống (vd môi trường) không có cột device_id -> bỏ qua lọc device.
        if device_id and spec.has_device:
            where.append("device_id = %s")
            params.append(device_id)
        if system_id:
            where.append("system_id = %s")
            params.append(system_id)

        # Counter cấp hệ thống phải cộng giá trị cuối/đầu của TỪNG thiết bị. Nếu
        # ARRAY_AGG trực tiếp trên mọi thiết bị, ta chỉ lấy ngẫu nhiên một thiết bị.
        if (spec.kind == MetricKind.COUNTER and spec.has_device
                and aggregation in (AggregationType.FIRST, AggregationType.LAST)):
            sql = """
                SELECT bucket, SUM(val) AS val
                  FROM (
                        SELECT {bucket_expr} AS bucket,
                               device_id, {agg_expr} AS val
                          FROM {table}
                         WHERE {where}
                      GROUP BY bucket, device_id
                       ) per_device
              GROUP BY bucket
              ORDER BY bucket
            """.format(bucket_expr=bucket_expr, agg_expr=agg_expr, table=table,
                       where=' AND '.join(where))
            self.env.cr.execute(sql, params)
            return [DataPoint(row[0], float(row[1] or 0.0))
                    for row in self.env.cr.fetchall()]

        sql = """
            SELECT {bucket_expr} AS bucket,
                   {agg_expr} AS val
              FROM {table}
             WHERE {where}
          GROUP BY bucket
          ORDER BY bucket
        """.format(bucket_expr=bucket_expr, agg_expr=agg_expr, table=table,
                   where=' AND '.join(where))
        self.env.cr.execute(sql, params)
        return [DataPoint(row[0], float(row[1] or 0.0)) for row in self.env.cr.fetchall()]

    def _fetch_series_summary(self, spec, time_range, aggregation, gran,
                              device_id, system_id):
        """Truy vấn chuỗi thời gian từ BẢNG SUMMARY (đã tổng hợp sẵn theo giờ).

        Bảng summary lưu sẵn theo giờ; nếu cần theo ngày thì gom (roll-up) tiếp.
        MAX dùng cột *_max khi có; FIRST/LAST giữ đúng thứ tự bucket, các kiểu còn
        lại gộp trên summary_field đã khai báo trong MetricSpec.
        """
        model = self.env[spec.summary_model]
        table = model._table
        if aggregation == AggregationType.MAX and spec.summary_max_field:
            col = spec.summary_max_field
        else:
            col = spec.summary_field
        if aggregation == AggregationType.AVG:
            # Summary rows represent different numbers of raw samples. Averaging
            # their averages would give a sparse bucket the same weight as a full one.
            agg_expr = (
                'SUM({0} * sample_count) / NULLIF(SUM(sample_count), 0)'.format(col)
            )
        else:
            agg_expr = self._aggregation_expr(col, aggregation, 'bucket_start')

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
        if (spec.kind == MetricKind.COUNTER and spec.has_device
                and aggregation in (AggregationType.FIRST, AggregationType.LAST)):
            sql = """
                SELECT bucket, SUM(val) AS val
                  FROM (
                        SELECT {bucket} AS bucket, device_id,
                               {agg_expr} AS val
                          FROM {table}
                         WHERE {where}
                      GROUP BY bucket, device_id
                       ) per_device
              GROUP BY bucket
              ORDER BY bucket
            """.format(bucket=bucket_expr, agg_expr=agg_expr, table=table,
                       where=' AND '.join(where))
            self.env.cr.execute(sql, params)
            return [DataPoint(row[0], float(row[1] or 0.0))
                    for row in self.env.cr.fetchall()]

        sql = """
            SELECT {bucket} AS bucket, {agg_expr} AS val
              FROM {table}
             WHERE {where}
          GROUP BY bucket
          ORDER BY bucket
        """.format(bucket=bucket_expr, agg_expr=agg_expr, table=table,
                   where=' AND '.join(where))
        self.env.cr.execute(sql, params)
        return [DataPoint(row[0], float(row[1] or 0.0)) for row in self.env.cr.fetchall()]

    # ---- Thống kê vô hướng -------------------------------------------------
    def fetch_observation_bounds(self, spec, time_range, device_id=None, system_id=None):
        """Observation timestamps, not the time at which the AI queried data."""
        sources = [(spec.raw_model, 'record_date', None, spec.raw_field)]
        if spec.summary_model and spec.summary_field:
            sources.append((spec.summary_model, 'bucket_start', spec.summary_bucket,
                            spec.summary_field))
        for model, date_field, bucket, value_field in sources:
            params = [time_range.start_utc, time_range.end_utc]
            where = ['%s >= %%s' % date_field, '%s < %%s' % date_field,
                     '%s IS NOT NULL' % value_field]
            if bucket:
                where.append('bucket_type = %s')
                params.append(bucket)
            if device_id and spec.has_device:
                where.append('device_id = %s')
                params.append(device_id)
            if system_id:
                where.append('system_id = %s')
                params.append(system_id)
            self.env.cr.execute(
                'SELECT MIN({date}), MAX({date}), {devices} FROM {table} WHERE {where}'.format(
                    date=date_field, devices='COUNT(DISTINCT device_id)' if spec.has_device else '0',
                    table=self.env[model]._table, where=' AND '.join(where)),
                params)
            first, last, device_count = self.env.cr.fetchone()
            if last:
                local = lambda value: value.replace(tzinfo=timezone.utc).astimezone(
                    timezone(timedelta(hours=7))).isoformat()
                return {
                    'first_observed_at': local(first), 'last_observed_at': local(last),
                    'last_sample_age_seconds': max(0, round((fields.Datetime.now() - last).total_seconds())),
                    'start_gap_seconds': max(0, round((first - time_range.start_utc).total_seconds())),
                    'end_gap_seconds': max(0, round((time_range.end_utc - last).total_seconds())),
                    'source': 'summary' if bucket else 'raw',
                    'resolution': bucket or ('day' if spec.summary_bucket == 'day' else 'raw'),
                    'timestamp_meaning': 'bucket_start' if bucket else 'record_date',
                    'device_count': device_count,
                }
        return {'first_observed_at': None, 'last_observed_at': None, 'source': None}

    def fetch_scalar(self, spec: MetricSpec, time_range: TimeRange,
                     device_id=None, system_id=None) -> dict:
        """Trả về {avg, min, max, sum, last, first, count} cho metric trên khoảng.

        Riêng metric COUNTER: "năng lượng trong khoảng" = last - first,
        do hàm ``fetch_energy`` xử lý. Ở đây chỉ trả thống kê thô từ
        bảng raw.

        Nếu raw trả count=0 và metric có bảng summary -> tự fallback sang
        summary (bảng đã tổng hợp sẵn cho khoảng dài, tránh trả 0 giả).
        """
        raw = self._fetch_scalar_raw(spec, time_range, device_id, system_id)
        if raw['count'] > 0 or spec.kind == MetricKind.COUNTER:
            return raw
        # Fallback sang summary nếu có và raw rỗng (vd khoảng dài > 7 ngày
        # với weather metric chỉ giữ raw vài ngày). Counter dùng
        # ``fetch_energy_result`` để đọc summary; không dựng first/last từ nhiều
        # thiết bị bằng một subquery vô hướng vì có thể chọn sai thiết bị.
        return self._fetch_scalar_summary_or_default(
            spec, time_range, device_id, system_id, raw)

    def _fetch_scalar_raw(self, spec, time_range, device_id, system_id) -> dict:
        """Thống kê từ bảng raw (chi tiết từng bản ghi)."""
        model = self.env[spec.raw_model]
        table = model._table
        f = spec.raw_field
        params = [time_range.start_utc, time_range.end_utc]
        where = ["record_date >= %s", "record_date < %s"]
        if device_id and spec.has_device:
            where.append("device_id = %s")
            params.append(device_id)
        if system_id:
            where.append("system_id = %s")
            params.append(system_id)
        whr = ' AND '.join(where)

        # Counter của toàn hệ thống phải lấy first/last RIÊNG từng thiết bị rồi
        # cộng lại. Lấy một dòng first/last trên toàn bảng sẽ trộn hai thiết bị và
        # tạo ra delta kWh sai. Các thống kê còn lại vẫn được gộp trên mọi mẫu.
        if spec.kind == MetricKind.COUNTER and spec.has_device:
            sql = """
                SELECT SUM(sum_v) / NULLIF(SUM(n), 0) AS avg_v,
                       MIN(min_v) AS min_v, MAX(max_v) AS max_v,
                       SUM(sum_v) AS sum_v, SUM(last_v) AS last_v,
                       SUM(first_v) AS first_v, SUM(n) AS n
                  FROM (
                        SELECT device_id, MIN({f}) AS min_v, MAX({f}) AS max_v,
                               SUM({f}) AS sum_v,
                               (ARRAY_AGG({f} ORDER BY record_date DESC))[1] AS last_v,
                               (ARRAY_AGG({f} ORDER BY record_date ASC))[1] AS first_v,
                               COUNT(*) AS n
                          FROM {table}
                         WHERE {whr}
                      GROUP BY device_id
                       ) per_device
            """.format(f=f, table=table, whr=whr)
            self.env.cr.execute(sql, params)
            row = self.env.cr.fetchone()
            if not row or not row[6]:
                return {'avg': 0.0, 'min': 0.0, 'max': 0.0, 'sum': 0.0,
                        'last': 0.0, 'first': 0.0, 'count': 0}
            return {
                'avg': float(row[0] or 0.0), 'min': float(row[1] or 0.0),
                'max': float(row[2] or 0.0), 'sum': float(row[3] or 0.0),
                'last': float(row[4] or 0.0), 'first': float(row[5] or 0.0),
                'count': int(row[6]),
            }

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
            return {'avg': 0.0, 'min': 0.0, 'max': 0.0, 'sum': 0.0,
                    'last': 0.0, 'first': 0.0, 'count': 0}
        return {
            'avg': float(row[0] or 0.0), 'min': float(row[1] or 0.0),
            'max': float(row[2] or 0.0), 'sum': float(row[3] or 0.0),
            'last': float(row[4] or 0.0), 'first': float(row[5] or 0.0),
            'count': int(row[6]),
        }

    def _fetch_scalar_summary_or_default(self, spec, time_range, device_id,
                                         system_id, fallback: dict) -> dict:
        """Fallback sang summary nếu có sẵn, hoặc trả về `fallback` (count=0).

        Đảm bảo `fetch_scalar` không bao giờ trả count=0 âm thầm khi summary
        có dữ liệu (vd weather metric dùng summary theo ngày cho khoảng dài).
        """
        if spec.summary_model is None or spec.summary_field is None:
            return fallback
        model = self.env[spec.summary_model]
        table = model._table
        col = spec.summary_field
        # MAX dùng cột *_max riêng nếu có (bảng summary tách avg/max).
        max_col = spec.summary_max_field or col
        params = [time_range.start_utc, time_range.end_utc, spec.summary_bucket]
        where = ["bucket_start >= %s", "bucket_start < %s", "bucket_type = %s"]
        # Giữ ĐÚNG bộ lọc như nhánh raw: thiếu device_id sẽ gộp số của mọi
        # thiết bị vào câu hỏi về một thiết bị -> sai dữ liệu.
        if device_id and spec.has_device:
            where.append("device_id = %s")
            params.append(device_id)
        if system_id:
            where.append("system_id = %s")
            params.append(system_id)
        whr = ' AND '.join(where)
        # first/last phải theo THỜI GIAN (bucket_start), không phải min/max giá trị.
        sql = """
            SELECT SUM({col} * sample_count) / NULLIF(SUM(sample_count), 0) AS avg_v,
                   MIN({col}) AS min_v,
                   MAX({max_col}) AS max_v, SUM({col}) AS sum_v,
                   (SELECT {col} FROM {table} WHERE {whr}
                     ORDER BY bucket_start DESC LIMIT 1) AS last_v,
                   (SELECT {col} FROM {table} WHERE {whr}
                     ORDER BY bucket_start ASC LIMIT 1) AS first_v,
                   COUNT(*) AS n
              FROM {table} WHERE {whr}
        """.format(col=col, max_col=max_col, table=table, whr=whr)
        self.env.cr.execute(sql, params * 3)
        row = self.env.cr.fetchone()
        if not row or row[6] == 0:
            return fallback
        return {
            'avg': float(row[0] or 0.0), 'min': float(row[1] or 0.0),
            'max': float(row[2] or 0.0), 'sum': float(row[3] or 0.0),
            'last': float(row[4] or 0.0), 'first': float(row[5] or 0.0),
            'count': int(row[6]),
        }

    def fetch_energy_result(self, spec: MetricSpec, time_range: TimeRange,
                            device_id=None, system_id=None) -> dict:
        """Trả năng lượng và trạng thái, phân biệt số 0 thật với thiếu dữ liệu.

        Kết quả chuẩn là ``{available, value, count, source}``. Chỉ các bucket
        summary nằm trọn trong khoảng được dùng; hai mép thời gian đọc raw và
        cộng delta có xử lý counter reset.
        """
        if spec.summary_model and spec.summary_bucket == 'hour':
            energy_col = 'energy_kwh'
            table = self.env[spec.summary_model]._table
            full_start = time_range.start_utc.replace(minute=0, second=0, microsecond=0)
            if full_start < time_range.start_utc:
                full_start += timedelta(hours=1)
            full_end = time_range.end_utc.replace(minute=0, second=0, microsecond=0)
            params = [full_start, full_end, spec.summary_bucket]
            where = ["bucket_start >= %s", "bucket_start < %s", "bucket_type = %s"]
            if device_id and spec.has_device:
                where.append("device_id = %s")
                params.append(device_id)
            if system_id:
                where.append("system_id = %s")
                params.append(system_id)
            sql = "SELECT SUM({col}), COUNT({col}) FROM {table} WHERE {whr}".format(
                col=energy_col, table=table, whr=' AND '.join(where))
            summary_value = 0.0
            summary_count = 0
            if full_start < full_end:
                self.env.cr.execute(sql, params)
                row = self.env.cr.fetchone() or (None, 0)
                summary_value = float(row[0] or 0.0)
                summary_count = int(row[1] or 0)

            if full_start >= full_end:
                value, count = self._fetch_raw_counter_delta(
                    spec, time_range.start_utc, time_range.end_utc,
                    device_id, system_id)
                if count:
                    return {
                        'available': True,
                        'value': value,
                        'count': count,
                        'source': 'raw',
                    }
                return {'available': False, 'value': None, 'count': 0, 'source': None}

            raw_value = 0.0
            raw_count = 0
            left_end = min(full_start, time_range.end_utc)
            if time_range.start_utc < left_end:
                value, count = self._fetch_raw_counter_delta(
                    spec, time_range.start_utc, left_end, device_id, system_id)
                raw_value += value
                raw_count += count
            right_start = max(full_end, time_range.start_utc)
            if right_start < time_range.end_utc:
                value, count = self._fetch_raw_counter_delta(
                    spec, right_start, time_range.end_utc, device_id, system_id)
                raw_value += value
                raw_count += count

            if summary_count or raw_count:
                return {
                    'available': True,
                    'value': summary_value + raw_value,
                    'count': summary_count + raw_count,
                    'source': 'summary+raw' if summary_count and raw_count
                              else ('summary' if summary_count else 'raw'),
                }

        if spec.kind == MetricKind.COUNTER and spec.has_device:
            value, count = self._fetch_raw_counter_delta(
                spec, time_range.start_utc, time_range.end_utc,
                device_id, system_id)
            if count:
                return {
                    'available': True,
                    'value': value,
                    'count': count,
                    'source': 'raw',
                }

        stats = self.fetch_scalar(spec, time_range, device_id, system_id)
        if not stats['count']:
            return {'available': False, 'value': None, 'count': 0, 'source': None}
        return {
            'available': True,
            'value': max(0.0, stats['last'] - stats['first']),
            'count': stats['count'],
            'source': 'raw',
        }

    def _fetch_raw_counter_delta(self, spec, start_utc, end_utc,
                                 device_id=None, system_id=None):
        """Sum ordered positive counter steps, treating a decrease as a reset.

        The last sample before ``start_utc`` is included only as the LAG seed, so
        bucket boundaries remain connected without counting that seed as a sample.
        """
        table = self.env[spec.raw_model]._table
        field = spec.raw_field
        filters = []
        extra = []
        if device_id and spec.has_device:
            filters.append('device_id = %s')
            extra.append(device_id)
        if system_id:
            filters.append('system_id = %s')
            extra.append(system_id)
        suffix = (' AND ' + ' AND '.join(filters)) if filters else ''
        sql = """
            WITH active_devices AS (
                SELECT DISTINCT device_id
                  FROM {table}
                 WHERE record_date >= %s AND record_date < %s {suffix}
            ), source_rows AS (
                SELECT id, device_id, record_date, {field} AS value
                  FROM {table}
                 WHERE record_date >= %s AND record_date < %s {suffix}
                UNION ALL
                SELECT p.id, p.device_id, p.record_date, p.{field} AS value
                  FROM active_devices d
                  JOIN LATERAL (
                        SELECT id, device_id, record_date, {field}
                          FROM {table}
                         WHERE device_id = d.device_id AND record_date < %s
                      ORDER BY record_date DESC, id DESC LIMIT 1
                  ) p ON TRUE
            ), ordered AS (
                SELECT *, LAG(value) OVER (
                    PARTITION BY device_id ORDER BY record_date, id
                ) AS previous_value
                  FROM source_rows
            )
            SELECT COALESCE(SUM(CASE
                       WHEN value IS NULL OR previous_value IS NULL THEN 0
                       WHEN value >= previous_value THEN value - previous_value
                       ELSE GREATEST(value, 0)
                   END), 0),
                   COUNT(*)
              FROM ordered
             WHERE record_date >= %s AND record_date < %s
        """.format(table=table, field=field, suffix=suffix)
        params = (
            [start_utc, end_utc] + extra
            + [start_utc, end_utc] + extra
            + [start_utc, start_utc, end_utc]
        )
        self.env.cr.execute(sql, params)
        row = self.env.cr.fetchone() or (0.0, 0)
        return float(row[0] or 0.0), int(row[1] or 0)

    def fetch_extrema(self, spec, time_range, device_id=None, system_id=None):
        """Exact min/max observations from all retained raw samples, not a series.

        Total load uses both branches on the same observation. Multiple devices
        are summed only at timestamps with an observation from every device seen
        in the selected scope; asynchronous maxima are never added together.
        """
        from ..domain.value_objects import UTC7
        table = self.env[spec.raw_model]._table
        where = ['record_date >= %s', 'record_date < %s']
        params = [time_range.start_utc, time_range.end_utc]
        if device_id and spec.has_device:
            where.append('device_id = %s')
            params.append(device_id)
        if system_id:
            where.append('system_id = %s')
            params.append(system_id)
        total_load = spec.key == 'total_load_power'
        device_column = 'device_id' if spec.has_device else 'NULL::integer'
        extra = ', output_power, limiter_power' if total_load else ''
        if total_load:
            observations = """
                SELECT record_date, SUM(value) AS value,
                       ARRAY_AGG(device_id ORDER BY device_id) AS device_ids,
                       SUM(output_power) AS output_power,
                       SUM(limiter_power) AS grid_import_power
                  FROM points
              GROUP BY record_date
                HAVING COUNT(*) = (SELECT COUNT(DISTINCT device_id) FROM points)
                   AND COUNT(value) = COUNT(*)
            """
        else:
            observations = """
                SELECT record_date, value, ARRAY[device_id] AS device_ids
                  FROM points WHERE value IS NOT NULL
            """
        # DISTINCT ON picks the last received row for a duplicate timestamp.
        sql = """
            WITH points AS (
                SELECT DISTINCT ON ({device}, record_date)
                       {device} AS device_id, record_date, {field} AS value {extra}
                  FROM {table} WHERE {where}
              ORDER BY {device}, record_date, id DESC
            ), observations AS ({observations})
            SELECT (SELECT COUNT(*) FROM points),
                   (SELECT COUNT(*) FROM observations),
                   (SELECT COUNT(DISTINCT device_id) FROM points),
                   (SELECT ROW_TO_JSON(peak) FROM (
                        SELECT * FROM observations ORDER BY value DESC, record_date ASC LIMIT 1
                    ) peak),
                   (SELECT ROW_TO_JSON(low) FROM (
                        SELECT * FROM observations ORDER BY value ASC, record_date ASC LIMIT 1
                    ) low)
        """.format(device=device_column, field=spec.raw_field, extra=extra,
                   table=table, where=' AND '.join(where), observations=observations)
        self.env.cr.execute(sql, params)
        count, observations_count, devices, peak, low = self.env.cr.fetchone()
        def observation(row):
            if not row:
                return None
            observed = datetime.fromisoformat(row.pop('record_date'))
            result = {'value': row['value'],
                      'observed_at': observed.replace(tzinfo=timezone.utc).astimezone(UTC7).isoformat(),
                      'device_ids': [value for value in row['device_ids'] if value is not None]}
            if total_load:
                result['components'] = {key: row[key] for key in ('output_power', 'grid_import_power')}
            return result
        return {
            'available': bool(observations_count), 'source': 'raw',
            'sample_count': count, 'evaluated_observations': observations_count,
            'observed_device_count': devices, 'max': observation(peak), 'min': observation(low),
            'scope': 'synchronized_device_sum' if total_load else 'individual_observations',
            'reason': None if observations_count else (
                'Không có mẫu raw hợp lệ hoặc không có mẫu đồng thời đủ các thiết bị trong phạm vi.'),
            'note': 'Cực trị chính xác trên các mẫu raw còn lưu, không phải đỉnh liên tục giữa mẫu '
                    'hay xác nhận dữ liệu phủ đủ khoảng/đủ mọi thiết bị cấu hình. '
                    'Nếu nhiều mẫu bằng nhau, trả thời điểm sớm nhất.',
        }

    def fetch_power_energy(self, metric, time_range, device_id=None, system_id=None):
        """Integrate raw power per device, without bridging telemetry outages.

        Linear interpolation is used only between adjacent valid samples at most
        five minutes apart. Boundary segments are clipped to the requested range.
        Missing time is excluded, never filled with zero or extrapolated.
        """
        expressions = {
            'output_power': 'output_power',
            'grid_import_power': 'limiter_power',
            'pv_input': 'charge_power',
            'total_load_energy': '(output_power + limiter_power)',
        }
        if metric not in expressions or time_range.days > 31:
            return {'value': None, 'available': False, 'reason':
                    'Ước tính từ công suất chỉ hỗ trợ dữ liệu raw trong khoảng tối đa 31 ngày.'}
        spec = MetricRegistry.get('output_power' if metric == 'total_load_energy' else metric)
        table = self.env[spec.raw_model]._table
        filters = ['record_date >= %s', 'record_date <= %s']
        params = [time_range.start_utc - timedelta(minutes=5),
                  time_range.end_utc + timedelta(minutes=5)]
        for column, value in [('device_id', device_id), ('system_id', system_id)]:
            if value:
                filters.append(column + ' = %s')
                params.append(value)
        self.env.cr.execute(
            'SELECT device_id, record_date, ' + expressions[metric] +
            ' FROM ' + table + ' WHERE ' + ' AND '.join(filters) +
            ' ORDER BY device_id, record_date, id', params)
        previous, devices = {}, {}
        for device, observed, power in self.env.cr.fetchall():
            old = previous.get(device)
            previous[device] = (observed, power)
            if time_range.start_utc <= observed < time_range.end_utc:
                devices.setdefault(device, {'kwh': 0.0, 'seconds': 0.0})
            if old is None or old[1] is None or power is None:
                continue
            span = (observed - old[0]).total_seconds()
            if not 0 < span <= 300:
                continue
            start = max(old[0], time_range.start_utc)
            end = min(observed, time_range.end_utc)
            seconds = (end - start).total_seconds()
            if seconds <= 0:
                continue
            p0 = float(old[1]) + (float(power) - float(old[1])) * (start - old[0]).total_seconds() / span
            p1 = float(old[1]) + (float(power) - float(old[1])) * (end - old[0]).total_seconds() / span
            item = devices.setdefault(device, {'kwh': 0.0, 'seconds': 0.0})
            item['kwh'] += (p0 + p1) / 2 * seconds / 3600000
            item['seconds'] += seconds
        duration = (time_range.end_utc - time_range.start_utc).total_seconds()
        covered = sum(item['seconds'] for item in devices.values())
        return {
            'value': round(sum(item['kwh'] for item in devices.values()), 4) if covered else None,
            'available': bool(covered), 'unit': 'kWh', 'estimated': True,
            'method': 'trapezoidal_raw_power', 'source': 'raw', 'max_gap_seconds': 300,
            'coverage_pct': round(covered / (duration * len(devices)) * 100, 2) if devices else 0,
            'per_device': [{'device_id': device, 'value': round(item['kwh'], 4),
                            'covered_seconds': item['seconds'],
                            'coverage_pct': round(item['seconds'] / duration * 100, 2)}
                           for device, item in devices.items()],
            'reason': None if covered else 'Không có cặp mẫu công suất liên tiếp đủ gần để tích phân.',
            'note': 'Chỉ tính phần thời gian có mẫu liên tiếp; không ngoại suy qua khoảng mất dữ liệu. '
                    'Độ phủ tính trên thiết bị có dữ liệu, không xác nhận đủ mọi thiết bị cấu hình.',
        }

    def fetch_energy_detail(self, spec: MetricSpec, time_range: TimeRange,
                            device_id=None, system_id=None) -> dict:
        """Adapter tương thích cho contract local cũ ``{value, has_data}``."""
        result = self.fetch_energy_result(
            spec, time_range, device_id=device_id, system_id=system_id)
        return {
            'value': result['value'] if result['available'] else 0.0,
            'has_data': result['available'],
        }

    def fetch_energy(self, spec: MetricSpec, time_range: TimeRange,
                     device_id=None, system_id=None) -> float:
        """Adapter tương thích chỉ trả số; thiếu dữ liệu giữ giá trị cũ là 0.0."""
        result = self.fetch_energy_result(
            spec, time_range, device_id=device_id, system_id=system_id)
        return float(result['value']) if result['available'] else 0.0
