# -*- coding: utf-8 -*-
"""Dashboard aggregation logic cho Smart Solar."""
from datetime import timedelta, timezone
from collections import OrderedDict, defaultdict

from odoo import models, fields, api, _

_UTC7 = timezone(timedelta(hours=7))


def _to_utc7(dt):
    """Chuyển datetime naive (UTC) sang UTC+7."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_UTC7)


TIME_RANGE_CONFIG = {
    '2min': {'delta': 120,     'source': 'raw',
             'truncate_expr': "date_trunc('second', record_date)",
             'bucket': 'second'},
    '1h':   {'delta': 3600,    'source': 'raw',
             'truncate_expr': "date_trunc('minute', record_date)",
             'bucket': 'minute'},
    '6h':   {'delta': 21600,   'source': 'raw',
             'truncate_expr': "to_timestamp(floor(extract(epoch from record_date) / 300) * 300)",
             'bucket': '5min'},
    '12h':  {'delta': 43200,   'source': 'raw',
             'truncate_expr': "to_timestamp(floor(extract(epoch from record_date) / 600) * 600)",
             'bucket': '10min'},
    '24h':  {'delta': 86400,   'source': 'raw',
             'truncate_expr': "to_timestamp(floor(extract(epoch from record_date) / 1800) * 1800)",
             'bucket': '30min'},
    '1week':  {'delta': 604800,  'source': 'hourly',
               'truncate_expr': "bucket_start",
               'bucket': 'hour'},
    '1month': {'delta': 2592000, 'source': 'hourly',
               'truncate_expr': "date_trunc('day', bucket_start)",
               'bucket': 'day'},
    '3month': {'delta': 7776000,   'source': 'daily',
               'truncate_expr': "bucket_start",
               'bucket': 'day'},
    '6month': {'delta': 15552000,  'source': 'daily',
               'truncate_expr': "bucket_start",
               'bucket': 'day'},
    '1year':  {'delta': 31536000,  'source': 'daily',
               'truncate_expr': "date_trunc('week', bucket_start)",
               'bucket': 'week'},
    '5year':  {'delta': 157680000, 'source': 'daily',
               'truncate_expr': "date_trunc('month', bucket_start)",
               'bucket': 'month'},
}

TABLES = {
    'charge_power': {
        'raw': 'charge_power',
        'summary': 'charge_power_summary',
    },
    'grid_tie_inverter': {
        'raw': 'grid_tie_inverter',
        'summary': 'grid_tie_inverter_summary',
    },
}


class SmartSolarDashboard(models.AbstractModel):
    _name = 'smartsolar.dashboard'
    _description = 'Smart Solar Dashboard Aggregator'

    @api.model
    def _get_settings(self):
        Param = self.env['ir.config_parameter'].sudo()
        return {
            'electricity_price': float(Param.get_param('smartsolar.electricity_price', 2500.0) or 0),
            'co2_factor': float(Param.get_param('smartsolar.co2_factor', 0.5) or 0),
            'currency_symbol': Param.get_param('smartsolar.currency_symbol', '₫'),
            'refresh_interval': int(Param.get_param('smartsolar.dashboard_refresh', 60) or 0),
            'temperature_alert': float(Param.get_param('smartsolar.temperature_alert', 60.0) or 0),
        }

    @api.model
    def _resolve_time_range(self, time_range):
        cfg = TIME_RANGE_CONFIG.get(time_range) or TIME_RANGE_CONFIG['24h']
        date_to = fields.Datetime.now()
        date_from = date_to - timedelta(seconds=cfg['delta'])
        return cfg, date_from, date_to

    # ------------------------------------------------------------------
    # Overview KPI
    # ------------------------------------------------------------------
    @api.model
    def get_overview_kpi(self, system_id=None):
        settings = self._get_settings()
        System = self.env['smartsolar.system']
        Device = self.env['smartsolar.device']

        sys_domain = [('active', '=', True)]
        dev_domain = [('active', '=', True)]
        if system_id:
            sys_domain.append(('id', '=', int(system_id)))
            dev_domain.append(('system_id', '=', int(system_id)))

        total_systems = System.search_count(sys_domain)
        total_devices = Device.search_count(dev_domain)
        online_devices = Device.search_count(dev_domain + [('is_online', '=', True)])
        offline_devices = total_devices - online_devices
        capacity_kw = sum(System.search(sys_domain).mapped('capacity') or [0])
        current_power_w = self._get_current_total_power(system_id)
        today_kwh = self._get_today_energy(system_id)
        total_kwh = self._get_total_energy(system_id)
        total_limiter_kwh = self._get_total_limiter_energy(system_id)
        total_pv_kwh = self._get_total_pv_energy(system_id)
        today_pv_kwh = self._get_today_pv_energy(system_id)
        today_grid_export_kwh = self._get_today_grid_export(system_id)
        today_grid_import_kwh = self._get_today_grid_import(system_id)
        capacity_w = capacity_kw * 1000.0
        efficiency = (current_power_w / capacity_w * 100.0) if capacity_w > 0 else 0.0
        co2_total_kg = round(total_kwh * settings['co2_factor'], 2)

        # Hệ hybrid: PV → MPPT → Pin → GTI → Lưới
        # today_pv_kwh = MPPT today_kwh (điện PV thu được sạc vào pin)
        # today_kwh (=GTI energy_today) = điện hòa lưới thực tế
        # today_grid_import_kwh = limiter_today (điện lấy từ lưới vào)

        # Self-consumption: % điện PV không xuất lưới (dùng sạc pin / tải nhà)
        # = (PV_thu - Hoa_luoi) / PV_thu
        pv_self_use = max(today_pv_kwh - today_kwh, 0)
        self_consumption_pct = round(pv_self_use / today_pv_kwh * 100, 1) if today_pv_kwh > 0 else 0.0

        # Grid dependency: % điện tiêu thụ lấy từ lưới
        # = Lay_luoi / (Lay_luoi + Hoa_luoi)
        total_flow = today_grid_import_kwh + today_kwh
        grid_dependency_pct = round(today_grid_import_kwh / total_flow * 100, 1) if total_flow > 0 else 0.0

        # Yield: sản lượng hòa lưới / công suất danh định
        yield_kwh_per_kwp = round(today_kwh / capacity_kw, 3) if capacity_kw > 0 else 0.0

        # CO2 tree equivalent
        co2_trees = round(co2_total_kg / 21.77, 1)
        # Peak power time
        peak = self._get_peak_power_time(system_id)
        # Performance alert
        perf_alert = self.get_performance_alert(system_id)

        return {
            'total_systems': total_systems,
            'total_devices': total_devices,
            'online_devices': online_devices,
            'offline_devices': offline_devices,
            'current_power_w': round(current_power_w, 2),
            'current_power_kw': round(current_power_w / 1000.0, 3),
            'capacity_kw': round(capacity_kw, 2),
            'efficiency': round(efficiency, 1),
            'today_kwh': round(today_kwh, 3),
            'total_kwh': round(total_kwh, 3),
            'co2_saved_kg': round(today_kwh * settings['co2_factor'], 2),
            'co2_total_kg': co2_total_kg,
            'revenue_today': round(today_kwh * settings['electricity_price'], 0),
            'revenue_total': round(total_kwh * settings['electricity_price'], 0),
            'total_limiter_kwh': round(total_limiter_kwh, 3),
            'total_pv_kwh': round(total_pv_kwh, 3),
            'currency_symbol': settings['currency_symbol'],
            'refresh_interval': settings['refresh_interval'],
            'temperature_alert': settings['temperature_alert'],
            'self_consumption_pct': self_consumption_pct,
            'grid_dependency_pct': grid_dependency_pct,
            'yield_kwh_per_kwp': yield_kwh_per_kwp,
            'co2_trees': co2_trees,
            'peak_hour': peak.get('hour'),
            'peak_power_w': peak.get('power_w', 0),
            'perf_alert': perf_alert.get('alert', False),
            'perf_deviation_pct': perf_alert.get('deviation_pct', 0),
        }

    @api.model
    def _get_total_limiter_energy(self, system_id=None):
        params = []
        sys_filter = ''
        if system_id:
            sys_filter = 'AND system_id = %s'
            params.append(int(system_id))
        sql = f"""
            SELECT COALESCE(SUM(max_e), 0) FROM (
                SELECT MAX(limiter_total) AS max_e FROM grid_tie_inverter
                WHERE 1=1 {sys_filter} GROUP BY device_id
            ) t
        """
        self.env.cr.execute(sql, params)
        return float(self.env.cr.fetchone()[0] or 0)

    @api.model
    def _get_total_pv_energy(self, system_id=None):
        params = []
        sys_filter = ''
        if system_id:
            sys_filter = 'AND system_id = %s'
            params.append(int(system_id))
        sql_sum = f"""
            SELECT COALESCE(SUM(latest), 0) FROM (
                SELECT DISTINCT ON (device_id) device_id, total_kwh_end AS latest
                FROM charge_power_summary
                WHERE bucket_type = 'hour' {sys_filter}
                ORDER BY device_id, bucket_start DESC
            ) t
        """
        self.env.cr.execute(sql_sum, params)
        val = float(self.env.cr.fetchone()[0] or 0)
        if val > 0:
            return val
        sql_raw = f"""
            SELECT COALESCE(SUM(max_e), 0) FROM (
                SELECT MAX(total_kwh) AS max_e FROM charge_power
                WHERE 1=1 {sys_filter} GROUP BY device_id
            ) t
        """
        self.env.cr.execute(sql_raw, params)
        return float(self.env.cr.fetchone()[0] or 0)

    @api.model
    def _get_today_pv_energy(self, system_id=None):
        """Năng lượng PV sản xuất hôm nay (charge_power.today_kwh)."""
        params = []
        sys_filter = ''
        if system_id:
            sys_filter = 'AND system_id = %s'
            params.append(int(system_id))
        sql = f"""
            SELECT COALESCE(SUM(max_e), 0) FROM (
                SELECT MAX(today_kwh) AS max_e FROM charge_power
                WHERE record_date::date = CURRENT_DATE {sys_filter} GROUP BY device_id
            ) t
        """
        self.env.cr.execute(sql, params)
        return float(self.env.cr.fetchone()[0] or 0)

    @api.model
    def _get_today_grid_export(self, system_id=None):
        """Năng lượng xuất lưới hôm nay (grid_tie_inverter.energy_today)."""
        params = []
        sys_filter = ''
        if system_id:
            sys_filter = 'AND system_id = %s'
            params.append(int(system_id))
        sql = f"""
            SELECT COALESCE(SUM(max_e), 0) FROM (
                SELECT MAX(energy_today) AS max_e FROM grid_tie_inverter
                WHERE record_date::date = CURRENT_DATE {sys_filter} GROUP BY device_id
            ) t
        """
        self.env.cr.execute(sql, params)
        return float(self.env.cr.fetchone()[0] or 0)

    @api.model
    def _get_today_grid_import(self, system_id=None):
        """Năng lượng lấy từ lưới hôm nay (grid_tie_inverter.limiter_today)."""
        params = []
        sys_filter = ''
        if system_id:
            sys_filter = 'AND system_id = %s'
            params.append(int(system_id))
        sql = f"""
            SELECT COALESCE(SUM(max_e), 0) FROM (
                SELECT MAX(limiter_today) AS max_e FROM grid_tie_inverter
                WHERE record_date::date = CURRENT_DATE {sys_filter} GROUP BY device_id
            ) t
        """
        self.env.cr.execute(sql, params)
        return float(self.env.cr.fetchone()[0] or 0)

    @api.model
    def _get_peak_power_time(self, system_id=None):
        """Giờ sản xuất công suất cao nhất hôm nay."""
        params = []
        sys_filter = ''
        if system_id:
            sys_filter = 'AND system_id = %s'
            params.append(int(system_id))
        sql = f"""
            SELECT EXTRACT(HOUR FROM record_date + INTERVAL '7 hours') AS hour,
                   MAX(output_power) AS max_w
            FROM grid_tie_inverter
            WHERE record_date::date = CURRENT_DATE {sys_filter}
            GROUP BY hour ORDER BY max_w DESC LIMIT 1
        """
        self.env.cr.execute(sql, params)
        row = self.env.cr.fetchone()
        if row:
            return {'hour': int(row[0]), 'power_w': round(float(row[1] or 0), 0)}
        return {'hour': None, 'power_w': 0}

    @api.model
    def get_performance_alert(self, system_id=None):
        """So sánh sản lượng hôm nay vs trung bình 7 ngày gần nhất cùng khoảng giờ."""
        params_today = []
        params_avg = []
        sys_filter = ''
        if system_id:
            sys_filter = 'AND system_id = %s'
            params_today.append(int(system_id))
            params_avg.append(int(system_id))

        # Sản lượng hôm nay
        sql_today = f"""
            SELECT COALESCE(SUM(max_e), 0) FROM (
                SELECT MAX(energy_today) AS max_e FROM grid_tie_inverter
                WHERE record_date::date = CURRENT_DATE {sys_filter} GROUP BY device_id
            ) t
        """
        self.env.cr.execute(sql_today, params_today)
        today_kwh = float(self.env.cr.fetchone()[0] or 0)

        # Trung bình 7 ngày gần nhất (cùng khoảng giờ trong ngày — dùng SQL để tránh UTC edge cases)
        sql_avg = f"""
            SELECT COALESCE(AVG(daily_e), 0) FROM (
                SELECT record_date::date AS d, MAX(energy_today) AS daily_e
                FROM grid_tie_inverter
                WHERE record_date::date BETWEEN CURRENT_DATE - 8 AND CURRENT_DATE - 1
                  AND (record_date + INTERVAL '7 hours')::time <=
                      (NOW() AT TIME ZONE 'Asia/Ho_Chi_Minh')::time
                  {sys_filter}
                GROUP BY d, device_id
            ) t
        """
        self.env.cr.execute(sql_avg, params_avg)
        avg_kwh = float(self.env.cr.fetchone()[0] or 0)

        if avg_kwh > 0:
            deviation_pct = round((today_kwh - avg_kwh) / avg_kwh * 100, 1)
        else:
            deviation_pct = 0.0

        return {
            'alert': deviation_pct < -20,
            'today_kwh': round(today_kwh, 3),
            'avg_kwh': round(avg_kwh, 3),
            'deviation_pct': deviation_pct,
        }

    @api.model
    def get_heatmap_data(self, system_id=None):
        """Ma trận 24h × 30 ngày — công suất trung bình theo giờ và ngày."""
        params = []
        sys_filter = ''
        if system_id:
            sys_filter = 'AND system_id = %s'
            params.append(int(system_id))
        sql = f"""
            SELECT
                (record_date + INTERVAL '7 hours')::date AS day,
                EXTRACT(HOUR FROM record_date + INTERVAL '7 hours')::int AS hour,
                AVG(output_power) AS avg_w
            FROM grid_tie_inverter
            WHERE record_date >= NOW() - INTERVAL '30 days' {sys_filter}
            GROUP BY day, hour ORDER BY day, hour
        """
        self.env.cr.execute(sql, params)
        rows = self.env.cr.fetchall()

        data_map = defaultdict(dict)
        days_set = set()
        for day, hour, avg_w in rows:
            key = day.strftime('%Y-%m-%d') if hasattr(day, 'strftime') else str(day)
            data_map[key][int(hour)] = round(float(avg_w or 0), 1)
            days_set.add(key)

        days = sorted(days_set)
        hours = list(range(24))
        values = [[data_map[d].get(h, 0) for h in hours] for d in days]
        max_val = max((v for row in values for v in row), default=1) or 1

        return {'days': days, 'hours': hours, 'values': values, 'max_val': round(max_val, 1)}

    @api.model
    def get_monthly_comparison(self, system_id=None):
        """So sánh sản lượng hòa lưới tháng năm nay vs năm trước — hệ hybrid: chỉ GTI."""
        params = []
        sys_filter = ''
        if system_id:
            sys_filter = 'AND system_id = %s'
            params.append(int(system_id))

        sql = f"""
            SELECT
                DATE_TRUNC('month', bucket_start) AS month,
                SUM(energy_kwh) AS total
            FROM grid_tie_inverter_summary
            WHERE bucket_type = 'day'
              AND bucket_start >= NOW() - INTERVAL '2 years'
              {sys_filter}
            GROUP BY month ORDER BY month
        """
        self.env.cr.execute(sql, params)
        rows = self.env.cr.fetchall()

        from datetime import datetime as dt
        now = fields.Datetime.now()
        this_year = {}
        last_year = {}
        for month, total in rows:
            if hasattr(month, 'year'):
                label = month.strftime('%m/%Y')
                if month.year == now.year:
                    this_year[month.month] = round(float(total or 0), 3)
                elif month.year == now.year - 1:
                    last_year[month.month] = round(float(total or 0), 3)

        months = list(range(1, 13))
        labels = [f'T{m}' for m in months]
        return {
            'labels': labels,
            'this_year': [this_year.get(m, 0) for m in months],
            'last_year': [last_year.get(m, 0) for m in months],
            'this_year_label': str(now.year),
            'last_year_label': str(now.year - 1),
        }

    @api.model
    def get_energy_flow_series(self, time_range='1week', system_id=None):
        """Stacked bar: PV self-use, xuất lưới, lấy lưới theo ngày."""
        cfg, date_from, date_to = self._resolve_time_range(time_range)
        bucket_type = 'hour' if cfg['source'] == 'hourly' else 'day'
        params = [bucket_type, date_from, date_to]
        sys_filter = ''
        if system_id:
            sys_filter = 'AND system_id = %s'
            params.append(int(system_id))

        truncate = cfg['truncate_expr']
        sql = f"""
            WITH gti AS (
                SELECT {truncate} AS d,
                       SUM(energy_kwh) AS export_kwh,
                       SUM(COALESCE((SELECT SUM(energy_kwh) FROM charge_power_summary
                           WHERE bucket_type = %s
                             AND bucket_start BETWEEN %s AND %s
                             {sys_filter}
                           LIMIT 1), 0)) AS pv_kwh
                FROM grid_tie_inverter_summary
                WHERE bucket_type = %s AND bucket_start BETWEEN %s AND %s {sys_filter}
                GROUP BY d
            )
            SELECT d FROM gti ORDER BY d
        """
        # Simplified approach: query GTI and CP separately then combine
        params_gti = [bucket_type, date_from, date_to]
        if system_id:
            params_gti.append(int(system_id))
        sql_gti = f"""
            SELECT {truncate} AS d, SUM(energy_kwh) AS export_kwh
            FROM grid_tie_inverter_summary
            WHERE bucket_type = %s AND bucket_start BETWEEN %s AND %s {sys_filter}
            GROUP BY d ORDER BY d
        """
        sql_cp = f"""
            SELECT {truncate} AS d, SUM(energy_kwh) AS pv_kwh
            FROM charge_power_summary
            WHERE bucket_type = %s AND bucket_start BETWEEN %s AND %s {sys_filter}
            GROUP BY d ORDER BY d
        """
        sql_import = f"""
            SELECT {truncate} AS d,
                   COALESCE(SUM(energy_kwh), 0) AS import_kwh
            FROM grid_tie_inverter_summary
            WHERE bucket_type = %s AND bucket_start BETWEEN %s AND %s {sys_filter}
              AND limiter_power_avg > 0
            GROUP BY d ORDER BY d
        """
        self.env.cr.execute(sql_gti, params_gti)
        gti_rows = {r[0]: float(r[1] or 0) for r in self.env.cr.fetchall()}
        self.env.cr.execute(sql_cp, params_gti)
        cp_rows = {r[0]: float(r[1] or 0) for r in self.env.cr.fetchall()}
        self.env.cr.execute(sql_import, params_gti)
        import_rows = {r[0]: float(r[1] or 0) for r in self.env.cr.fetchall()}

        all_days = sorted(set(list(gti_rows.keys()) + list(cp_rows.keys())))
        fmt = '%Y-%m-%d' if cfg['bucket'] in ('day', 'week') else '%Y-%m-%d %H:%M'

        labels, export_data, self_use_data, import_data = [], [], [], []
        for d in all_days:
            labels.append(_to_utc7(d).strftime(fmt) if hasattr(d, 'strftime') else str(d))
            pv = cp_rows.get(d, 0)
            export = gti_rows.get(d, 0)
            imp = import_rows.get(d, 0)
            self_use = max(pv - export, 0)
            export_data.append(round(export, 3))
            self_use_data.append(round(self_use, 3))
            import_data.append(round(imp, 3))

        return {
            'labels': labels,
            'export': export_data,
            'self_use': self_use_data,
            'import_grid': import_data,
            'source': cfg['source'],
        }

    @api.model
    def _get_current_total_power(self, system_id=None):
        """Tổng công suất hiện tại:
        - GTI output_power: công suất hòa lưới hiện tại
        Hệ thống hybrid: PV → MPPT → Pin → GTI → Lưới
        Công suất thực tế ra lưới chỉ lấy từ GTI output_power.
        MPPT pv_input không tính ở đây vì đó là điện vào pin, chưa ra lưới.
        """
        params = []
        sys_filter = ''
        if system_id:
            sys_filter = 'AND system_id = %s'
            params.append(int(system_id))
        sql_gti = f"""
            SELECT COALESCE(SUM(output_power), 0) FROM (
                SELECT DISTINCT ON (device_id) device_id, output_power
                FROM grid_tie_inverter
                WHERE record_date >= NOW() - INTERVAL '10 minutes' {sys_filter}
                ORDER BY device_id, record_date DESC
            ) t
        """
        self.env.cr.execute(sql_gti, params)
        gti_total = self.env.cr.fetchone()[0] or 0
        return float(gti_total)

    @api.model
    def _get_today_energy(self, system_id=None):
        """Sản lượng hôm nay — hệ hybrid: chỉ tính GTI energy_today (kWh hòa lưới).
        MPPT today_kwh là điện PV sạc vào pin, không phải sản lượng cuối cùng ra lưới."""
        params = []
        sys_filter = ''
        if system_id:
            sys_filter = 'AND system_id = %s'
            params.append(int(system_id))
        sql_gti = f"""
            SELECT COALESCE(SUM(max_e), 0) FROM (
                SELECT MAX(energy_today) AS max_e FROM grid_tie_inverter
                WHERE record_date::date = CURRENT_DATE {sys_filter} GROUP BY device_id
            ) t
        """
        self.env.cr.execute(sql_gti, params)
        gti = self.env.cr.fetchone()[0] or 0
        return float(gti)

    @api.model
    def _get_total_energy(self, system_id=None):
        """Tổng năng lượng hòa lưới — hệ hybrid: chỉ tính GTI energy_total."""
        params = []
        sys_filter = ''
        if system_id:
            sys_filter = 'AND system_id = %s'
            params.append(int(system_id))
        sql_gti = f"""
            SELECT COALESCE(SUM(latest), 0) FROM (
                SELECT DISTINCT ON (device_id) device_id, energy_total_end AS latest
                FROM grid_tie_inverter_summary
                WHERE bucket_type = 'hour' {sys_filter}
                ORDER BY device_id, bucket_start DESC
            ) t
        """
        self.env.cr.execute(sql_gti, params)
        gti = self.env.cr.fetchone()[0] or 0
        if gti == 0:
            return self._get_total_energy_from_raw(system_id)
        return float(gti)

    @api.model
    def _get_total_energy_from_raw(self, system_id=None):
        params = []
        sys_filter = ''
        if system_id:
            sys_filter = 'AND system_id = %s'
            params.append(int(system_id))
        sql_gti = f"SELECT COALESCE(SUM(max_e), 0) FROM (SELECT MAX(energy_total) AS max_e FROM grid_tie_inverter WHERE 1=1 {sys_filter} GROUP BY device_id) t"
        self.env.cr.execute(sql_gti, params)
        return float(self.env.cr.fetchone()[0] or 0)

    # ------------------------------------------------------------------
    # Charge Power series
    # ------------------------------------------------------------------
    @api.model
    def get_charge_power_series(self, time_range='24h', system_id=None, device_ids=None):
        cfg, date_from, date_to = self._resolve_time_range(time_range)
        if cfg['source'] == 'raw':
            sql, params = self._build_charge_power_raw_sql(cfg, date_from, date_to, system_id, device_ids)
        else:
            sql, params = self._build_charge_power_summary_sql(cfg, date_from, date_to, system_id, device_ids)
        self.env.cr.execute(sql, params)
        rows = self.env.cr.fetchall()
        return {
            'labels': [_to_utc7(r[0]).strftime('%Y-%m-%d %H:%M:%S') if r[0] else '' for r in rows],
            'avg_power': [round(float(r[1] or 0), 2) for r in rows],
            'max_power': [round(float(r[2] or 0), 2) for r in rows],
            'pv_voltage': [round(float(r[3] or 0), 2) for r in rows],
            'bat_voltage': [round(float(r[4] or 0), 2) for r in rows],
            'pv_current': [round(float(r[5] or 0), 2) for r in rows],
            'bat_current': [round(float(r[6] or 0), 2) for r in rows],
            'temperature': [round(float(r[7] or 0), 2) for r in rows],
            'pv_input_power': [round(float(r[3] or 0) * float(r[5] or 0), 2) for r in rows],
            'time_range': time_range,
            'bucket': cfg['bucket'],
            'source': cfg['source'],
        }

    def _build_charge_power_raw_sql(self, cfg, date_from, date_to, system_id, device_ids):
        params = [date_from, date_to]
        filters = ''
        if system_id:
            filters += ' AND system_id = %s'
            params.append(int(system_id))
        if device_ids:
            filters += ' AND device_id = ANY(%s)'
            params.append([int(d) for d in device_ids])
        sql = f"""
            SELECT {cfg['truncate_expr']} AS bucket,
                   AVG(charge_power), MAX(charge_power),
                   AVG(pv_voltage), AVG(bat_voltage),
                   AVG(pv_current), AVG(bat_current),
                   AVG(temperature)
            FROM charge_power
            WHERE record_date BETWEEN %s AND %s {filters}
            GROUP BY bucket ORDER BY bucket
        """
        return sql, params

    def _build_charge_power_summary_sql(self, cfg, date_from, date_to, system_id, device_ids):
        bucket_type = 'hour' if cfg['source'] == 'hourly' else 'day'
        params = [bucket_type, date_from, date_to]
        filters = ''
        if system_id:
            filters += ' AND system_id = %s'
            params.append(int(system_id))
        if device_ids:
            filters += ' AND device_id = ANY(%s)'
            params.append([int(d) for d in device_ids])
        sql = f"""
            SELECT {cfg['truncate_expr']} AS bucket,
                   AVG(charge_power_avg), MAX(charge_power_max),
                   AVG(pv_voltage_avg), AVG(bat_voltage_avg),
                   AVG(pv_current_avg), AVG(bat_current_avg),
                   AVG(temperature_avg)
            FROM charge_power_summary
            WHERE bucket_type = %s AND bucket_start BETWEEN %s AND %s {filters}
            GROUP BY bucket ORDER BY bucket
        """
        return sql, params

    # ------------------------------------------------------------------
    # Grid Tie Inverter series
    # ------------------------------------------------------------------
    @api.model
    def get_grid_tie_series(self, time_range='24h', system_id=None, device_ids=None):
        cfg, date_from, date_to = self._resolve_time_range(time_range)
        if cfg['source'] == 'raw':
            sql, params = self._build_grid_tie_raw_sql(cfg, date_from, date_to, system_id, device_ids)
        else:
            sql, params = self._build_grid_tie_summary_sql(cfg, date_from, date_to, system_id, device_ids)
        self.env.cr.execute(sql, params)
        rows = self.env.cr.fetchall()
        return {
            'labels': [_to_utc7(r[0]).strftime('%Y-%m-%d %H:%M:%S') if r[0] else '' for r in rows],
            'output_power': [round(float(r[1] or 0), 2) for r in rows],
            'max_power': [round(float(r[2] or 0), 2) for r in rows],
            'total_power': [round(float(r[3] or 0), 2) for r in rows],
            'ac_voltage': [round(float(r[4] or 0), 2) for r in rows],
            'dc_voltage': [round(float(r[5] or 0), 2) for r in rows],
            'temperature': [round(float(r[6] or 0), 2) for r in rows],
            'limiter_power': [round(float(r[7] or 0), 2) for r in rows],
            'time_range': time_range,
            'bucket': cfg['bucket'],
            'source': cfg['source'],
        }

    def _build_grid_tie_raw_sql(self, cfg, date_from, date_to, system_id, device_ids):
        params = [date_from, date_to]
        filters = ''
        if system_id:
            filters += ' AND system_id = %s'
            params.append(int(system_id))
        if device_ids:
            filters += ' AND device_id = ANY(%s)'
            params.append([int(d) for d in device_ids])
        sql = f"""
            SELECT {cfg['truncate_expr']} AS bucket,
                   AVG(output_power), MAX(output_power),
                   AVG(total_power),
                   AVG(ac_voltage), AVG(dc_voltage),
                   AVG(temperature), AVG(limiter_power)
            FROM grid_tie_inverter
            WHERE record_date BETWEEN %s AND %s {filters}
            GROUP BY bucket ORDER BY bucket
        """
        return sql, params

    def _build_grid_tie_summary_sql(self, cfg, date_from, date_to, system_id, device_ids):
        bucket_type = 'hour' if cfg['source'] == 'hourly' else 'day'
        params = [bucket_type, date_from, date_to]
        filters = ''
        if system_id:
            filters += ' AND system_id = %s'
            params.append(int(system_id))
        if device_ids:
            filters += ' AND device_id = ANY(%s)'
            params.append([int(d) for d in device_ids])
        sql = f"""
            SELECT {cfg['truncate_expr']} AS bucket,
                   AVG(output_power_avg), MAX(output_power_max),
                   AVG(total_power_avg),
                   AVG(ac_voltage_avg), AVG(dc_voltage_avg),
                   AVG(temperature_avg), AVG(limiter_power_avg)
            FROM grid_tie_inverter_summary
            WHERE bucket_type = %s AND bucket_start BETWEEN %s AND %s {filters}
            GROUP BY bucket ORDER BY bucket
        """
        return sql, params

    # ------------------------------------------------------------------
    # Battery series
    # ------------------------------------------------------------------
    @api.model
    def get_battery_series(self, time_range='24h', system_id=None, device_ids=None):
        cfg, date_from, date_to = self._resolve_time_range(time_range)
        if cfg['source'] == 'raw':
            sql, params = self._build_battery_raw_sql(cfg, date_from, date_to, system_id, device_ids)
        else:
            sql, params = self._build_battery_summary_sql(cfg, date_from, date_to, system_id, device_ids)
        self.env.cr.execute(sql, params)
        rows = self.env.cr.fetchall()
        return {
            'labels': [_to_utc7(r[0]).strftime('%Y-%m-%d %H:%M:%S') if r[0] else '' for r in rows],
            'bat_voltage': [round(float(r[1] or 0), 2) for r in rows],
            'bat_voltage_min': [round(float(r[2] or 0), 2) for r in rows],
            'bat_current': [round(float(r[3] or 0), 2) for r in rows],
            'time_range': time_range,
            'bucket': cfg['bucket'],
            'source': cfg['source'],
        }

    def _build_battery_raw_sql(self, cfg, date_from, date_to, system_id, device_ids):
        params = [date_from, date_to]
        filters = ''
        if system_id:
            filters += ' AND system_id = %s'
            params.append(int(system_id))
        if device_ids:
            filters += ' AND device_id = ANY(%s)'
            params.append([int(d) for d in device_ids])
        sql = f"""
            SELECT {cfg['truncate_expr']} AS bucket,
                   AVG(bat_voltage), MIN(NULLIF(bat_voltage, 0)), AVG(bat_current)
            FROM charge_power
            WHERE record_date BETWEEN %s AND %s {filters}
            GROUP BY bucket ORDER BY bucket
        """
        return sql, params

    def _build_battery_summary_sql(self, cfg, date_from, date_to, system_id, device_ids):
        bucket_type = 'hour' if cfg['source'] == 'hourly' else 'day'
        params = [bucket_type, date_from, date_to]
        filters = ''
        if system_id:
            filters += ' AND system_id = %s'
            params.append(int(system_id))
        if device_ids:
            filters += ' AND device_id = ANY(%s)'
            params.append([int(d) for d in device_ids])
        sql = f"""
            SELECT {cfg['truncate_expr']} AS bucket,
                   AVG(bat_voltage_avg), MIN(NULLIF(bat_voltage_min, 0)), AVG(bat_current_avg)
            FROM charge_power_summary
            WHERE bucket_type = %s AND bucket_start BETWEEN %s AND %s {filters}
            GROUP BY bucket ORDER BY bucket
        """
        return sql, params

    # ------------------------------------------------------------------
    # PV Efficiency series
    # ------------------------------------------------------------------
    @api.model
    def get_pv_efficiency_series(self, time_range='24h', system_id=None, device_ids=None):
        cfg, date_from, date_to = self._resolve_time_range(time_range)
        if cfg['source'] == 'raw':
            sql, params = self._build_pv_efficiency_raw_sql(cfg, date_from, date_to, system_id, device_ids)
        else:
            sql, params = self._build_pv_efficiency_summary_sql(cfg, date_from, date_to, system_id, device_ids)
        self.env.cr.execute(sql, params)
        rows = self.env.cr.fetchall()
        return {
            'labels': [_to_utc7(r[0]).strftime('%Y-%m-%d %H:%M:%S') if r[0] else '' for r in rows],
            'pv_input_power': [round(float(r[1] or 0), 2) for r in rows],
            'charge_power': [round(float(r[2] or 0), 2) for r in rows],
            'efficiency': [round(float(r[3] or 0), 1) for r in rows],
            'time_range': time_range,
            'bucket': cfg['bucket'],
            'source': cfg['source'],
        }

    def _build_pv_efficiency_raw_sql(self, cfg, date_from, date_to, system_id, device_ids):
        params = [date_from, date_to]
        filters = ''
        if system_id:
            filters += ' AND system_id = %s'
            params.append(int(system_id))
        if device_ids:
            filters += ' AND device_id = ANY(%s)'
            params.append([int(d) for d in device_ids])
        sql = f"""
            SELECT {cfg['truncate_expr']} AS bucket,
                   AVG(pv_voltage * pv_current) AS pv_input,
                   AVG(charge_power) AS charge_pwr,
                   CASE WHEN AVG(pv_voltage * pv_current) > 0
                        THEN LEAST(AVG(charge_power) / AVG(pv_voltage * pv_current) * 100, 100)
                        ELSE 0 END AS efficiency
            FROM charge_power
            WHERE record_date BETWEEN %s AND %s {filters}
            GROUP BY bucket ORDER BY bucket
        """
        return sql, params

    def _build_pv_efficiency_summary_sql(self, cfg, date_from, date_to, system_id, device_ids):
        bucket_type = 'hour' if cfg['source'] == 'hourly' else 'day'
        params = [bucket_type, date_from, date_to]
        filters = ''
        if system_id:
            filters += ' AND system_id = %s'
            params.append(int(system_id))
        if device_ids:
            filters += ' AND device_id = ANY(%s)'
            params.append([int(d) for d in device_ids])
        sql = f"""
            SELECT {cfg['truncate_expr']} AS bucket,
                   AVG(pv_voltage_avg * pv_current_avg) AS pv_input,
                   AVG(charge_power_avg) AS charge_pwr,
                   CASE WHEN AVG(pv_voltage_avg * pv_current_avg) > 0
                        THEN LEAST(AVG(charge_power_avg) / AVG(pv_voltage_avg * pv_current_avg) * 100, 100)
                        ELSE 0 END AS efficiency
            FROM charge_power_summary
            WHERE bucket_type = %s AND bucket_start BETWEEN %s AND %s {filters}
            GROUP BY bucket ORDER BY bucket
        """
        return sql, params

    # ------------------------------------------------------------------
    # Energy comparison
    # ------------------------------------------------------------------
    @api.model
    def get_energy_comparison(self, time_range='1week', system_id=None):
        cfg, date_from, date_to = self._resolve_time_range(time_range)
        if cfg['source'] == 'raw':
            return self._energy_comparison_from_raw(cfg, date_from, date_to, system_id)
        return self._energy_comparison_from_summary(cfg, date_from, date_to, system_id)

    def _energy_comparison_from_raw(self, cfg, date_from, date_to, system_id):
        """Hệ hybrid: chỉ dùng GTI energy_today làm sản lượng hòa lưới."""
        params = [date_from, date_to]
        sys_filter = ''
        if system_id:
            sys_filter = 'AND system_id = %s'
            params.append(int(system_id))
        sql = f"""
            SELECT date_trunc('day', record_date) AS d, SUM(max_e) AS total
            FROM (
                SELECT date_trunc('day', record_date) AS record_date,
                       MAX(energy_today) AS max_e
                FROM grid_tie_inverter
                WHERE record_date BETWEEN %s AND %s {sys_filter}
                GROUP BY date_trunc('day', record_date), device_id
            ) t
            GROUP BY d ORDER BY d
        """
        self.env.cr.execute(sql, params)
        rows = self.env.cr.fetchall()
        return {
            'labels': [_to_utc7(r[0]).strftime('%Y-%m-%d') if r[0] else '' for r in rows],
            'energy_kwh': [round(float(r[1] or 0), 3) for r in rows],
            'source': 'raw',
        }

    def _energy_comparison_from_summary(self, cfg, date_from, date_to, system_id):
        bucket_type = 'hour' if cfg['source'] == 'hourly' else 'day'
        """Hệ hybrid: chỉ dùng GTI energy_kwh (sản lượng hòa lưới) từ summary."""
        bucket_type = 'hour' if cfg['source'] == 'hourly' else 'day'
        params = [bucket_type, date_from, date_to]
        sys_filter = ''
        if system_id:
            sys_filter = 'AND system_id = %s'
            params.append(int(system_id))
        truncate = cfg['truncate_expr']
        sql = f"""
            SELECT {truncate} AS d, COALESCE(SUM(energy_kwh), 0)
            FROM grid_tie_inverter_summary
            WHERE bucket_type = %s AND bucket_start BETWEEN %s AND %s {sys_filter}
            GROUP BY d ORDER BY d
        """
        self.env.cr.execute(sql, params)
        rows = self.env.cr.fetchall()
        fmt = '%Y-%m-%d' if cfg['bucket'] in ('day', 'week') else '%Y-%m'
        if cfg['bucket'] == 'hour':
            fmt = '%Y-%m-%d %H:%M'
        return {
            'labels': [_to_utc7(r[0]).strftime(fmt) if r[0] else '' for r in rows],
            'energy_kwh': [round(float(r[1] or 0), 3) for r in rows],
            'source': cfg['source'],
        }

    # ------------------------------------------------------------------
    # Temperature series
    # ------------------------------------------------------------------
    @api.model
    def get_temperature_series(self, time_range='24h', system_id=None):
        cfg, date_from, date_to = self._resolve_time_range(time_range)
        if cfg['source'] == 'raw':
            sql, params = self._build_temperature_raw_sql(cfg, date_from, date_to, system_id)
        else:
            sql, params = self._build_temperature_summary_sql(cfg, date_from, date_to, system_id)
        self.env.cr.execute(sql, params)
        rows = self.env.cr.fetchall()
        bucket_set = OrderedDict()
        device_map = {}
        for r in rows:
            dev_id, dev_name, guid, bucket, avg_t, _max_t = r
            bucket_key = _to_utc7(bucket).strftime('%Y-%m-%d %H:%M:%S') if bucket else ''
            bucket_set[bucket_key] = True
            label = dev_name or guid or f'Device #{dev_id}'
            device_map.setdefault(dev_id, {'label': label, 'data': {}})
            device_map[dev_id]['data'][bucket_key] = round(float(avg_t or 0), 2)
        labels = list(bucket_set.keys())
        series = [{'label': info['label'], 'data': [info['data'].get(b, None) for b in labels]}
                  for _dev_id, info in device_map.items()]
        return {'labels': labels, 'series': series, 'source': cfg['source']}

    def _build_temperature_raw_sql(self, cfg, date_from, date_to, system_id):
        params_base = [date_from, date_to]
        sys_filter = ''
        if system_id:
            sys_filter = 'AND system_id = %s'
            params_base.append(int(system_id))
        sql = f"""
            SELECT d.id, d.name, d.device_guid,
                   {cfg['truncate_expr'].replace('record_date', 't.record_date')} AS bucket,
                   AVG(t.temperature), MAX(t.temperature)
            FROM (
                SELECT device_id, system_id, record_date, temperature FROM grid_tie_inverter
                WHERE record_date BETWEEN %s AND %s {sys_filter}
                UNION ALL
                SELECT device_id, system_id, record_date, temperature FROM charge_power
                WHERE record_date BETWEEN %s AND %s {sys_filter}
            ) t
            JOIN smartsolar_device d ON d.id = t.device_id
            GROUP BY d.id, d.name, d.device_guid, bucket ORDER BY bucket, d.id
        """
        return sql, params_base + params_base

    def _build_temperature_summary_sql(self, cfg, date_from, date_to, system_id):
        bucket_type = 'hour' if cfg['source'] == 'hourly' else 'day'
        params_base = [bucket_type, date_from, date_to]
        sys_filter = ''
        if system_id:
            sys_filter = 'AND system_id = %s'
            params_base.append(int(system_id))
        truncate = cfg['truncate_expr'].replace('bucket_start', 't.bucket_start')
        sql = f"""
            SELECT d.id, d.name, d.device_guid,
                   {truncate} AS bucket,
                   AVG(t.temperature_avg), MAX(t.temperature_max)
            FROM (
                SELECT device_id, system_id, bucket_start, temperature_avg, temperature_max
                FROM grid_tie_inverter_summary
                WHERE bucket_type = %s AND bucket_start BETWEEN %s AND %s {sys_filter}
                UNION ALL
                SELECT device_id, system_id, bucket_start, temperature_avg, temperature_max
                FROM charge_power_summary
                WHERE bucket_type = %s AND bucket_start BETWEEN %s AND %s {sys_filter}
            ) t
            JOIN smartsolar_device d ON d.id = t.device_id
            GROUP BY d.id, d.name, d.device_guid, bucket ORDER BY bucket, d.id
        """
        return sql, params_base + params_base

    # ------------------------------------------------------------------
    # Energy distribution
    # ------------------------------------------------------------------
    @api.model
    def get_energy_distribution(self, system_id=None):
        """Hệ hybrid: phân bổ theo luồng năng lượng thực tế.
        - Hòa lưới: GTI energy_total (điện xuất ra lưới)
        - Thu PV: MPPT total_kwh (điện PV thu vào pin)
        - Lấy lưới: GTI limiter_total (điện lấy từ lưới)
        """
        params = []
        sys_filter = ''
        if system_id:
            sys_filter = 'AND system_id = %s'
            params.append(int(system_id))
        sql_gti = f"""
            SELECT COALESCE(SUM(latest), 0) FROM (
                SELECT DISTINCT ON (device_id) device_id, energy_total_end AS latest
                FROM grid_tie_inverter_summary WHERE bucket_type = 'hour' {sys_filter}
                ORDER BY device_id, bucket_start DESC
            ) t
        """
        sql_cp = f"""
            SELECT COALESCE(SUM(latest), 0) FROM (
                SELECT DISTINCT ON (device_id) device_id, total_kwh_end AS latest
                FROM charge_power_summary WHERE bucket_type = 'hour' {sys_filter}
                ORDER BY device_id, bucket_start DESC
            ) t
        """
        sql_limiter = f"""
            SELECT COALESCE(SUM(max_e), 0) FROM (
                SELECT MAX(limiter_total) AS max_e FROM grid_tie_inverter
                WHERE 1=1 {sys_filter} GROUP BY device_id
            ) t
        """
        self.env.cr.execute(sql_gti, params)
        gti = float(self.env.cr.fetchone()[0] or 0)
        self.env.cr.execute(sql_cp, params)
        cp = float(self.env.cr.fetchone()[0] or 0)
        self.env.cr.execute(sql_limiter, params)
        limiter = float(self.env.cr.fetchone()[0] or 0)
        if gti == 0 and cp == 0:
            return self._distribution_from_raw(system_id)
        return {
            'labels': ['Hòa lưới (GTI)', 'Thu PV (MPPT)', 'Lấy lưới'],
            'data': [round(gti, 3), round(cp, 3), round(limiter, 3)],
        }

    def _distribution_from_raw(self, system_id):
        """Fallback raw cho hệ hybrid."""
        params = []
        sys_filter = ''
        if system_id:
            sys_filter = 'AND system_id = %s'
            params.append(int(system_id))
        sql_gti = f"SELECT COALESCE(SUM(max_e), 0) FROM (SELECT MAX(energy_total) AS max_e FROM grid_tie_inverter WHERE 1=1 {sys_filter} GROUP BY device_id) t"
        sql_cp = f"SELECT COALESCE(SUM(max_e), 0) FROM (SELECT MAX(total_kwh) AS max_e FROM charge_power WHERE 1=1 {sys_filter} GROUP BY device_id) t"
        sql_limiter = f"SELECT COALESCE(SUM(max_e), 0) FROM (SELECT MAX(limiter_total) AS max_e FROM grid_tie_inverter WHERE 1=1 {sys_filter} GROUP BY device_id) t"
        self.env.cr.execute(sql_gti, params)
        gti = float(self.env.cr.fetchone()[0] or 0)
        self.env.cr.execute(sql_cp, params)
        cp = float(self.env.cr.fetchone()[0] or 0)
        self.env.cr.execute(sql_limiter, params)
        limiter = float(self.env.cr.fetchone()[0] or 0)
        return {
            'labels': ['Hòa lưới (GTI)', 'Thu PV (MPPT)', 'Lấy lưới'],
            'data': [round(gti, 3), round(cp, 3), round(limiter, 3)],
        }

    # ------------------------------------------------------------------
    # Device status
    # ------------------------------------------------------------------
    @api.model
    def get_device_status(self, system_id=None):
        domain = [('active', '=', True)]
        if system_id:
            domain.append(('system_id', '=', int(system_id)))
        devices = self.env['smartsolar.device'].search(domain)
        settings = self._get_settings()
        result = []
        for dev in devices:
            latest_temp = 0.0
            latest_power = 0.0
            if dev.device_type == 'grid_tie_inverter':
                rec = self.env['grid.tie.inverter'].search(
                    [('device_id', '=', dev.id)], limit=1, order='record_date desc')
                if rec:
                    latest_temp = rec.temperature
                    latest_power = rec.output_power
            elif dev.device_type == 'charge_power':
                rec = self.env['charge.power'].search(
                    [('device_id', '=', dev.id)], limit=1, order='record_date desc')
                if rec:
                    latest_temp = rec.temperature
                    latest_power = rec.charge_power
            status = 'offline'
            if dev.is_online:
                status = 'warning' if latest_temp >= settings['temperature_alert'] else 'online'

            # Tính thời gian offline (phút)
            offline_minutes = None
            if not dev.is_online and dev.last_sync_date:
                delta = fields.Datetime.now() - dev.last_sync_date
                offline_minutes = int(delta.total_seconds() / 60)

            result.append({
                'id': dev.id,
                'name': dev.name or dev.device_guid,
                'guid': dev.device_guid,
                'type': dev.device_type,
                'is_online': dev.is_online,
                'status': status,
                'temperature': round(latest_temp, 1),
                'power': round(latest_power, 2),
                'firmware': dev.firmware_version or '',
                'last_sync': _to_utc7(dev.last_sync_date).strftime('%Y-%m-%d %H:%M:%S') if dev.last_sync_date else '',
                'system_name': dev.system_id.name if dev.system_id else '',
                'offline_minutes': offline_minutes,
            })
        return result

    # ------------------------------------------------------------------
    # System list
    # ------------------------------------------------------------------
    @api.model
    def get_system_options(self):
        systems = self.env['smartsolar.system'].search([('active', '=', True)])
        return [{'id': s.id, 'name': s.display_name, 'code': s.code} for s in systems]

    # ------------------------------------------------------------------
    # Environment (thời tiết) — chỉ có dữ liệu khi module
    # smartsolar_environment được cài. Truy cập phòng thủ để dashboard
    # không phụ thuộc cứng vào module đó.
    # ------------------------------------------------------------------
    @api.model
    def get_environment(self, system_id=None):
        """Bản ghi môi trường mới nhất (nếu module smartsolar_environment có cài).

        Trả về {available: bool, ...}. available=False khi chưa cài module môi
        trường hoặc chưa có dữ liệu -> frontend ẩn widget, không lỗi.
        """
        if 'smartsolar.environment' not in self.env:
            return {'available': False}

        domain = []
        if system_id:
            domain.append(('system_id', '=', int(system_id)))
        rec = self.env['smartsolar.environment'].search(
            domain, limit=1, order='record_date desc')
        if not rec:
            return {'available': False}

        return {
            'available': True,
            'record_date': _to_utc7(rec.record_date).strftime('%Y-%m-%d %H:%M') if rec.record_date else '',
            'system_name': rec.system_id.name if rec.system_id else '',
            'weather_code': rec.weather_code,
            'weather_description': rec.weather_description or '',
            # current
            'temperature': round(rec.temperature_2m, 1),
            'humidity': round(rec.relative_humidity_2m, 0),
            'wind_speed': round(rec.wind_speed_10m, 1),
            'cloud_cover': round(rec.cloud_cover, 0),
            'pressure': round(rec.surface_pressure, 0),
            'rain': round(rec.rain, 1),
            'precipitation': round(rec.precipitation, 1),
            # daily (hôm nay)
            'sunrise': _to_utc7(rec.sunrise).strftime('%H:%M') if rec.sunrise else '',
            'sunset': _to_utc7(rec.sunset).strftime('%H:%M') if rec.sunset else '',
            'sunshine_hours': round((rec.sunshine_duration or 0) / 3600.0, 1),
            'daylight_hours': round((rec.daylight_duration or 0) / 3600.0, 1),
            'uv_index_max': round(rec.uv_index_max, 1),
            'radiation': round(rec.shortwave_radiation_sum, 2),
            'temp_max': round(rec.temperature_2m_max, 1),
            'wind_max': round(rec.wind_speed_10m_max, 1),
            'rain_probability': round(rec.precipitation_probability_max, 0),
            'rain_sum': round(rec.precipitation_sum, 1),
        }

    # ------------------------------------------------------------------
    # All-in-one fetch
    # ------------------------------------------------------------------
    @api.model
    def get_dashboard_data(self, time_range='24h', system_id=None):
        energy_range = '1week' if time_range in ('1h', '6h', '12h', '24h', '2min') else time_range
        flow_range = '1week' if time_range in ('1h', '6h', '12h', '24h', '2min') else time_range
        return {
            'kpi': self.get_overview_kpi(system_id=system_id),
            'charge_power': self.get_charge_power_series(time_range=time_range, system_id=system_id),
            'grid_tie': self.get_grid_tie_series(time_range=time_range, system_id=system_id),
            'energy_comparison': self.get_energy_comparison(time_range=energy_range, system_id=system_id),
            'temperature': self.get_temperature_series(time_range=time_range, system_id=system_id),
            'battery': self.get_battery_series(time_range=time_range, system_id=system_id),
            'pv_efficiency': self.get_pv_efficiency_series(time_range=time_range, system_id=system_id),
            'distribution': self.get_energy_distribution(system_id=system_id),
            'devices': self.get_device_status(system_id=system_id),
            'systems': self.get_system_options(),
            'heatmap': self.get_heatmap_data(system_id=system_id),
            'monthly_comparison': self.get_monthly_comparison(system_id=system_id),
            'energy_flow': self.get_energy_flow_series(time_range=flow_range, system_id=system_id),
            'environment': self.get_environment(system_id=system_id),
            'time_range': time_range,
            'system_id': system_id,
        }
