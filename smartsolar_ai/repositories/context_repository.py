# -*- coding: utf-8 -*-
"""Read explicitly allowed system metadata; never expose credentials."""
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
