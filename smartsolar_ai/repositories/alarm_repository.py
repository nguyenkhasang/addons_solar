# -*- coding: utf-8 -*-
"""AlarmRepository — kho cảnh báo.

Hiện CHƯA có model cảnh báo riêng, nên cảnh báo được SUY RA từ các tín hiệu quan
sát được: thiết bị offline, và chuỗi trạng thái (status) khác "bình thường".

Thiết kế hướng tương lai:
    Khi sau này thêm model ``smartsolar.alarm`` thật, chỉ cần mở rộng hàm
    ``fetch_alarms`` để đọc thêm từ model đó — các tầng gọi (Service/Tool) KHÔNG
    phải sửa gì. Đây là lợi ích của việc giấu nguồn dữ liệu sau Repository.
"""
from __future__ import annotations

from datetime import timezone

from odoo import fields

from .base_repository import BaseRepository
from ..domain.value_objects import TimeRange, UTC7

# Các chuỗi trạng thái được coi là "bình thường"/khỏe mạnh — ngoài tập này là cảnh báo.
_NORMAL_STATUS = {'', '0', 'normal', 'ok', 'running', 'charging', 'online'}


class AlarmRepository(BaseRepository):

    def fetch_alarms(self, time_range: TimeRange, severity=None,
                     device_id=None, system_id=None):
        """Gom cảnh báo từ nhiều nguồn, lọc theo mức độ, sắp xếp mới nhất trước.

        Ghép 2 nguồn suy diễn: thiết bị đang offline + trạng thái bất thường trong
        khoảng. Nếu truyền ``severity`` thì lọc theo mức độ đó.
        """
        alarms = []
        alarms += self._offline_alarms(system_id, device_id)
        alarms += self._status_alarms(time_range, device_id, system_id)
        if severity:
            alarms = [a for a in alarms if a['severity'] == severity]
        alarms.sort(key=lambda a: a.get('time') or '', reverse=True)
        return alarms

    def _offline_alarms(self, system_id, device_id):
        """Cảnh báo mức 'critical' cho mỗi thiết bị đang offline."""
        domain = [('active', '=', True), ('is_online', '=', False)]
        if system_id:
            domain.append(('system_id', '=', system_id))
        if device_id:
            domain.append(('id', '=', device_id))
        out = []
        for d in self.env['smartsolar.device'].search(domain):
            t = None
            if d.last_sync_date:
                t = d.last_sync_date.replace(
                    tzinfo=timezone.utc).astimezone(UTC7).isoformat()
            out.append({
                'type': 'device_offline',
                'severity': 'critical',
                'device_id': d.id,
                'device_name': d.name or d.device_guid,
                'message': 'Thiết bị offline',
                'time': t,
            })
        return out

    def _status_alarms(self, time_range: TimeRange, device_id, system_id):
        """Cảnh báo mức 'warning' khi thiết bị báo trạng thái khác bình thường.

        Quét cả 2 bảng dữ liệu (grid_tie_inverter, charge_power), gom theo
        (thiết bị, trạng thái) trong khoảng, bỏ qua các trạng thái bình thường.
        ``occurrences`` cho biết trạng thái đó lặp lại bao nhiêu lần.
        """
        out = []
        for model_name in ('grid.tie.inverter', 'charge.power'):
            table = self.env[model_name]._table
            params = [time_range.start_utc, time_range.end_utc]
            where = ["record_date >= %s", "record_date < %s",
                     "status IS NOT NULL", "status <> ''"]
            if device_id:
                where.append("device_id = %s")
                params.append(device_id)
            if system_id:
                where.append("system_id = %s")
                params.append(system_id)
            sql = """
                SELECT device_id, device_guid, status,
                       MAX(record_date) AS last_seen, COUNT(*) AS n
                  FROM {table}
                 WHERE {whr}
              GROUP BY device_id, device_guid, status
            """.format(table=table, whr=' AND '.join(where))
            self.env.cr.execute(sql, params)
            for dev_id, guid, status, last_seen, n in self.env.cr.fetchall():
                if str(status).strip().lower() in _NORMAL_STATUS:
                    continue
                t = last_seen.replace(
                    tzinfo=timezone.utc).astimezone(UTC7).isoformat() if last_seen else None
                out.append({
                    'type': 'device_status',
                    'severity': 'warning',
                    'device_id': dev_id,
                    'device_name': guid,
                    'message': 'Trạng thái: %s' % status,
                    'occurrences': int(n),
                    'time': t,
                })
        return out
