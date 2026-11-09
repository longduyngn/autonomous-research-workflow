import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core.quota import CodexAppServerProbe, LimitErrorDetector, parse_codex_rate_limits


class CodexLiveProbeTests(unittest.TestCase):
    def test_rpc_usage_cache_throttle_and_failed_refresh_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = Mock(return_value=1800000000)
            fallback = Mock()
            fallback.probe.return_value = []
            probe = CodexAppServerProbe(Path(tmp) / 'cache.json', clock=clock, fallback=fallback)
            response = {'rateLimits': {'limitId': 'codex', 'primary': {
                'usedPercent': 12, 'windowDurationMins': 300, 'resetsAt': 1800000500}},
                'private_banner': 'MUST NOT CACHE'}
            with patch('core.codex_rpc.read_app_server', return_value=response) as rpc:
                first = probe.probe()
                self.assertEqual((first[0].used_percent, first[0].source), (12, 'codex_app_server'))
                self.assertEqual(probe.probe(), first)
                rpc.assert_called_once()
            self.assertNotIn('MUST NOT CACHE', probe.cache.read_text())
            clock.return_value += 121
            with patch('core.codex_rpc.read_app_server', side_effect=TimeoutError) as rpc:
                self.assertEqual(probe.probe(), first)
                self.assertEqual(probe.probe(), first)
                rpc.assert_called_once()
            self.assertEqual(json.loads(probe.cache.read_text())['captured_at'], 1800000000)
            fallback.probe.return_value = [parse_codex_rate_limits({'primary': {
                'used_percent': 20, 'resets_at': 1800000500}}, clock())[0]]
            self.assertEqual(probe.probe()[0].used_percent, 20)

    def test_first_failure_is_throttled_even_without_cached_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            fallback = Mock()
            fallback.probe.return_value = []
            probe = CodexAppServerProbe(Path(tmp) / 'cache.json', fallback=fallback)
            with patch('core.codex_rpc.read_app_server', side_effect=RuntimeError) as rpc:
                self.assertEqual(probe.probe(), [])
                self.assertEqual(probe.probe(), [])
                self.assertEqual(rpc.call_count, 1)
                probe.probe(force=True)
                self.assertEqual(rpc.call_count, 2)

    def test_rpc_rejects_mutating_methods_before_spawning(self):
        from core.codex_rpc import read_app_server
        with patch('core.codex_rpc.subprocess.Popen') as spawn:
            with self.assertRaises(ValueError):
                read_app_server('turn/start')
            spawn.assert_not_called()

    def test_schema_reached_types_do_not_guess_the_five_hour_window(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/codex_limit_schema.json').read_text())
        for reached in fixture['reached_types']:
            with self.subTest(reached=reached):
                snapshots = parse_codex_rate_limits({'rateLimitReachedType': reached,
                    'primary': {'usedPercent': 5, 'windowDurationMins': 300},
                    'secondary': {'usedPercent': 6, 'windowDurationMins': 10080}}, 1800000000)
                self.assertEqual([s.used_percent for s in snapshots], [5, 6, 100])
                self.assertEqual(snapshots[-1].window, 'limit')
                self.assertIsNone(snapshots[-1].resets_at)

    def test_synthetic_codex_error_envelopes_and_reset_hints(self):
        text = json.dumps({'type': 'turn.failed', 'error': {'code': 'usage_limit_reached',
                           'message': 'Usage limit reached', 'resets_at': 1800000900}})
        self.assertEqual(LimitErrorDetector.detect(text, 1800000000)['resets_at'], 1800000900)
        iso = "You've hit your usage limit. Try again at 2026-11-10T02:00:00Z."
        self.assertEqual(LimitErrorDetector.detect(iso, 1800000000)['resets_at'], 1794276000)

    def test_rpc_handshake_ignores_notifications_and_returns_matching_response(self):
        from core.codex_rpc import read_app_server
        writes = []
        proc = Mock()
        proc.stdin.write.side_effect = writes.append
        proc.stdout = io.StringIO('\n'.join(map(json.dumps, [
            {'method': 'ignored', 'params': {'private': 'not retained'}},
            {'id': 1, 'result': {}}, {'id': 2, 'result': {'rateLimits': {}}}])))
        proc.poll.return_value = 0
        with patch('core.codex_rpc.find_executable', return_value='codex.exe'), \
             patch('core.codex_rpc.subprocess.Popen', return_value=proc):
            self.assertEqual(read_app_server('account/rateLimits/read'), {'rateLimits': {}})
        sent = [json.loads(line) for line in writes]
        self.assertEqual([r['method'] for r in sent], ['initialize', 'initialized', 'account/rateLimits/read'])
        proc.wait.assert_called_once()

    def test_rpc_error_does_not_expose_server_details(self):
        from core.codex_rpc import read_app_server
        proc = Mock()
        proc.stdout = io.StringIO(json.dumps({'id': 1, 'error': {'message': 'private-auth-detail'}}))
        proc.poll.return_value = 0
        with patch('core.codex_rpc.find_executable', return_value='codex.exe'), \
             patch('core.codex_rpc.subprocess.Popen', return_value=proc):
            with self.assertRaisesRegex(RuntimeError, '^Codex app-server rejected read-only query$'):
                read_app_server('model/list')
