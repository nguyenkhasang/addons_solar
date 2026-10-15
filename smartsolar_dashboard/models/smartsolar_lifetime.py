"""Persistent energy ledger. Dashboard reads never rebuild history.

Daily rows are replaceable evidence; monthly rows snapshot the tariff. Only the
delta of an affected month is applied to the system total in the same transaction.
"""
from datetime import datetime, time, timedelta, timezone

from odoo import api, fields, models
from .smartsolar_dashboard import _to_local


class SmartSolarLifetime(models.Model):
    _name = 'smartsolar.lifetime'
    _description = 'Tổng tích luỹ Smart Solar'

    system_id = fields.Many2one('smartsolar.system', required=True, ondelete='restrict', index=True)
    company_id = fields.Many2one(related='system_id.company_id', store=True)
    first_day = fields.Date()
    updated_at = fields.Datetime()
    scanned_at = fields.Datetime()
    pending_days = fields.Json(default=lambda self: [])
    initialized = fields.Boolean()
    inverter_kwh = fields.Float(digits=(20, 6))
    grid_kwh = fields.Float(digits=(20, 6))
    pv_kwh = fields.Float(digits=(20, 6))
    saved = fields.Float(digits=(20, 0))
    known_days = fields.Integer()
    pv_days = fields.Integer()
    estimated_days = fields.Integer()
    partial_days = fields.Integer()
    _system_unique = models.Constraint('UNIQUE(system_id)', 'Mỗi hệ thống có một tổng tích luỹ.')

    def init(self):
        # Background change detection must not scan every raw row as history grows.
        for table in ('grid_tie_inverter', 'charge_power',
                      'grid_tie_inverter_summary', 'charge_power_summary'):
            self.env.cr.execute(f'CREATE INDEX IF NOT EXISTS {table}_lifetime_write_idx '
                                f'ON {table} (write_date, system_id)')
        for table in ('grid_tie_inverter', 'charge_power'):
            self.env.cr.execute(f'CREATE INDEX IF NOT EXISTS {table}_device_time_idx '
                                f'ON {table} (device_id, record_date, id)')

    @api.model
    def get_totals(self, system_id=None):
        """Bounded read: one total per visible system; no source/history queries."""
        systems = self.env['smartsolar.system'].search(
            [('id', '=', int(system_id))] if system_id else [])
        rows = self.search([('system_id', 'in', systems.ids)])
        first = min([day for day in rows.mapped('first_day') if day] or [False])
        stamps = rows.mapped('updated_at')
        ready = len(rows) == len(systems) and bool(rows) and all(rows.mapped('initialized'))
        known = sum(rows.mapped('known_days'))
        pv_known = sum(rows.mapped('pv_days'))
        expected = sum((_to_local(row.updated_at or fields.Datetime.now(),
            row.system_id.timezone).date() - row.first_day).days + 1
            for row in rows if row.first_day)
        return {
            'inverter_kwh': round(sum(rows.mapped('inverter_kwh')), 3) if known else None,
            'grid_kwh': round(sum(rows.mapped('grid_kwh')), 3) if known else None,
            'pv_kwh': round(sum(rows.mapped('pv_kwh')), 3) if pv_known else None,
            'saved': round(sum(rows.mapped('saved'))) if known else None,
            'first_day': fields.Date.to_string(first) if first else None,
            'updated_at': _to_local(min(stamps), self.env['smartsolar.dashboard'].
                _get_system_timezone(system_id)).strftime('%Y-%m-%d %H:%M:%S')
                if stamps and all(stamps) else None,
            'initialized': ready, 'known_days': known,
            'missing_days': max(0, expected-known),
            'estimated_days': sum(rows.mapped('estimated_days')),
            'partial_days': sum(rows.mapped('partial_days')),
            'pending_days': sum(len(row.pending_days or []) for row in rows),
            # Before tariff history exists, historical savings are estimates.
            'historical_tariff_estimated': True,
        }

    @api.model
    def _cron_refresh(self, batch_days=31):
        # Serialize workers, including maintenance invocations. Transaction lock
        # also makes monthly replacements and total deltas atomic and repeatable.
        self.env.cr.execute('SELECT pg_try_advisory_xact_lock(194151014)')
        if not self.env.cr.fetchone()[0]:
            return False
        cutoff = fields.Datetime.now()
        dashboard = self.env['smartsolar.dashboard']
        tariff = dashboard._get_settings()['tariff']
        for system in self.env['smartsolar.system'].search([]):
            row = self.search([('system_id', '=', system.id)], limit=1)
            if not row:
                row = self.create({'system_id': system.id})
            row._enqueue_changes(cutoff)
            # Process recent days first: current-month data becomes usable early
            # while initial history fills in bounded batches in the background.
            days = sorted(row.pending_days or [], reverse=True)[:max(1, batch_days)]
            if days:
                row._refresh_dates(days, cutoff, tariff)
                pending = sorted(set(row.pending_days or []) - set(days))
                row.write({'pending_days': pending, 'initialized': not pending,
                           'updated_at': cutoff})
        return True

    def _enqueue_changes(self, cutoff):
        self.ensure_one()
        tz = self.env['smartsolar.dashboard']._get_system_timezone(self.system_id.id)
        today = _to_local(cutoff, tz).date()
        pending = set(self.pending_days or [])
        source_queries, params = [], []
        for table, date_col in (('grid_tie_inverter', 'record_date'), ('charge_power', 'record_date'),
                                ('grid_tie_inverter_summary', 'bucket_start'),
                                ('charge_power_summary', 'bucket_start')):
            source_queries.append(f'SELECT MIN({date_col}) AS at FROM {table} WHERE system_id=%s')
            params.append(self.system_id.id)
        if not self.first_day:
            self.env.cr.execute('SELECT MIN(at) FROM (' + ' UNION ALL '.join(source_queries) + ') s', params)
            at = self.env.cr.fetchone()[0]
            if not at:
                return
            first = _to_local(at, tz).date()
            self.first_day = first
            day = first
            while day <= today:
                pending.add(day.isoformat())
                day += timedelta(days=1)
        # An overlap avoids missing a writer that started before the prior scan
        # and committed afterwards. SQL changes are deduplicated by local day.
        since = (self.scanned_at or cutoff) - timedelta(minutes=5)
        queries, params = [], []
        for table, date_col in (('grid_tie_inverter', 'record_date'), ('charge_power', 'record_date'),
                                ('grid_tie_inverter_summary', 'bucket_start'),
                                ('charge_power_summary', 'bucket_start')):
            queries.append(f"SELECT DISTINCT timezone(%s, {date_col} AT TIME ZONE 'UTC')::date "
                           f'FROM {table} WHERE system_id=%s AND write_date>=%s')
            params.extend([tz, self.system_id.id, since])
        self.env.cr.execute(' UNION '.join(queries), params)
        changed = [item[0] for item in self.env.cr.fetchall()]
        if changed and min(changed) < self.first_day:
            day = min(changed)
            while day < self.first_day:
                pending.add(day.isoformat())
                day += timedelta(days=1)
            self.first_day = min(changed)
        # New day after midnight, including days with no samples, remains unknown.
        last = _to_local(self.scanned_at, tz).date() if self.scanned_at else today
        while last <= today:
            pending.add(last.isoformat())
            last += timedelta(days=1)
        for day in changed:
            # Paired integration and counter deltas may cross a midnight boundary.
            for adjacent in (day-timedelta(days=1), day, day+timedelta(days=1)):
                if self.first_day <= adjacent <= today:
                    pending.add(adjacent.isoformat())
        self.write({'pending_days': sorted(pending), 'scanned_at': cutoff})

    def _refresh_dates(self, dates, cutoff, tariff):
        """Replace selected days, then update only their months and total delta."""
        self.ensure_one()
        dashboard = self.env['smartsolar.dashboard']
        tz = _to_local(cutoff, self.system_id.timezone).tzinfo
        Day = self.env['smartsolar.lifetime.day']
        months = set()
        for label in sorted(set(dates)):
            day = fields.Date.to_date(label)
            start = datetime.combine(day, time.min, tz).astimezone(timezone.utc).replace(tzinfo=None)
            end = min(datetime.combine(day+timedelta(days=1), time.min, tz).astimezone(
                timezone.utc).replace(tzinfo=None), cutoff)
            if start >= end:
                continue
            daily = dashboard.get_energy_comparison('1month', self.system_id.id,
                                                   date_bounds=(start, end))
            if not daily['labels']:
                continue
            a, b, pv = (daily[key][0] for key in ('inverter_kwh', 'grid_kwh', 'pv_kwh'))
            known = a is not None and b is not None
            values = {'lifetime_id': self.id, 'day': day, 'known': known,
                      'inverter_kwh': a or 0, 'grid_kwh': b or 0,
                      'pv_known': pv is not None, 'pv_kwh': pv or 0,
                      'estimated': bool(daily['estimated'][0]),
                      'partial': known and daily['coverage_pct'][0] is not None
                                 and daily['coverage_pct'][0] < 99,
                      'coverage_pct': daily['coverage_pct'][0] or 0}
            old = Day.search([('lifetime_id', '=', self.id), ('day', '=', day)], limit=1)
            if old:
                # Raw retention must not replace validated retained evidence with
                # unknown. A changed source with available evidence can replace it.
                self.env.cr.execute('''SELECT
                    EXISTS(SELECT 1 FROM grid_tie_inverter
                           WHERE system_id=%s AND record_date>=%s AND record_date<%s),
                    EXISTS(SELECT 1 FROM charge_power
                           WHERE system_id=%s AND record_date>=%s AND record_date<%s)''',
                    [self.system_id.id, start, end, self.system_id.id, start, end])
                raw_grid, raw_pv = self.env.cr.fetchone()
                if not known and old.known and not raw_grid:
                    for key in ('known', 'inverter_kwh', 'grid_kwh', 'estimated', 'partial', 'coverage_pct'):
                        values[key] = old[key]
                if pv is None and old.pv_known and not raw_pv:
                    values.update(pv_known=True, pv_kwh=old.pv_kwh)
                old.write(values)
            else:
                Day.create(values)
            months.add(day.replace(day=1))
        for month in sorted(months):
            self._refresh_month(month, cutoff, tariff)

    def _refresh_month(self, month, cutoff, tariff):
        self.ensure_one()
        Month = self.env['smartsolar.lifetime.month']
        old = Month.search([('lifetime_id', '=', self.id), ('month', '=', month)], limit=1)
        today = _to_local(cutoff, self.system_id.timezone).date()
        # Closed periods keep their tariff even when an old day is corrected.
        applied = old.tariff if old and month < today.replace(day=1) else tariff
        rows = self.env['smartsolar.lifetime.day'].search([
            ('lifetime_id', '=', self.id), ('day', '>=', month),
            ('day', '<', (month.replace(day=28)+timedelta(days=4)).replace(day=1))], order='day')
        daily = {'labels': [fields.Date.to_string(r.day) for r in rows],
                 'inverter_kwh': [r.inverter_kwh if r.known else None for r in rows],
                 'grid_kwh': [r.grid_kwh if r.known else None for r in rows],
                 'total_kwh': [r.inverter_kwh+r.grid_kwh if r.known else None for r in rows],
                 'estimated': rows.mapped('estimated'), 'notes': [None]*len(rows),
                 'coverage_pct': rows.mapped('coverage_pct')}
        pv = [r.pv_kwh if r.pv_known else None for r in rows]
        result = self.env['smartsolar.dashboard']._build_energy_widgets(daily, pv, 0, tariff=applied)
        values = {'lifetime_id': self.id, 'month': month, 'tariff': applied,
                  'inverter_kwh': result['metrics'][1]['month'] or 0,
                  'grid_kwh': result['metrics'][0]['month'] or 0,
                  'pv_kwh': result['metrics'][3]['month'] or 0,
                  'saved': result['saved'] or 0, 'known_days': sum(rows.mapped('known')),
                  'pv_days': sum(rows.mapped('pv_known')),
                  'estimated_days': sum(r.known and r.estimated for r in rows),
                  'partial_days': sum(rows.mapped('partial'))}
        keys = ('inverter_kwh', 'grid_kwh', 'pv_kwh', 'saved', 'known_days',
                'pv_days', 'estimated_days', 'partial_days')
        self.write({key: self[key]+values[key]-(old[key] if old else 0) for key in keys})
        if old:
            old.write(values)
        else:
            Month.create(values)


class SmartSolarLifetimeDay(models.Model):
    _name = 'smartsolar.lifetime.day'
    _description = 'Điện năng tích luỹ theo ngày'
    lifetime_id = fields.Many2one('smartsolar.lifetime', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='lifetime_id.company_id', store=True)
    day = fields.Date(required=True, index=True)
    inverter_kwh = fields.Float(digits=(20, 6))
    grid_kwh = fields.Float(digits=(20, 6))
    pv_kwh = fields.Float(digits=(20, 6))
    known = fields.Boolean()
    pv_known = fields.Boolean()
    estimated = fields.Boolean()
    partial = fields.Boolean()
    coverage_pct = fields.Float()
    _day_unique = models.Constraint('UNIQUE(lifetime_id, day)', 'Một bản ghi mỗi ngày và hệ thống.')


class SmartSolarLifetimeMonth(models.Model):
    _name = 'smartsolar.lifetime.month'
    _description = 'Điện năng và tiền tiết kiệm theo kỳ'
    lifetime_id = fields.Many2one('smartsolar.lifetime', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='lifetime_id.company_id', store=True)
    month = fields.Date(required=True, index=True)
    tariff = fields.Json(required=True)
    inverter_kwh = fields.Float(digits=(20, 6))
    grid_kwh = fields.Float(digits=(20, 6))
    pv_kwh = fields.Float(digits=(20, 6))
    saved = fields.Float(digits=(20, 0))
    known_days = fields.Integer()
    pv_days = fields.Integer()
    estimated_days = fields.Integer()
    partial_days = fields.Integer()
    _month_unique = models.Constraint('UNIQUE(lifetime_id, month)', 'Một bản ghi mỗi kỳ và hệ thống.')
