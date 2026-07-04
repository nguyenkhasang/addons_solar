# -*- coding: utf-8 -*-
"""DeviceRepository — trạng thái thiết bị, online/offline, thời lượng offline."""
from __future__ import annotations

from datetime import timezone

from odoo import fields

from .base_repository import BaseRepository
from ..domain.value_objects import UTC7


class DeviceRepository(BaseRepository):

    def fetch_devices(self, system_id=None):
        """Trả về danh sách dict trạng thái các thiết bị đang kích hoạt.

        Với mỗi thiết bị offline, tính luôn ``offline_minutes`` = số phút kể từ lần
        đồng bộ cuối (now - last_sync_date). Thời gian đồng bộ cuối được đổi sang
        giờ UTC+7 cho dễ đọc. Thiết bị online thì offline_minutes = 0.
        """
        domain = [('active', '=', True)] + self._system_domain(system_id)
        devices = self.env['smartsolar.device'].search(domain)
        now = fields.Datetime.now()
        out = []
        for d in devices:
            offline_minutes = None
            last_sync_local = None
            if d.last_sync_date:
                delta = now - d.last_sync_date
                offline_minutes = round(delta.total_seconds() / 60.0, 1)
                last_sync_local = d.last_sync_date.replace(
                    tzinfo=timezone.utc).astimezone(UTC7).isoformat()
            out.append({
                'id': d.id,
                'name': d.name or d.device_guid,
                'device_guid': d.device_guid,
                'type': d.device_type,
                'online': bool(d.is_online),
                'firmware': d.firmware_version or '',
                'last_sync': last_sync_local,
                'offline_minutes': offline_minutes if not d.is_online else 0.0,
                'last_sync_status': d.last_sync_status or '',
            })
        return out
