"""Energy-only refresh delegates to the authoritative widget reader."""
from datetime import datetime
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from odoo import fields
from ..models.dashboard_readers import DashboardReaders


class TestEnergySnapshot(TestCase):
    def test_snapshot_uses_fixed_cutoff_ledger_and_existing_widget_calculation(self):
        for system_id in (None, 12):
            for cutoff in (datetime(2026, 10, 29, 6), datetime(2026, 10, 31, 17, 0, 1)):
                with self.subTest(system=system_id, cutoff=cutoff):
                    reader = MagicMock()
                    widgets = {'metrics': [{'today': None, 'month': 0}], 'saved': None}
                    reader.get_energy_widgets.return_value = widgets
                    dashboard = SimpleNamespace(with_context=MagicMock(return_value=reader))
                    with patch.object(fields.Datetime, 'now', return_value=cutoff):
                        result = DashboardReaders.get_energy_widgets_snapshot(dashboard, system_id)
                    dashboard.with_context.assert_called_once_with(
                        smartsolar_dashboard_read_ledger=True, smartsolar_dashboard_end_at=cutoff)
                    reader.get_energy_widgets.assert_called_once_with(
                        system_id, daily_cache={}, end_at=cutoff)
                    self.assertIs(result['energy_widgets'], widgets)
                    self.assertEqual(result['cutoff'], fields.Datetime.to_string(cutoff))
                    self.assertEqual(reader.method_calls, [
                        ('get_energy_widgets', (system_id,), {'daily_cache': {}, 'end_at': cutoff})])
