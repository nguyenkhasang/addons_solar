# -*- coding: utf-8 -*-
"""Read explicitly allowed system metadata; never expose credentials."""
import math

from .base_repository import BaseRepository


class ContextRepository(BaseRepository):
    def fetch_systems(self, system_id=None, limit=20):
        domain = [('id', '=', system_id)] if system_id else []
        systems = self.env['smartsolar.system'].search(domain, order='id', limit=limit + 1)
        return {
            'systems': [{
                'id': system.id, 'name': system.name, 'code': system.code,
                'location': system.location or None,
                'timezone': system.timezone or 'Asia/Ho_Chi_Minh',
                'capacity_kw': system.capacity or None,
                'installation_date': str(system.installation_date) if system.installation_date else None,
                'state': system.state,
            } for system in systems[:limit]],
            'truncated': len(systems) > limit,
        }


    def fetch_reporting_settings(self):
        # Explicit public configuration allowlist; never return arbitrary parameters.
        Param = self.env['ir.config_parameter'].sudo()
        raw = Param.get_param('smartsolar.electricity_price')
        price = None
        try:
            candidate = float(raw) if raw not in (False, None, '') else float('nan')
            if math.isfinite(candidate) and candidate >= 0:
                price = candidate
        except (ValueError, TypeError):
            pass
        return {
            'electricity_price': {
                'value': price, 'available': price is not None,
                'currency_symbol': Param.get_param('smartsolar.currency_symbol', '₫'),
                'unit': 'currency/kWh', 'source': 'smartsolar.electricity_price',
                'is_estimate': True,
                'note': 'Đơn giá dashboard do quản trị cấu hình, không xác minh biểu giá điện lực/bậc thang/thuế. '
                        'Không dùng giá mặc định giao diện khi chưa lưu cấu hình. '
                        'Tiền mua điện dùng điện lưới; tiết kiệm cần điện solar thay thế lưới, không lấy PV nạp pin thay thế.',
            },
        }
