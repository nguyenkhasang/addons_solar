"""Dashboard-only readers; legacy public metrics keep their existing contract."""
from collections import defaultdict
from bisect import bisect_left
from datetime import timedelta, timezone

from odoo import api, fields, models
from .smartsolar_dashboard import _to_local


class DashboardReaders(models.AbstractModel):
    _inherit = 'smartsolar.dashboard'

    def _resolve_time_range(self, time_range, system_id=None):
        cfg, start, end = super()._resolve_time_range(time_range, system_id)
        cutoff = self.env.context.get('smartsolar_dashboard_end_at')
        if cutoff is None:
            return cfg, start, end
        if time_range in ('today', 'this_month'):
            local = _to_local(cutoff, self._get_system_timezone(system_id)).replace(
                hour=0, minute=0, second=0, microsecond=0)
            if time_range == 'this_month':
                local = local.replace(day=1)
            start = local.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            start += cutoff-end
        return dict(cfg, delta=(cutoff-start).total_seconds()), start, cutoff

    @api.model
    def get_dashboard_overview(self, system_id=None):
        cutoff = fields.Datetime.now()
        reader = self.with_context(smartsolar_dashboard_read_ledger=True,
                                   smartsolar_dashboard_end_at=cutoff)
        return {
            'kpi': reader._get_dashboard_overview(system_id, {}, cutoff),
            'bms': self.env['smartsolar.battery'].get_snapshots(system_id),
            'devices': self.get_device_status(system_id), 'systems': self.get_system_options(),
            'environment': self.get_environment(system_id),
            'cutoff': fields.Datetime.to_string(cutoff),
        }

    @api.model
    def _get_dashboard_overview(self, system_id, daily_cache, end_at):
        settings = self._get_settings()
        systems = self.env['smartsolar.system'].search(
            [('id', '=', int(system_id))] if system_id else [])
        devices = self.env['smartsolar.device'].search([
            ('system_id', '=', int(system_id))] if system_id else [])
        online = len(devices.filtered('is_online'))
        power = self._get_current_total_power(system_id)
        capacity = sum(systems.mapped('capacity'))
        totals = self.env['smartsolar.lifetime'].get_totals(system_id)
        return {
            'total_systems': len(systems), 'total_devices': len(devices),
            'online_devices': online, 'offline_devices': len(devices)-online,
            'current_power_w': round(power, 2), 'current_power_kw': round(power/1000, 3),
            'capacity_kw': round(capacity, 2),
            'efficiency': round(power/(capacity*1000)*100, 1) if capacity > 0 else 0,
            'total_kwh': totals['inverter_kwh'], 'total_limiter_kwh': totals['grid_kwh'],
            'total_pv_kwh': totals['pv_kwh'], 'revenue_total': totals['saved'],
            'lifetime': totals, 'currency_symbol': settings['currency_symbol'],
            'refresh_interval': settings['refresh_interval'],
            'temperature_alert': settings['temperature_alert'],
            'pv_peak': self._get_today_power_peak('pv', system_id),
            'load_peak': self._get_today_power_peak('load', system_id),
            'energy_widgets': self.get_energy_widgets(system_id, settings, daily_cache, end_at),
        }

    def _read_energy_ledger(self, start, end, system_id, cache=None):
        """Use validated completed days; exact partial days retain the raw reader.

        Global multi-system validation is not necessarily the sum of independent
        counter validations. Preserve that original reader for that scope.
        """
        timezone_name = self._get_system_timezone(system_id)
        if not system_id:
            systems = self.env['smartsolar.system'].with_context(active_test=False).search([])
            if len(systems) != 1 or self._get_system_timezone(systems.id) != timezone_name:
                return None
            system_id = systems.id
        account = self.env['smartsolar.lifetime'].search([('system_id', '=', int(system_id))], limit=1)
        if not account or not account.first_day:
            return None
        cache = cache if cache is not None else {}
        result_key = ('ledger_range', system_id, timezone_name, start, end)
        if result_key in cache:
            return cache[result_key]
        local = _to_local(start, timezone_name).replace(hour=0, minute=0, second=0, microsecond=0)
        rows = self.env['smartsolar.lifetime.day'].search([
            ('lifetime_id', '=', account.id), ('day', '>=', local.date()),
            ('day', '<=', _to_local(end, timezone_name).date())])
        by_day = {row.day: row for row in rows}
        pending = set(account.pending_days or [])
        result = {key: [] for key in ('labels', 'inverter_kwh', 'grid_kwh', 'total_kwh',
                                      'pv_kwh', 'estimated', 'notes', 'coverage_pct')}
        raw_reader = self.with_context(smartsolar_dashboard_read_ledger=False)
        today = _to_local(fields.Datetime.now(), timezone_name).date()
        while True:
            left = local.astimezone(timezone.utc).replace(tzinfo=None)
            right = (local+timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None)
            if left >= end:
                break
            a, b = max(start, left), min(end, right)
            key = ('ledger_day', system_id, timezone_name, a, b)
            item = cache.get(key)
            if item is None:
                row = by_day.get(local.date())
                complete = a == left and b == right and local.date() < today
                if complete and row and local.date().isoformat() not in pending:
                    known = row.known
                    inverter = row.inverter_kwh if known else None
                    grid = row.grid_kwh if known else None
                    item = {'inverter_kwh': inverter, 'grid_kwh': grid,
                            'total_kwh': round(inverter+grid, 3) if known else None,
                            'pv_kwh': row.pv_kwh if row.pv_known else None,
                            'estimated': row.estimated,
                            'coverage_pct': row.coverage_pct if row.estimated else None,
                            'notes': ('Ước tính từ công suất inverter và lấy lưới trên cùng phần thời gian có dữ liệu; không ngoại suy.'
                                      if row.estimated else 'Điện năng theo công-tơ trong khoảng đã chọn.')}
                elif complete and local.date() < account.first_day:
                    item = {key: None for key in ('inverter_kwh', 'grid_kwh', 'total_kwh', 'pv_kwh')}
                    item.update(estimated=True, coverage_pct=0, notes='Chưa có dữ liệu trong ngày.')
                else:
                    daily = raw_reader.get_energy_comparison('1month', system_id,
                        daily_cache=cache, date_bounds=(a, b))
                    item = {key: daily[key][0] for key in result if key != 'labels'}
                cache[key] = item
            result['labels'].append(local.date().isoformat())
            for name in item:
                result[name].append(item[name])
            local += timedelta(days=1)
        result['energy_kwh'] = result['inverter_kwh']
        result['source'] = 'validated_daily_ledger'
        result['history_as_of'] = fields.Datetime.to_string(account.updated_at) if account.updated_at else None
        cache[result_key] = result
        return result

    @api.model
    def get_heatmap_data(self, system_id=None):
        if not self.env.context.get('smartsolar_dashboard_read_ledger'):
            return super().get_heatmap_data(system_id)
        # UTC hours avoid timezone conversion per raw row. A fractional-offset
        # timezone has different local-hour boundaries and keeps the old reader.
        tz = self._get_system_timezone(system_id)
        end = self.env.context.get('smartsolar_dashboard_end_at') or fields.Datetime.now()
        if _to_local(end, tz).utcoffset().total_seconds() % 3600:
            return super().get_heatmap_data(system_id)
        start = end-timedelta(days=30)
        hours = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: [0.0, 0])))
        for table, model, definitions in (
            ('grid_tie_inverter', 'grid.tie.inverter.summary', (
                ('grid_out', 'output_power', 'output_power'),
                ('total_load', 'total_load_power', 'COALESCE(output_power,0)+COALESCE(limiter_power,0)'))),
            ('charge_power', 'charge.power.summary', (
                ('pv_input', 'pv_input', 'pv_voltage*pv_current'),))):
            scope = ' AND system_id=%s' if system_id else ''
            values = ', '.join(f'SUM({expr}),COUNT({expr})' for _, _, expr in definitions)
            self.env.cr.execute(f'''SELECT device_id,date_trunc('hour',record_date),
                COUNT(*),MAX(write_date),{values} FROM {table}
                WHERE record_date>=%s AND record_date<%s {scope}
                GROUP BY device_id,date_trunc('hour',record_date)''',
                [start, end]+([int(system_id)] if system_id else []))
            raw = {(row[0], row[1]): row for row in self.env.cr.fetchall()}
            retained = {}
            summary_values = ', '.join(
                f"(metric_statistics->'{field}'->>'sum')::double precision,"
                f"(metric_statistics->'{field}'->>'count')::integer"
                for _, field, _ in definitions)
            self.env.cr.execute(f"""SELECT device_id,bucket_start,sample_count,write_date,
                {summary_values} FROM {table}_summary WHERE bucket_type='hour'
                AND quality_version=4 AND bucket_start>=%s AND bucket_start<=%s {scope}""",
                [start, end-timedelta(hours=1)]+([int(system_id)] if system_id else []))
            for row in self.env.cr.fetchall():
                source = raw.get((row[0], row[1]))
                if source and (source[2] != row[2] or (source[3] and source[3] > row[3])):
                    continue
                if table == 'grid_tie_inverter' and row[7] != row[2]:
                    continue
                if all(value is not None for value in row[4:]):
                    retained[(row[0], row[1])] = row
            for key in raw.keys() | retained.keys():
                local = _to_local(key[1], tz)
                for index, (metric, field, _) in enumerate(definitions):
                    source = retained.get(key, raw.get(key))
                    total, count = source[4+index*2:6+index*2]
                    item = hours[local.date().isoformat()][local.hour][metric]
                    item[0] += float(total or 0)
                    item[1] += count or 0
        days = sorted(hours)
        series = {}
        for metric in ('grid_out', 'pv_input', 'total_load'):
            values = [[round(hours[day][hour][metric][0]/hours[day][hour][metric][1], 1)
                       if hours[day][hour][metric][1] else 0 for hour in range(24)] for day in days]
            series[metric] = {'values': values, 'max_val': max((v for row in values for v in row), default=1) or 1}
        return dict(days=days, hours=list(range(24)), series=series, **series['grid_out'])

    @api.model
    def get_battery_flow_series(self, time_range='24h', system_id=None):
        if not self.env.context.get('smartsolar_dashboard_read_ledger'):
            return super().get_battery_flow_series(time_range, system_id)
        self.env.flush_all()
        cfg, start, end = self._resolve_time_range(time_range, system_id)
        bucket = cfg['truncate_expr'].replace('bucket_start', 'record_date')
        if cfg['source'] == 'hourly':
            bucket = "date_trunc('hour',record_date)"
        elif cfg['source'] == 'daily' and cfg['bucket'] == 'day':
            tz = self._get_safe_timezone(system_id)
            bucket = f"timezone('UTC', timezone('{tz}', date_trunc('day', timezone('{tz}', record_date AT TIME ZONE 'UTC'))))"
        domain = [('device_type', 'in', ['charge_power', 'grid_tie_inverter'])]
        if system_id:
            domain.append(('system_id', '=', int(system_id)))
        devices = self.env['smartsolar.device'].search(domain)
        systems = defaultdict(lambda: {'charge_power': [], 'grid_tie_inverter': []})
        for device in devices:
            systems[device.system_id.id][device.device_type].append(device.id)
        lookup, anchors = {}, {}
        for kind, table, expressions in (
            ('charge_power', 'charge_power', 'bat_voltage*bat_current,pv_voltage*pv_current,bat_voltage'),
            ('grid_tie_inverter', 'grid_tie_inverter', 'output_power')):
            ids = devices.filtered(lambda d: d.device_type == kind).ids
            if not ids:
                continue
            self.env.cr.execute(f'''SELECT id,system_id,device_id,record_date,
                {expressions},{bucket} FROM {table}
                WHERE record_date>=%s AND record_date<=%s AND device_id=ANY(%s)
                ORDER BY record_date,id''', [start-timedelta(seconds=10), end+timedelta(seconds=10), ids])
            points = defaultdict(dict)
            for row in self.env.cr.fetchall():
                identifier, sid, device, at, *values = row
                if kind == 'charge_power' and start <= at < end and device in systems[sid][kind]:
                    anchors[(sid, at)] = values[-1]
                if kind == 'charge_power' and not (values[2] is not None and values[2] > 0):
                    continue
                # Latest eligible id at duplicate timestamps, as DISTINCT ON
                # in the SQL reader; eligibility is applied BEFORE deduplication.
                points[device][at] = (identifier, values[:-1])
            for device, values in points.items():
                times = sorted(values)
                lookup[device] = (times, values)

        def nearest(device, at):
            times, values = lookup.get(device, ([], {}))
            if at in values:
                return values[at][1], 0.0
            index = bisect_left(times, at)
            before = times[index-1] if index else None
            after = times[index] if index < len(times) else None
            left = (at-before).total_seconds() if before is not None else float('inf')
            right = (after-at).total_seconds() if after is not None else float('inf')
            if left == right and before is not None and after is not None:
                stamp = before if values[before][0] > values[after][0] else after
            else:
                stamp = before if left < right else after
            gap = min(left, right)
            return (values[stamp][1], gap) if stamp is not None and gap <= 10 else None

        def mean(values):
            known = [float(value) for value in values if value is not None]
            return sum(known)/len(known) if known else None

        groups = defaultdict(list)
        for (sid, at), stamp in anchors.items():
            chargers = [nearest(device, at) for device in systems[sid]['charge_power']]
            inverters = [nearest(device, at) for device in systems[sid]['grid_tie_inverter']]
            cp_ok = bool(chargers) and all(row and row[0][0] is not None for row in chargers)
            gt_ok = bool(inverters) and all(row and row[0][0] is not None for row in inverters)
            dc = sum(float(row[0][0]) for row in chargers) if cp_ok else None
            pv_values = [row[0][1] for row in chargers] if cp_ok else []
            pv = sum(float(value) for value in pv_values if value is not None) if any(
                value is not None for value in pv_values) else None
            net = dc-sum(float(row[0][0]) for row in inverters) if cp_ok and gt_ok else None
            current = net/float(chargers[0][0][2]) if net is not None and len(chargers) == 1 else None
            skew = max((row[1] for row in chargers+inverters if row), default=None)
            groups[(stamp, sid)].append((pv, dc, net, current, skew))
        buckets = defaultdict(list)
        for (stamp, sid), points in groups.items():
            buckets[stamp].append(([mean([p[i] for p in points]) for i in range(4)],
                sum(p[2] is not None for p in points), len(points),
                max((p[4] for p in points if p[4] is not None), default=None)))
        rows = []
        for stamp, groups in sorted(buckets.items()):
            totals = [sum(group[0][i] for group in groups) if all(
                group[0][i] is not None for group in groups) else None for i in range(3)]
            rows.append((stamp, *totals, groups[0][0][3] if len(groups) == 1 else None,
                         sum(g[1] for g in groups), sum(g[2] for g in groups),
                         max((g[3] for g in groups if g[3] is not None), default=None)))
        number = lambda value: round(float(value), 3) if value is not None else None
        timezone_name = self._get_system_timezone(system_id)
        return {
            'labels': [_to_local(row[0], timezone_name).strftime('%Y-%m-%d %H:%M:%S') for row in rows],
            **{name: [number(row[index]) for row in rows] for index, name in enumerate(
                ('pv_input_power', 'mppt_output_power', 'net_power', 'net_current'), 1)},
            'paired_count': [row[5] for row in rows], 'sample_count': [row[6] for row in rows],
            'max_skew_seconds': [number(row[7]) for row in rows],
            'estimated': True, 'source': 'matched_raw', 'max_pair_gap_seconds': 10,
            'time_range': time_range, 'bucket': cfg['bucket'],
            'note': 'Ước tính V×I đầu ra MPPT trừ AC OUT, chưa đối chiếu BMS/chưa trừ tổn hao inverter. '
                    'Chỉ gộp mẫu đủ các thiết bị trong ±10 giây; không dựng dữ liệu từ summary trung bình. '
                    'Dòng ròng chỉ có khi phạm vi gồm một hệ thống và một MPPT.'}
