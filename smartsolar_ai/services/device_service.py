# -*- coding: utf-8 -*-
"""DeviceService — trạng thái thiết bị và cảnh báo.

Service mỏng, chỉ điều phối giữa 2 Repository (thiết bị + cảnh báo) và đóng gói
thành DTO. Tách khỏi AnalyticsService vì đây là mối quan tâm khác (tình trạng vận
hành, không phải phân tích số liệu đo).
"""
from __future__ import annotations

from ..domain.dto import AlarmResult, DeviceStatusResult
from ..domain.value_objects import TimeRange
from ..repositories.alarm_repository import AlarmRepository
from ..repositories.device_repository import DeviceRepository


class DeviceService:
    def __init__(self, env):
        self._device_repo = DeviceRepository(env)
        self._alarm_repo = AlarmRepository(env)

    def get_device_status(self, device_id=None, system_id=None) -> DeviceStatusResult:
        """Trạng thái hiện tại của các thiết bị (kèm thời lượng offline).

        Truyền ``device_id`` để lọc còn đúng 1 thiết bị; ``system_id`` để lọc theo
        hệ thống; không truyền gì -> tất cả thiết bị đang kích hoạt.
        """
        devices = self._device_repo.fetch_devices(system_id)
        if device_id:
            devices = [d for d in devices if d['id'] == device_id]
        return DeviceStatusResult(devices=devices)

    def get_alarms(self, time_range: TimeRange, severity=None,
                   device_id=None, system_id=None) -> AlarmResult:
        """Lịch sử cảnh báo/sự kiện trên một khoảng (có thể lọc theo mức độ)."""
        alarms = self._alarm_repo.fetch_alarms(
            time_range, severity=severity, device_id=device_id, system_id=system_id)
        return AlarmResult(
            range_local=[time_range.start_local_iso(), time_range.end_local_iso()],
            alarms=alarms,
        )
