"""Persist durable estimates, extrema and counter quality alongside summaries."""
from collections import defaultdict
from bisect import bisect_left
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from odoo import api, fields, models
from .summary_math import calculate, counter_is_valid, COUNTERS, VERSION


class SolarSummaryQuality(models.AbstractModel):
    _name = 'smartsolar.summary.quality'
    _description = 'Durable summary quality and extrema'

    power_energy = fields.Json(string='Điện năng ước tính và độ phủ')
    metric_extrema = fields.Json(string='Cực trị và thời điểm mẫu')
    metric_statistics = fields.Json(string='Thống kê mẫu gốc')
    counter_quality = fields.Json(string='Chất lượng công-tơ')
    quality_version = fields.Integer(string='Phiên bản metadata', default=0)

    @api.model
    def _quality_rows(self, table, start, end, device_id=None, system_id=None):
        lower = start - timedelta(seconds=300)
        scope, params = '', [lower, end + timedelta(seconds=300)]
        for field, value in [('device_id', device_id), ('system_id', system_id)]:
            if value:
                scope += ' AND ' + field + ' = %s'
                params.append(value)
        self.env.cr.execute('SELECT * FROM ' + table +
                            ' WHERE record_date >= %s AND record_date <= %s '
                            'AND device_id IS NOT NULL ' + scope + ' ORDER BY device_id, record_date, id', params)
        rows = self.env.cr.dictfetchall()
        devices = sorted({row['device_id'] for row in rows})
        # Seed each counter with its last valid reading, even across long outages.
        for field in COUNTERS[table]:
            self.env.cr.execute("""
                SELECT DISTINCT ON (device_id) * FROM {table}
                 WHERE device_id = ANY(%s) AND record_date < %s
                   AND {field} >= 0
                   AND (counter_validity->>%s) IS DISTINCT FROM 'false'
                   AND ({field} <> 0 OR counter_validity->>%s = 'true')
              ORDER BY device_id, record_date DESC, id DESC
            """.format(table=table, field=field), [devices, lower, field, field])
            rows.extend(self.env.cr.dictfetchall())
        grouped = defaultdict(list)
        for row in rows:
            grouped[row['device_id']].append(row)
        return grouped

    @api.model
    def _enrich_hourly(self, start, end):
        table = self._table.removesuffix('_summary')
        grouped = self._quality_rows(table, start, end)
        summaries = self.search([('bucket_type', '=', 'hour'),
                                 ('bucket_start', '>=', start), ('bucket_start', '<', end)])
        indexed = {}
        for device, rows in grouped.items():
            rows.sort(key=lambda row: (row['record_date'], row['id']))
            seeds, indices = {}, []
            for index, row in enumerate(rows):
                indices.append(dict(seeds))
                for field in COUNTERS[table]:
                    if counter_is_valid(row, field):
                        seeds[field] = index
            indexed[device] = (rows, [r['record_date'] for r in rows], indices)
        for summary in summaries:
            if summary.device_id.id not in indexed:
                continue
            all_rows, dates, indices = indexed[summary.device_id.id]
            left = bisect_left(dates, summary.bucket_start)
            right = bisect_left(dates, summary.bucket_start + timedelta(hours=1))
            positions = set(range(max(0, left - 1), min(len(all_rows), right + 1)))
            if left < len(indices):
                positions.update(indices[left].values())
            rows = [all_rows[index] for index in sorted(positions)]
            count = sum(summary.bucket_start <= row['record_date'] <
                        summary.bucket_start + timedelta(hours=1) for row in rows)
            if not count or count < summary.sample_count:
                continue  # Do not overwrite metadata for buckets whose raw was purged.
            if summary.quality_version == VERSION and count == summary.sample_count and not any(
                    row['record_date'] < summary.bucket_start for row in rows):
                continue  # Preserve boundary delta/estimate when its preceding raw was purged.
            result = calculate(rows, table, summary.bucket_start,
                               summary.bucket_start + timedelta(hours=1))
            values = {key: result[key] for key in ('power_energy', 'metric_extrema', 'counter_quality', 'metric_statistics')}
            values['quality_version'] = VERSION
            for field, result_counter in result['counters'].items():
                if field == 'limiter_total':
                    names = ('limiter_energy_kwh', 'limiter_total_start', 'limiter_total_end', 'limiter_reset_count')
                elif field == 'energy_total':
                    names = ('energy_kwh', 'energy_total_start', 'energy_total_end', 'counter_reset_count')
                else:
                    names = ('energy_kwh', 'total_kwh_start', 'total_kwh_end', 'counter_reset_count')
                values.update(zip(names, [result_counter['energy'], result_counter['first'],
                                           result_counter['last'], result_counter['resets']]))
            summary.write(values)

    @api.model
    def _enrich_daily(self, start, end):
        days = self.search([('bucket_type', '=', 'day'),
                           ('bucket_start', '>=', start), ('bucket_start', '<', end)])
        for day in days:
            tz = ZoneInfo(day.system_id.timezone or 'Asia/Ho_Chi_Minh')
            local = day.bucket_start.replace(tzinfo=timezone.utc).astimezone(tz)
            next_day = (local + timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None)
            hours = self.search([('bucket_type', '=', 'hour'), ('device_id', '=', day.device_id.id),
                                 ('bucket_start', '>=', day.bucket_start), ('bucket_start', '<', next_day)])
            if sum(hours.mapped('sample_count')) < day.sample_count:
                continue  # Retained day cannot be rebuilt from partially purged hours.
            # Legacy summaries have unknown estimate/peak metadata; keep it explicit.
            if not hours or any(hour.quality_version != VERSION for hour in hours):
                day.write({'quality_version': 0, 'power_energy': False,
                           'metric_extrema': False, 'counter_quality': False, 'metric_statistics': False})
                continue
            power, extrema, counters, statistics = {}, {}, {}, {}
            duration = (next_day - day.bucket_start).total_seconds()
            for hour in hours:
                for key, item in (hour.metric_statistics or {}).items():
                    dest = statistics.get(key)
                    if dest is None:
                        statistics[key] = dict(item)
                        continue
                    dest['count'] += item['count']
                    dest['sum'] += item['sum']
                    dest['min'] = min(dest['min'], item['min'])
                    dest['max'] = max(dest['max'], item['max'])
                    for field, earlier in [('first', True), ('last', False)]:
                        at = field + '_at'
                        if (item[at] < dest[at] if earlier else item[at] > dest[at]):
                            dest[field], dest[at] = item[field], item[at]
                for key, item in (hour.power_energy or {}).items():
                    dest = power.setdefault(key, {'value': 0.0, 'covered_seconds': 0.0,
                        'duration_seconds': duration, 'estimated': True, 'method': 'trapezoidal_power',
                        'max_gap_seconds': 300})
                    dest['value'] += item.get('value') or 0
                    dest['covered_seconds'] += item.get('covered_seconds') or 0
                for key, item in (hour.metric_extrema or {}).items():
                    dest = extrema.setdefault(key, {})
                    for kind in ('min', 'max'):
                        candidate = item[kind]
                        current = dest.get(kind)
                        if (not current or (candidate['value'] < current['value'] if kind == 'min'
                                            else candidate['value'] > current['value'])
                            or (candidate['value'] == current['value'] and
                                candidate['observed_at'] < current['observed_at'])):
                            dest[kind] = candidate
                for key, item in (hour.counter_quality or {}).items():
                    dest = counters.setdefault(key, {'valid_samples': 0, 'invalid_samples': 0,
                        'ambiguous_zero_samples': 0, 'reset_count': 0, 'min': None, 'max': None,
                        'stalled_with_power': False, 'reliable': True})
                    for field in ('valid_samples', 'invalid_samples', 'ambiguous_zero_samples', 'reset_count'):
                        dest[field] += item.get(field, 0)
                    for field in ('min', 'max'):
                        if item.get(field) is not None:
                            values = [value for value in (dest[field], item[field]) if value is not None]
                            dest[field] = min(values) if field == 'min' else max(values)
                    dest['stalled_with_power'] |= item.get('stalled_with_power', False)
                    dest['reliable'] &= item.get('reliable', False)
            for item in power.values():
                item['coverage_pct'] = item['covered_seconds'] / duration * 100
                if not item['covered_seconds']:
                    item['value'] = None
            for item in counters.values():
                item['constant'] = item['min'] is not None and item['min'] == item['max']
            day.write({'power_energy': power, 'metric_extrema': extrema,
                       'counter_quality': counters, 'metric_statistics': statistics, 'quality_version': VERSION})
