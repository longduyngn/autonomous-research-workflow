import argparse
import base64
import io
import json
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.orchestrator import HybridOrchestrator
from core.quota_cli import format_status, install_statusline, plan_statusline, route, run, status, statusline_command
from core.scheduler import TaskScheduler
from scripts import claude_statusline_quota as statusline

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = json.loads((ROOT / 'config/workflow_config.example.json').read_text(encoding='utf-8'))


def rollout(path, used=40.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'timestamp': datetime.now(timezone.utc).isoformat(), 'payload': {'type': 'token_count', 'rate_limits': {
        'limit_id': 'codex', 'primary': {'used_percent': used, 'window_minutes': 300, 'resets_at': time.time() + 3600},
        'secondary': {'used_percent': 12.0, 'window_minutes': 10080, 'resets_at': time.time() + 90000}}}}) + '\n', encoding='utf-8')


class StatusLineInstallerTests(unittest.TestCase):
    def test_fresh_install_and_idempotence(self):
        settings = {'theme': 'dark', 'env': {'KEEP': '1'}}
        new, info = plan_statusline(settings, ROOT, 'C:\\py\\python.exe')
        self.assertEqual(info['action'], 'install')
        self.assertEqual({k: v for k, v in new.items() if k != 'statusLine'}, settings)
        self.assertIn('C:/py/python.exe', new['statusLine']['command'])
        self.assertNotIn('\\', new['statusLine']['command'])
        self.assertEqual(plan_statusline(new, ROOT, 'C:\\py\\python.exe')[1]['action'], 'none')
        self.assertNotIn('statusLine', settings)

    def test_existing_statusline_is_chained_and_restored(self):
        settings = {'statusLine': {'type': 'command', 'command': 'my-line --fancy "x y"', 'padding': 2}}
        new, info = plan_statusline(settings, ROOT, sys.executable)
        self.assertTrue(info['chains_existing'])
        self.assertEqual(new['statusLine']['padding'], 2)
        import shlex
        self.assertEqual(base64.b64decode(shlex.split(new['statusLine']['command'])[-1]).decode(), 'my-line --fancy "x y"')
        restored, removal = plan_statusline(new, ROOT, sys.executable, remove=True)
        self.assertEqual((removal['restores_previous'], restored['statusLine']), (True, settings['statusLine']))
        plain, _ = plan_statusline({}, ROOT, sys.executable)
        self.assertNotIn('statusLine', plan_statusline(plain, ROOT, sys.executable, remove=True)[0])
        self.assertEqual(plan_statusline(settings, ROOT, remove=True)[1]['action'], 'none')

    def test_non_command_statusline_is_not_replaced(self):
        settings = {'statusLine': {'type': 'static', 'text': 'hello'}}
        new, info = plan_statusline(settings, ROOT, sys.executable)
        self.assertEqual((new, info['action']), (settings, 'none'))

    def test_check_never_writes_and_apply_backs_up_and_touches_only_statusline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'settings.json'
            original = {'permissions': {'allow': ['Read']}, 'env': {'A': 'b'}, 'statusLine': {'type': 'command', 'command': 'old'}}
            path.write_text(json.dumps(original), encoding='utf-8')
            before = path.read_text(encoding='utf-8')
            info = install_statusline(path, ROOT, python=sys.executable)
            self.assertEqual((info['action'], info['applied']), ('install', False))
            self.assertEqual(path.read_text(encoding='utf-8'), before)
            info = install_statusline(path, ROOT, apply=True, python=sys.executable, clock=lambda: 5)
            self.assertTrue(info['applied'])
            self.assertEqual(Path(info['backup']).read_text(encoding='utf-8'), before)
            after = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual({k: v for k, v in after.items() if k != 'statusLine'}, {k: v for k, v in original.items() if k != 'statusLine'})
            self.assertIn('claude_statusline_quota.py', after['statusLine']['command'])
            self.assertEqual(install_statusline(path, ROOT, apply=True, python=sys.executable)['action'], 'none')
            install_statusline(path, ROOT, apply=True, remove=True)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), original)
            missing = install_statusline(Path(tmp) / 'none.json', ROOT, python=sys.executable)
            self.assertEqual((missing['action'], missing['applied']), ('install', False))
            self.assertFalse((Path(tmp) / 'none.json').exists())


class StatusLineScriptTests(unittest.TestCase):
    PAYLOAD = {'model': {'id': 'x'}, 'transcript_path': 'C:/secret/place.jsonl', 'rate_limits': {
        'five_hour': {'used_percentage': 41.0, 'resets_at': 1794081000}, 'seven_day': {'used_percentage': 9.5, 'resets_at': 1794081600}}}

    def run_script(self, argv, payload):
        out = io.StringIO()
        with patch('sys.stdin', io.StringIO(payload if isinstance(payload, str) else json.dumps(payload))), redirect_stdout(out):
            code = statusline.main(argv)
        return code, out.getvalue()

    def test_only_rate_limits_are_stored_and_a_short_line_is_printed(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self.run_script(['--directory', tmp], self.PAYLOAD)
            saved = json.loads((Path(tmp) / 'claude_statusline.json').read_text(encoding='utf-8'))
            self.assertEqual((code, out), (0, 'claude 5h 41% 7d 10%'))
            self.assertEqual(set(saved), {'captured_at', 'rate_limits'})
            self.assertNotIn('secret', json.dumps(saved))

    def test_missing_limits_and_bad_input_never_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_script(['--directory', tmp], {'model': 'x'}), (0, 'claude'))
            self.assertEqual(self.run_script(['--directory', tmp], 'not json'), (0, 'claude'))
            self.assertFalse((Path(tmp) / 'claude_statusline.json').exists())

    def test_chained_statusline_receives_the_same_input_and_keeps_rendering(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = f'"{sys.executable}" -c "import sys,json; print(\'prev:\'+str(len(json.load(sys.stdin))))"'
            code, out = self.run_script(['--directory', tmp, '--chain-base64', base64.b64encode(command.encode()).decode()], self.PAYLOAD)
            self.assertEqual((code, out.strip()), (0, 'prev:3'))
            self.assertTrue((Path(tmp) / 'claude_statusline.json').exists())
            broken = base64.b64encode(b'definitely-not-a-command-xyz').decode()
            self.assertEqual(self.run_script(['--directory', tmp, '--chain-base64', broken], self.PAYLOAD)[0], 0)


class QuotaCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        readiness = patch('core.quota.AgyProbe.probe', return_value=[])
        readiness.start()
        self.addCleanup(readiness.stop)
        self.db = WorkspaceDatabase(self.root / 'db')
        self.config = {k: EXAMPLE[k] for k in ('providers', 'roles')}  # personal config without a quota block
        rollout(self.root / 'sessions/2026/11/09/rollout-x.jsonl', used=95.0)
        (self.root / 'config').mkdir()
        (self.root / 'config/workflow_config.example.json').write_text(json.dumps(EXAMPLE), encoding='utf-8')
        EXAMPLE_ACCOUNTS = json.loads(json.dumps(EXAMPLE['quota_routing']))
        EXAMPLE_ACCOUNTS['accounts']['codex']['sessions_dir'] = str(self.root / 'sessions')
        EXAMPLE_ACCOUNTS['accounts']['codex']['probe'] = 'codex_rollout'
        # These are offline CLI tests; never inherit an active Haiku probe from
        # the shipping config. Missing status-line fixture means unknown usage.
        EXAMPLE_ACCOUNTS['accounts']['claude']['probe'] = 'claude_statusline'
        self.example = {**EXAMPLE, 'quota_routing': EXAMPLE_ACCOUNTS}
        (self.root / 'config/workflow_config.example.json').write_text(json.dumps(self.example), encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def test_status_reads_codex_fixture_without_a_quota_block_and_formats(self):
        report = status(self.config, self.root, self.db)
        self.assertFalse(report['routing_enabled'])
        self.assertIn('example defaults', report['config_source'])
        codex = report['accounts']['codex']
        self.assertEqual((codex['state'], codex['confidence']), ('reserved', 'measured'))
        self.assertEqual(report['accounts']['agy:gemini-4-argon']['remaining'], 100.0)
        text = format_status(report)
        self.assertIn('disabled (opt-in)', text)
        self.assertRegex(text, r'codex\s+reserved')

    def test_route_is_what_if_when_disabled_and_respects_task_override(self):
        result = route(self.config, self.root, self.db, 'operator')
        self.assertFalse(result['routing_enabled'])
        self.assertIn('what-if', result['note'])
        self.assertEqual(result['decision']['provider'], 'claude_cli')  # codex 95% used is reserved
        self.assertEqual(route(self.config, self.root, self.db, 'ghost')['decision'], None)
        status(self.config, self.root, self.db)
        task = self.db.create_task('x')
        self.db.update_task(task.id, provider_override='codex_cli')
        pinned = route(self.config, self.root, self.db, 'operator', task.id)['decision']
        self.assertEqual((pinned['source'], pinned['provider'], pinned['reserved_use']), ('pinned', 'codex_cli', True))
        with self.assertRaises(ValueError):
            route(self.config, self.root, self.db, 'operator', 999)

    def test_run_dispatch_prints_json(self):
        out = io.StringIO()
        with redirect_stdout(out):
            run(argparse.Namespace(action='status', json=True), self.config, self.root, self.db)
        self.assertEqual(json.loads(out.getvalue())['accounts']['codex']['state'], 'reserved')
        out = io.StringIO()
        with redirect_stdout(out):
            run(argparse.Namespace(action='route', role='planner', task=None, requires=['codex'], json=True), self.config, self.root, self.db)
        decision = json.loads(out.getvalue())['decision']
        self.assertEqual((decision['source'], decision['account'], decision['reserved_use']), ('requires', 'codex', True))


class PollerTests(unittest.TestCase):
    def test_scheduler_refreshes_quota_on_its_own_interval_only_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for enabled in (True, False):
                config = {'logs_dir': 'logs', 'isolation': {'enabled': False}, 'providers': EXAMPLE['providers'], 'roles': EXAMPLE['roles'],
                          'quota_routing': {**EXAMPLE['quota_routing'], 'enabled': enabled, 'poll_seconds': 3600}}
                orch = HybridOrchestrator(root, config, WorkspaceDatabase(root / f'db{enabled}'), ContextLogManager(root / 'logs'), MagicMock())
                self.assertEqual(orch.quota_monitor is not None, enabled)
                if enabled:
                    orch.quota_monitor.refresh = MagicMock(return_value={})
                scheduler = TaskScheduler(orch)
                try:
                    scheduler.tick()
                    scheduler.tick()
                    if scheduler.quota_future:
                        scheduler.quota_future.result(timeout=5)
                finally:
                    scheduler.stop()
                if enabled:
                    orch.quota_monitor.refresh.assert_called_once()
                else:
                    self.assertIsNone(scheduler.quota_future)


if __name__ == '__main__':
    unittest.main()
