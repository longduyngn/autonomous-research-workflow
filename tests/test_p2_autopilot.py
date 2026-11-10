import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('p2_autopilot', ROOT / 'scripts' / 'p2_autopilot.py')
autopilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(autopilot)


class FakeStore:
    def __init__(self):
        self.snapshots_put = []

    def put(self, snapshots):
        self.snapshots_put.extend(snapshots)


class FakeRouter:
    """Returns routes in order, skipping accounts already cooled down."""

    def __init__(self, routes, cooled):
        self.routes, self.cooled = routes, cooled

    def select(self, role):
        if role != 'planner':
            return None
        for provider, model, account in self.routes:
            if 'quota:' + account not in self.cooled and provider not in self.cooled:
                return SimpleNamespace(action='run', provider=provider, model=model, account=account)
        return SimpleNamespace(action='wait', until=time.time() + 60, reason='none left')


class AutopilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [patch.object(autopilot, 'STATE', self.tmp / 'state'),
                        patch.object(autopilot, 'HEARTBEAT', self.tmp / 'heartbeat'),
                        patch.object(autopilot, 'PLAN_DIR', self.tmp / 'plan')]
        for item in self.patches:
            item.start()
        self.settings = dict(autopilot.DEFAULTS)

    def tearDown(self):
        for item in self.patches:
            item.stop()

    def test_fresh_heartbeat_blocks_tick(self):
        autopilot.touch_heartbeat('working')
        self.assertTrue(autopilot.gate_should_skip(self.settings).startswith('coordinator_active'))

    def test_stale_heartbeat_done_marker_and_live_lock(self):
        autopilot.touch_heartbeat()
        old = time.time() - 10_000
        os.utime(autopilot.HEARTBEAT, (old, old))
        self.assertIsNone(autopilot.gate_should_skip(self.settings))
        autopilot.write_json(autopilot.STATE / 'lock.json', {'pid': os.getpid()})
        self.assertEqual(autopilot.gate_should_skip(self.settings), 'autopilot_running')
        autopilot.write_json(autopilot.STATE / 'lock.json', {'pid': 999999})
        self.assertIsNone(autopilot.gate_should_skip(self.settings))  # stale lock cleared
        (autopilot.STATE / 'DONE').write_text('x')
        self.assertEqual(autopilot.gate_should_skip(self.settings), 'p2_done')

    def _tick(self, outcomes, gate_ok=True):
        store = FakeStore()
        config = {'providers': {'claude_cli': {'type': 'claude_cli'}, 'codex_cli': {'type': 'codex_cli'}},
                  'quota_routing': {'default_cooldown_seconds': 900}}
        routes = [('claude_cli', 'opus', 'claude'), ('codex_cli', 'gpt-6-astra', 'codex')]

        def fake_build():
            return config, store, FakeRouter(routes, autopilot.local_cooldowns())

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd[0])
            code, err = outcomes.pop(0)
            return SimpleNamespace(returncode=code, stdout='summary line', stderr=err)

        with patch.object(autopilot, 'build_router', fake_build), \
                patch.object(autopilot.subprocess, 'run', fake_run), \
                patch.object(autopilot, 'run_gate', return_value=(gate_ok, [{'exit_code': 0 if gate_ok else 1}])), \
                patch.object(autopilot, 'commit', return_value='abc1234'):
            result = autopilot.tick(self.settings)
        return result, calls, store

    def test_quota_error_switches_to_next_route_in_same_tick(self):
        result, calls, store = self._tick([(1, "You've hit your usage limit. Try again in 2h"), (0, '')])
        self.assertEqual(result, 'committed')
        self.assertEqual(calls, ['claude', 'codex'])
        self.assertEqual(store.snapshots_put[0].account, 'claude')
        self.assertIn('quota:claude', autopilot.local_cooldowns())
        events = [json.loads(line)['event'] for line in (autopilot.STATE / 'ledger.jsonl').read_text().splitlines()]
        self.assertEqual(events, ['start', 'quota', 'start', 'committed'])
        self.assertFalse((autopilot.STATE / 'lock.json').exists())

    def test_gate_failure_is_not_committed_and_feeds_next_prompt(self):
        result, calls, _ = self._tick([(0, '')], gate_ok=False)
        self.assertEqual(result, 'gate_failed')
        self.assertTrue((autopilot.STATE / 'last_gate_failure.json').exists())
        run_dir = next((autopilot.STATE / 'runs').iterdir())
        prompt = autopilot.prompt_text(run_dir, autopilot.read_json(autopilot.STATE / 'last_gate_failure.json', {})['tail'])
        self.assertIn('did NOT pass gate G', prompt)

    def test_all_routes_exhausted_records_wait(self):
        limit = "You've hit your usage limit. Try again in 1h"
        result, calls, _ = self._tick([(1, limit), (1, limit)])
        self.assertEqual(result, 'waiting')
        self.assertGreater(autopilot.read_json(autopilot.STATE / 'wait.json', {})['until'], time.time())
        self.assertIn('waiting_for_quota', autopilot.gate_should_skip({**self.settings, 'heartbeat_stale_seconds': -1}))

    def test_prompt_forbids_commits_and_secrets(self):
        run_dir = self.tmp / 'run'
        run_dir.mkdir()
        text = autopilot.prompt_text(run_dir, '')
        self.assertIn('DO NOT run git commit', text)
        self.assertIn('Never read or print credentials', text)


if __name__ == '__main__':
    unittest.main()
