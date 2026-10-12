"""The migration gate must not approve incomplete or failed device validation."""
from pathlib import Path
import unittest
from unittest.mock import patch

import check_windows_slint_gate as gate


class GateTests(unittest.TestCase):
    def assess(self, **arguments):
        with patch.object(Path, 'read_bytes', return_value=b'verified fixture'), \
             patch.object(gate, 'validate_release_pe'), \
             patch.object(gate, 'imports', return_value={'kernel32.dll'}), \
             patch.object(gate, 'verify_installer', return_value={'size': arguments.pop('size', 30_000_000)}):
            return gate.assess(Path('probe.exe'), Path('fixture-setup.exe'), **arguments)

    def test_build_success_alone_does_not_authorize_migration(self):
        result = self.assess()
        self.assertEqual(result['status'], 'pending')
        self.assertFalse(result['migrationAllowed'])

    def test_size_failure_cannot_be_overridden_by_ui_results(self):
        ui = {name: {'status': 'passed', 'evidence': 'manual fixture'} for name in gate.UI_CHECKS}
        result = self.assess(size=40_000_001, ui=ui)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['migrationAllowed'])

    def test_hidden_start_does_not_count_as_memory_after_panel_close(self):
        report = {'sampling': 'Started directly in tray', 'meanWorkingSetBytes': 20_000_000}
        result = self.assess(closed_baseline=report, closed_candidate=report)
        self.assertEqual(result['checks']['memoryAfterPanelClose']['status'], 'failed')

    def test_complete_measured_validation_can_pass(self):
        start = {'sampling': 'Started directly in tray', 'meanWorkingSetBytes': 20_000_000}
        closed = {'sampling': 'Panel opened then closed to tray; no service', 'meanWorkingSetBytes': 50_000_000}
        ui = {name: {'status': 'passed', 'evidence': 'manual fixture'} for name in gate.UI_CHECKS}
        result = self.assess(baseline=start, candidate=start, closed_baseline=closed, closed_candidate=closed, ui=ui)
        self.assertTrue(result['migrationAllowed'])


if __name__ == '__main__':
    unittest.main()
