"""JK BMS latest state and sampled history; BLE runs in a separate daemon."""
from datetime import timedelta, timezone
from odoo import api, fields, models
from odoo.exceptions import ValidationError, AccessError
from ..services.jk_bms.protocol import validate_state, connection_status


class SmartSolarBattery(models.Model):
    _name = 'smartsolar.battery'
    _description = 'Pin JK BMS'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    system_id = fields.Many2one('smartsolar.system', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='system_id.company_id', store=True)
    device_identifier = fields.Char(required=True, index=True)
    bms_model = fields.Char(readonly=True)
    firmware = fields.Char(readonly=True)
    hardware = fields.Char(readonly=True)
    last_seen = fields.Datetime(readonly=True, index=True)
    last_history_at = fields.Datetime(readonly=True)
    latest_state = fields.Json(readonly=True)
    history_enabled = fields.Boolean(string='Lưu lịch sử', default=True,
                                     help='Tắt để chỉ cập nhật dữ liệu trực tiếp. Lịch sử đã lưu vẫn được giữ theo thời hạn lưu trữ.')
    history_interval = fields.Integer(default=60, required=True)
    retention_days = fields.Integer(default=30, required=True)
    stale_seconds = fields.Integer(default=10, required=True)
    offline_seconds = fields.Integer(default=60, required=True)
    current_deadband = fields.Float(default=.2, required=True)
    cell_delta_warning = fields.Float(default=.03, required=True, help='Ngưỡng chênh cell (V)')
    telemetry_ids = fields.One2many('smartsolar.battery.telemetry', 'battery_id')
    _identifier_unique = models.Constraint('UNIQUE(device_identifier)', 'BLE address phải duy nhất.')

    @api.constrains('history_interval', 'retention_days', 'stale_seconds', 'offline_seconds', 'current_deadband', 'cell_delta_warning')
    def _check_config(self):
        for battery in self:
            if (battery.history_interval < 30 or battery.retention_days < 1 or
                    not 0 < battery.stale_seconds < battery.offline_seconds or
                    battery.current_deadband < 0 or battery.cell_delta_warning <= 0):
                raise ValidationError('Cấu hình nhịp mẫu/ngưỡng BMS không hợp lệ.')

    def _ingest(self, state):
        self.ensure_one()
        if not self.env.user.has_group('smartsolar.group_bms_collector'):
            raise AccessError('BMS collector permission required')
        self.check_access('write')
        if not self.active or state.get('device_identifier') != self.device_identifier:
            raise ValidationError('Battery identity mismatch or inactive')
        try:
            at = validate_state(state)
        except (TypeError, ValueError, KeyError) as error:
            raise ValidationError(str(error)) from error
        now = fields.Datetime.now()
        if at > now + timedelta(seconds=30) or at < now - timedelta(seconds=self.offline_seconds):
            raise ValidationError('Stale/future telemetry rejected')
        # Lock prevents concurrent collectors advancing the same sample slot.
        self.env.cr.execute('SELECT id FROM smartsolar_battery WHERE id=%s FOR UPDATE', [self.id])
        self.invalidate_recordset(['last_seen', 'last_history_at'])
        if self.last_seen and at <= self.last_seen:
            return False
        clean = {k: state[k] for k in (
            'timestamp', 'voltage', 'current', 'soc', 'remaining_capacity', 'nominal_capacity',
            'cycle_count', 'cycle_capacity', 'cells', 'mos_temperature', 'battery_temperature_1',
            'battery_temperature_2', 'charge_mos', 'discharge_mos', 'balancing', 'balance_current', 'alarm_bits')}
        cells = clean['cells']
        clean.update(power=clean['voltage'] * clean['current'],
                     cell_min_voltage=min(cells), cell_max_voltage=max(cells),
                     cell_min_index=cells.index(min(cells)) + 1, cell_max_index=cells.index(max(cells)) + 1,
                     cell_delta_voltage=max(cells) - min(cells), average_cell_voltage=sum(cells) / 16)
        values = {'latest_state': clean, 'last_seen': at,
                  'bms_model': state.get('bms_model'), 'firmware': state.get('firmware'), 'hardware': state.get('hardware')}
        if self.history_enabled and (not self.last_history_at or (at - self.last_history_at).total_seconds() >= self.history_interval):
            self.env['smartsolar.battery.telemetry'].create({
                'battery_id': self.id, 'record_date': at, 'state': clean,
                **{k: clean[k] for k in ('voltage', 'current', 'power', 'soc', 'cell_delta_voltage')},
                'temperature': max([v for k, v in clean.items() if 'temperature' in k and v is not None], default=0),
            })
            values['last_history_at'] = at
        self.write(values)
        self.env['bus.bus']._sendone(f'smartsolar.realtime.{self.system_id.id}', 'smartsolar_bms', self._snapshot())
        self.env['bus.bus']._sendone('smartsolar.realtime.all', 'smartsolar_bms', self._snapshot())
        return True

    def _snapshot(self):
        self.ensure_one()
        self.check_access('read')
        age = max(0, (fields.Datetime.now() - self.last_seen).total_seconds()) if self.last_seen else None
        state = dict(self.latest_state or {})
        current = state.get('current')
        state.update(id=self.id, name=self.name, system_id=self.system_id.id,
                     bms_model=self.bms_model, firmware=self.firmware,
                     age_seconds=age, status=connection_status(age, self.stale_seconds, self.offline_seconds),
                     stale_seconds=self.stale_seconds, offline_seconds=self.offline_seconds,
                     history_enabled=self.history_enabled, history_interval=self.history_interval,
                     cell_delta_warning=self.cell_delta_warning,
                     flow_status=('Chưa có dữ liệu' if current is None else 'Nghỉ' if abs(current) < self.current_deadband else 'Đang sạc' if current > 0 else 'Đang xả'))
        return state

    @api.model
    def get_snapshots(self, system_id=None):
        domain = [('active', '=', True)]
        if system_id:
            domain.append(('system_id', '=', int(system_id)))
        return [b._snapshot() for b in self.search(domain)]

    @api.model
    def _cron_purge_history(self):
        for battery in self.search([]):
            self.env['smartsolar.battery.telemetry'].search([
                ('battery_id', '=', battery.id),
                ('record_date', '<', fields.Datetime.now() - timedelta(days=battery.retention_days)),
            ], limit=10000).unlink()


class SmartSolarBatteryTelemetry(models.Model):
    _name = 'smartsolar.battery.telemetry'
    _description = 'Lịch sử pin JK BMS'
    _order = 'record_date desc, id desc'

    battery_id = fields.Many2one('smartsolar.battery', required=True, ondelete='cascade', index=True)
    record_date = fields.Datetime(required=True, index=True)
    state = fields.Json(required=True)
    voltage = fields.Float(digits=(16, 3))
    current = fields.Float(digits=(16, 3))
    power = fields.Float()
    soc = fields.Float()
    cell_delta_voltage = fields.Float(digits=(16, 4))
    temperature = fields.Float()
