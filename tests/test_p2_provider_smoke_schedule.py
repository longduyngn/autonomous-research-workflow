"""P2.4-04: CLI version pins, drift detection and the once-per-day smoke throttle."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from core.provider_versions import SmokeSchedule, drift_report, installed_version, load_pins


class VersionTests(unittest.TestCase):
    def test_pins_cover_every_cli_with_exact_versions(self):
        pins = load_pins()
        self.assertEqual(set(pins), {'opencode_cli', 'codex_cli', 'claude_cli', 'agy_cli'})
        for spec in pins.values():
            self.assertRegex(spec['version'], r'^\d+\.\d+\.\d+$')
            self.assertTrue(spec['executable'])

    def test_version_parsing_matches_real_outputs(self):
        pins = load_pins()
        outputs = {'codex_cli': 'codex-cli 0.167.0\n', 'claude_cli': '2.1.319 (Claude Code)\n',
                   'agy_cli': '1.5.5\n', 'opencode_cli': '1.19.5\n'}
        with patch('core.provider_versions.find_executable', return_value='cli.exe'):
            for name, text in outputs.items():
                version = installed_version(pins[name]['executable'], pins[name]['version_pattern'], run=lambda argv: text)
                self.assertEqual(version, pins[name]['version'], name)

    def test_drift_and_missing_are_reported(self):
        pins = {'codex_cli': {'executable': 'codex', 'version': '0.167.0', 'version_pattern': r'codex-cli\s+(\S+)'},
                'agy_cli': {'executable': 'agy', 'version': '1.5.5', 'version_pattern': r'^(\S+)'}}
        with patch('core.provider_versions.find_executable', side_effect=lambda name: None if name == 'agy' else 'x'):
            report = drift_report(pins, run=lambda argv: 'codex-cli 0.170.0')
        self.assertEqual(report['codex_cli']['status'], 'drift')
        self.assertEqual(report['agy_cli']['status'], 'missing')

    def test_doctor_includes_pins_without_model_calls(self):
        from core.diagnostics import inspect_environment
        import json
        root = Path(__file__).resolve().parents[1]
        config = json.loads((root / 'config/workflow_config.example.json').read_text(encoding='utf-8'))
        with patch('core.provider_versions.drift_report', return_value={'codex_cli': {'status': 'ok'}}) as drift, \
                patch('core.providers.run_process', side_effect=AssertionError('doctor must not run agents')):
            report = inspect_environment(root, config)
        drift.assert_called_once()
        self.assertEqual(report['cli_version_pins'], {'codex_cli': {'status': 'ok'}})


class ScheduleTests(unittest.TestCase):
    def test_once_per_day_and_no_catch_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = [time.mktime((2026, 11, 11, 9, 0, 0, 0, 0, -1))]
            schedule = SmokeSchedule(Path(tmp) / 's.json', clock=lambda: now[0])
            self.assertTrue(schedule.due('p'))
            schedule.record('p', True)
            now[0] += 3600
            self.assertFalse(schedule.due('p'))
            now[0] += 5 * 86400  # machine slept for days: exactly one run is due, not five
            self.assertTrue(schedule.due('p'))
            schedule.record('p', False)
            self.assertFalse(schedule.due('p'))
            self.assertTrue(schedule.due('other'))


if __name__ == '__main__':
    unittest.main()
