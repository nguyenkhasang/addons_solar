# -*- coding: utf-8 -*-
"""Mở rộng smartsolar.system: toạ độ địa lý + logic gọi Open-Meteo.

Dùng _inherit để "cắm thêm" tính năng môi trường vào model hệ thống có sẵn của
module smartsolar, mà KHÔNG sửa module gốc. Cài module này -> hệ thống có thêm
lat/long, nút lấy thời tiết, và cron 5 phút. Gỡ module -> smartsolar trở lại
nguyên trạng.
"""
from __future__ import annotations

import logging

from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class SmartSolarSystem(models.Model):
    _inherit = 'smartsolar.system'

    # --- Toa do dia ly de lay du lieu moi truong (Open-Meteo) ---
    latitude = fields.Float(
        string='Vi do (Latitude)', digits=(9, 6), default=21.1314,
        help='Vi do cua he thong, dung de goi API thoi tiet Open-Meteo.',
    )
    longitude = fields.Float(
        string='Kinh do (Longitude)', digits=(9, 6), default=105.7984,
        help='Kinh do cua he thong, dung de goi API thoi tiet Open-Meteo.',
    )
    environment_ids = fields.One2many('smartsolar.environment', 'system_id',
                                      string='Du lieu moi truong')

    # ------------------------------------------------------------------
    # Du lieu moi truong (Open-Meteo)
    # ------------------------------------------------------------------
    OPEN_METEO_URL = 'https://api.open-meteo.com/v1/forecast'

    # Thu tu bien phai giu nguyen giua request (params) va luc doc ket qua.
    _OM_CURRENT_VARS = [
        'temperature_2m', 'relative_humidity_2m', 'wind_speed_10m',
        'cloud_cover', 'surface_pressure', 'rain', 'precipitation',
    ]
    _OM_DAILY_VARS = [
        'sunrise', 'sunset', 'daylight_duration', 'sunshine_duration',
        'uv_index_max', 'uv_index_clear_sky_max', 'shortwave_radiation_sum',
        'wind_speed_10m_max', 'wind_gusts_10m_max', 'temperature_2m_max',
        'precipitation_probability_max', 'precipitation_hours',
        'precipitation_sum', 'weather_code',
    ]

    def _fetch_environment_data(self):
        """Goi Open-Meteo cho 1 he thong va tao 1 ban ghi smartsolar.environment.

        Dung requests thuan (JSON endpoint), khong can SDK/pandas. Tra ve record
        vua tao, hoac False neu loi/thieu toa do. Loi mang duoc nuot (log warning)
        de cron khong vo khi mot he thong tam thoi khong goi duoc API.
        """
        self.ensure_one()
        if not self.latitude and not self.longitude:
            _logger.warning('System %s: chua cau hinh latitude/longitude', self.name)
            return False

        try:
            import requests
        except ImportError:
            _logger.error('Thieu python dependency: requests')
            return False

        params = {
            'latitude': self.latitude,
            'longitude': self.longitude,
            'current': ','.join(self._OM_CURRENT_VARS),
            'daily': ','.join(self._OM_DAILY_VARS),
            'timezone': 'auto',
            'forecast_days': 1,
        }
        try:
            resp = requests.get(self.OPEN_METEO_URL, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            _logger.warning('System %s: loi goi Open-Meteo: %s', self.name, e)
            return False

        try:
            values = self._parse_open_meteo(data)
        except Exception as e:
            _logger.error('System %s: loi parse Open-Meteo: %s', self.name, e, exc_info=True)
            return False

        values['system_id'] = self.id
        return self.env['smartsolar.environment'].create(values)

    def _parse_open_meteo(self, data):
        """Chuyen JSON Open-Meteo thanh dict field cho smartsolar.environment.

        current/daily deu tra ve gia tri tuc thoi va mot mang daily (chi lay phan
        tu [0] = hom nay). Cac moc thoi gian ISO duoc chuyen sang datetime naive.
        """
        from datetime import datetime, date

        def _dt(value):
            if not value:
                return False
            try:
                return datetime.fromisoformat(str(value).replace('Z', '+00:00')).replace(tzinfo=None)
            except (ValueError, TypeError):
                return False

        def _d(value):
            if not value:
                return False
            try:
                return date.fromisoformat(str(value)[:10])
            except (ValueError, TypeError):
                return False

        def _first(seq):
            return seq[0] if isinstance(seq, list) and seq else False

        current = data.get('current') or {}
        daily = data.get('daily') or {}

        values = {
            'latitude': data.get('latitude', self.latitude),
            'longitude': data.get('longitude', self.longitude),
            'elevation': data.get('elevation', 0.0),
            # current
            'current_time': _dt(current.get('time')),
            'temperature_2m': current.get('temperature_2m', 0.0),
            'relative_humidity_2m': current.get('relative_humidity_2m', 0.0),
            'wind_speed_10m': current.get('wind_speed_10m', 0.0),
            'cloud_cover': current.get('cloud_cover', 0.0),
            'surface_pressure': current.get('surface_pressure', 0.0),
            'rain': current.get('rain', 0.0),
            'precipitation': current.get('precipitation', 0.0),
            # daily (hom nay)
            'daily_date': _d(_first(daily.get('time'))),
            'sunrise': _dt(_first(daily.get('sunrise'))),
            'sunset': _dt(_first(daily.get('sunset'))),
            'daylight_duration': _first(daily.get('daylight_duration')) or 0.0,
            'sunshine_duration': _first(daily.get('sunshine_duration')) or 0.0,
            'uv_index_max': _first(daily.get('uv_index_max')) or 0.0,
            'uv_index_clear_sky_max': _first(daily.get('uv_index_clear_sky_max')) or 0.0,
            'shortwave_radiation_sum': _first(daily.get('shortwave_radiation_sum')) or 0.0,
            'wind_speed_10m_max': _first(daily.get('wind_speed_10m_max')) or 0.0,
            'wind_gusts_10m_max': _first(daily.get('wind_gusts_10m_max')) or 0.0,
            'temperature_2m_max': _first(daily.get('temperature_2m_max')) or 0.0,
            'precipitation_probability_max': _first(daily.get('precipitation_probability_max')) or 0.0,
            'precipitation_hours': _first(daily.get('precipitation_hours')) or 0.0,
            'precipitation_sum': _first(daily.get('precipitation_sum')) or 0.0,
            'weather_code': int(_first(daily.get('weather_code')) or 0),
        }
        return values

    def action_fetch_environment(self):
        """Nut bam thu cong: lay du lieu moi truong ngay lap tuc."""
        self.ensure_one()
        record = self._fetch_environment_data()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Du lieu moi truong'),
                'message': _('Lay du lieu thoi tiet thanh cong') if record else _('Khong lay duoc du lieu thoi tiet'),
                'type': 'success' if record else 'warning',
                'sticky': False,
            }
        }

    @api.model
    def _cron_fetch_environment(self):
        """Cron 5 phut/lan: lay du lieu moi truong cho tat ca he thong active co toa do."""
        systems = self.search([('active', '=', True)])
        for system in systems:
            if not system.latitude and not system.longitude:
                continue
            try:
                system._fetch_environment_data()
            except Exception as e:
                _logger.error('Error fetching environment for system %s: %s',
                              system.name, e, exc_info=True)
        return True
