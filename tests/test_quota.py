import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from core.configuration import validate_config
from core.database import WorkspaceDatabase
from core.quota import (QuotaRouter, ClaudeStatusProbe, CodexRolloutProbe, LimitErrorDetector, QuotaMonitor, QuotaStore, Snapshot,
                        AgyProbe, account_for, account_state, parse_claude_event, parse_codex_rate_limits, usage_tokens)

NOW = 1_800_000_000.0


def rollout_line(primary, secondary, timestamp='2026-11-09T08:57:50.571Z', reached=None, limit_id='codex'):
    return json.dumps({'timestamp': timestamp, 'type': 'event_msg', 'payload': {'type': 'token_count', 'info': None, 'rate_limits': {
        'limit_id': limit_id, 'primary': {'used_percent': primary[0], 'window_minutes': 300, 'resets_at': primary[1]},
        'secondary': {'used_percent': secondary[0], 'window_minutes': 10080, 'resets_at': secondary[1]},
        'plan_type': 'plus', 'rate_limit_reached_type': reached}}})


class SnapshotTests(unittest.TestCase):
    def test_elapsed_window_has_reset_and_old_measurement_is_estimate(self):
        snap = Snapshot('codex', 'five_hour', 95.0, NOW - 10, 'x', NOW - 100)
        view = snap.effective(NOW, 900)
        self.assertEqual((view.used_percent, view.confidence, view.last_reset), (0.0, 'inferred', NOW - 10))
        live = Snapshot('codex', 'five_hour', 40.0, NOW + 3600, 'x', NOW - 2000).effective(NOW, 900)
        self.assertEqual((live.used_percent, live.confidence, live.last_reset), (40.0, 'estimated', NOW + 3600 - 18000))

    def test_account_state_reserve_exhausted_and_unlimited(self):
        weekly = Snapshot('codex', 'seven_day', 91.0, NOW + 5000, 'x', NOW)
        five = Snapshot('codex', 'five_hour', 10.0, NOW + 100, 'x', NOW)
        state = account_state([five, weekly], NOW)
        self.assertEqual((state['state'], state['until'], state['remaining']), ('reserved', NOW + 5000, 9.0))
        self.assertEqual(account_state([five], NOW)['state'], 'ok')
        both = account_state([Snapshot('c', 'five_hour', 100.0, NOW + 100, 'x', NOW), weekly], NOW)
        self.assertEqual((both['state'], both['until']), ('exhausted', NOW + 5000))
        self.assertEqual(account_state([Snapshot('a', 'five_hour', 100.0, NOW + 9, 'x', NOW)], NOW, unlimited=True)['state'], 'ok')
        self.assertEqual(account_state([], NOW)['remaining'], None)

    def test_account_mapping_and_usage(self):
        self.assertEqual(account_for({'type': 'codex_cli'}), 'codex')
        self.assertEqual(account_for({'type': 'claude_cli'}), 'claude')
        self.assertEqual(account_for({'type': 'remote_claude_operator'}), 'remote_claude')
        self.assertEqual(account_for({'type': 'agy_cli'}, 'gemini-4-argon-high'), 'agy:gemini-4-argon')
        self.assertEqual(account_for({'type': 'agy_cli'}, 'Gemini-3.8-Flash-low'), 'agy:gemini-3.8-flash')
        self.assertEqual(account_for({'type': 'agy_cli'}, 'claude-opus-5-5-high'), 'agy:claude')
        self.assertEqual(account_for({'type': 'agy_cli'}, 'claude-sonnet-5-5-medium'), 'agy:claude')
        self.assertEqual(account_for({'type': 'agy_cli'}), 'agy:default')
        self.assertIsNone(account_for({'type': 'http_llm'}))
        self.assertEqual(account_for({'type': 'agy_cli', 'quota_account': 'mine'}), 'mine')
        self.assertEqual(usage_tokens({'input_tokens': 2, 'output_tokens': 51, 'cache_creation_input_tokens': 9046, 'cache_read_input_tokens': 7}), 9099)
        self.assertEqual(usage_tokens(None), 0)


class ProbeTests(unittest.TestCase):
    def test_codex_parse_windows_and_reached_type(self):
        event = json.loads(rollout_line((1.0, 1794232639), (70.0, 1794453208)))['payload']['rate_limits']
        five, week = parse_codex_rate_limits(event, NOW)
        self.assertEqual((five.window, five.used_percent, five.resets_at), ('five_hour', 1.0, 1794232639.0))
        self.assertEqual((week.window, week.used_percent), ('seven_day', 70.0))
        event['rate_limit_reached_type'] = 'secondary'
        self.assertEqual([s.used_percent for s in parse_codex_rate_limits(event, NOW)], [1.0, 100.0])
        event['limit_id'] = 'codex_other'
        self.assertEqual(parse_codex_rate_limits(event, NOW), [])

    def test_rollout_probe_uses_newest_mtime_across_date_dirs_and_last_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / '2026/11/09/rollout-a.jsonl'
            resumed = root / '2026/10/20/rollout-b.jsonl'
            for path in (old, resumed):
                path.parent.mkdir(parents=True)
            old.write_text(rollout_line((90.0, 1794000000), (10.0, 1794900000)) + '\n', encoding='utf-8')
            resumed.write_text('{"not":"json"\n' + rollout_line((3.0, 1794000100), (20.0, 1794900000)) + '\n'
                               + '{"type":"event_msg","payload":{"type":"agent_message","text":"private"}}\n'
                               + rollout_line((5.0, 1794000100), (21.0, 1794900000), '2026-11-09T09:00:00Z') + '\n', encoding='utf-8')
            os.utime(old, (1_000_000, 1_000_000))
            os.utime(resumed, (2_000_000, 2_000_000))
            snaps = CodexRolloutProbe(root).probe()
            self.assertEqual([s.used_percent for s in snaps], [5.0, 21.0])
            self.assertEqual(snaps[0].observed_at, 1794214800.0)
            self.assertEqual(CodexRolloutProbe(root / 'missing').probe(), [])

    def test_rollout_probe_skips_files_without_rate_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            empty, good = root / '2026/11/09/rollout-new.jsonl', root / '2026/11/08/rollout-old.jsonl'
            for path in (empty, good):
                path.parent.mkdir(parents=True)
            empty.write_text('{"type":"session_meta"}\n', encoding='utf-8')
            good.write_text(rollout_line((7.0, 1794000100), (8.0, 1794900000)) + '\n', encoding='utf-8')
            os.utime(good, (1_000_000, 1_000_000))
            self.assertEqual(CodexRolloutProbe(root).probe()[0].used_percent, 7.0)

    def test_claude_stream_event_and_statusline(self):
        event = {'type': 'rate_limit_event', 'rate_limit_info': {'status': 'allowed', 'unifiedWindows': {
            'five_hour': {'utilization': 0.05, 'resetsAt': 1794081000, 'status': 'allowed'},
            'seven_day': {'utilization': 0.04, 'resetsAt': 1794081600, 'status': 'rejected'}}}}
        five, week = parse_claude_event(event, NOW)
        self.assertEqual((five.account, five.used_percent, five.resets_at), ('claude', 5.0, 1794081000.0))
        self.assertEqual(week.used_percent, 100.0)
        self.assertEqual(parse_claude_event({'type': 'result'}, NOW), [])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 's.json'
            self.assertEqual(ClaudeStatusProbe(path).probe(), [])
            path.write_text(json.dumps({'captured_at': NOW, 'rate_limits': {'five_hour': {'used_percentage': 23.5, 'resets_at': 1794081000},
                            'seven_day': {'used_percentage': 'bad'}}}), encoding='utf-8')
            snaps = ClaudeStatusProbe(path).probe()
            self.assertEqual([(s.window, s.used_percent, s.observed_at) for s in snaps], [('five_hour', 23.5, NOW)])
            path.write_text('not json', encoding='utf-8')
            self.assertEqual(ClaudeStatusProbe(path).probe(), [])


class DetectorTests(unittest.TestCase):
    def test_limit_texts_and_reset_hints(self):
        detect = LimitErrorDetector.detect
        found = detect("You've hit your limit - resets 3pm (Asia/Saigon)", NOW)
        self.assertTrue(found['exhausted'])
        self.assertGreater(found['resets_at'], NOW)
        self.assertLessEqual(found['resets_at'] - NOW, 86400)
        self.assertEqual(detect('Claude AI usage limit reached|1794081000', NOW)['resets_at'], 1794081000.0)
        self.assertEqual(detect('429 RESOURCE_EXHAUSTED: quota exceeded. Please try again in 2 hours 5 minutes', NOW)['resets_at'], NOW + 7500)
        self.assertEqual(detect('Error: rate limit exceeded, retry-after: 90', NOW)['resets_at'], NOW + 90)
        self.assertEqual(detect('5-hour limit reached, resets at 2026-11-09T12:00:00Z', NOW)['resets_at'], 1794225600.0)
        self.assertEqual(detect('google.api_core.exceptions.ResourceExhausted: 429 Quota exceeded for quota metric. Please try again in 30 minutes', NOW)['resets_at'], NOW + 1800)
        self.assertEqual(detect('You have exhausted your model capacity on gemini-3.8-flash. Resets in 15m', NOW)['resets_at'], NOW + 900)
        self.assertEqual(detect('Request limit for gemini-4-argon reached. Please try again after 11:45 pm', NOW)['exhausted'], True)
        self.assertAlmostEqual(detect('Quota will replenish in 45s', NOW)['resets_at'], NOW + 45)
        self.assertIsNone(detect('usage limit hit', NOW)['resets_at'])
        for text in ('', None, 'All checks passed', 'Selected model is at capacity', 'HTTP 500 internal error'):
            self.assertIsNone(detect(text, NOW), text)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = QuotaStore(WorkspaceDatabase(Path(self.tmp.name) / 'db.sqlite'))

    def tearDown(self):
        self.tmp.cleanup()

    def test_put_keeps_newest_observation_per_window(self):
        self.store.put([Snapshot('codex', 'five_hour', 50.0, NOW + 1, 'a', NOW)])
        self.store.put([Snapshot('codex', 'five_hour', 10.0, NOW + 1, 'old', NOW - 5)])
        self.store.put([Snapshot('codex', 'seven_day', 20.0, NOW + 1, 'a', NOW)])
        got = {s.window: s for s in self.store.snapshots('codex')}
        self.assertEqual((got['five_hour'].used_percent, got['five_hour'].source), (50.0, 'a'))
        self.assertEqual(len(self.store.snapshots()), 2)

    def test_measured_headroom_clears_older_limit_error(self):
        self.store.put([Snapshot('claude', 'limit', 100.0, NOW + 9999, 'limit_error', NOW, 'inferred')])
        self.store.put([Snapshot('claude', 'five_hour', 100.0, NOW + 9999, 'claude_statusline', NOW + 5)])
        self.assertEqual(len(self.store.snapshots('claude')), 2)
        self.store.put([Snapshot('claude', 'five_hour', 20.0, NOW + 9999, 'claude_statusline', NOW + 10)])
        self.assertEqual([s.window for s in self.store.snapshots('claude')], ['five_hour'])

    def test_ledger_roundtrip_estimate_and_last_route(self):
        for tokens in (400, 100):
            self.store.record_run(role='operator', provider='claude_cli', model='sonnet', account='claude', task_id=7,
                                  tokens=tokens, duration=1.0, success=True, ts=NOW - 60)
        self.store.record_run(role='operator', provider='x', account='claude', task_id=7, tokens=9, ts=NOW - 100000)
        self.store.record_run(role='operator', provider='y', task_id=7, success=False, limit_error=True, resets_at=NOW + 5, ts=NOW)
        self.assertEqual(self.store.last_route(7, 'operator'), ('claude_cli', 'sonnet'))
        self.assertIsNone(self.store.last_route(8, 'operator'))
        estimate = self.store.ledger_estimate('claude', 1000, NOW)
        self.assertEqual((estimate.used_percent, estimate.confidence, estimate.window), (50.0, 'estimated', 'ledger'))
        self.assertIsNone(self.store.ledger_estimate('claude', None, NOW))
        self.assertEqual(len(self.store.ledger(limit=10)), 4)

    def test_monitor_refresh_isolates_probe_failures(self):
        class Good:
            def probe(self):
                return [Snapshot('codex', 'five_hour', 12.0, NOW + 100, 'fake', NOW)]

        class Bad:
            def probe(self):
                raise OSError('boom')
        monitor = QuotaMonitor({'quota_routing': {'accounts': {'agy:claude': {'budget_tokens_5h': 100}}}}, self.store, self.tmp.name,
                               clock=lambda: NOW, probes={'codex': Good(), 'claude': Bad()})
        self.store.record_run(role='r', provider='p', account='agy:claude', tokens=30, ts=NOW - 10)
        result = monitor.refresh()
        self.assertEqual(result['errors'], {'claude': 'OSError'})
        self.assertEqual({s.account: s.used_percent for s in self.store.snapshots()}, {'codex': 12.0, 'agy:claude': 30.0})


class ConfigTests(unittest.TestCase):
    def example(self):
        return json.loads((Path(__file__).resolve().parents[1] / 'config/workflow_config.example.json').read_text(encoding='utf-8'))

    def test_example_is_disabled_and_valid(self):
        config = validate_config(self.example())
        self.assertIs(config['quota_routing']['enabled'], False)
        self.assertEqual(config['quota_routing']['accounts']['agy:gemini-4-argon'], {'unlimited': True})
        self.assertEqual(validate_config({'quota_routing': {'enabled': False}})['quota_routing']['enabled'], False)

    def test_invalid_blocks_are_rejected(self):
        def mutate(change):
            config = self.example()
            change(config['quota_routing'])
            return config
        bad = {
            'reserve': lambda q: q.update(reserve_percent=100),
            'poll': lambda q: q.update(poll_seconds=1),
            'bool': lambda q: q.update(enabled='yes'),
            'provider': lambda q: q['classes']['worker'].append({'provider': 'nope', 'model': ''}),
            'model': lambda q: q['classes']['worker'].append({'provider': 'codex_cli', 'model': 3}),
            'role': lambda q: q['role_classes'].update(ghost='worker'),
            'class': lambda q: q['role_classes'].update(operator='ghost'),
            'reserve class': lambda q: q['reserve'].update(ghost=[]),
            'requires': lambda q: q['requires'].update(operator='claude'),
            'budget': lambda q: q['accounts'].update(codex={'budget_tokens_5h': 0}),
            'workaround': lambda q: q['workarounds'].update(claude=[{'provider': 'nope'}]),
            'enabled without roles': lambda q: q.update(enabled=True, role_classes={}),
        }
        for name, change in bad.items():
            with self.subTest(name), self.assertRaises(ValueError):
                validate_config(mutate(change))
        with self.assertRaises(ValueError):
            validate_config({'quota_routing': []})


class RouterTests(unittest.TestCase):
    HOUR = 3600

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = QuotaStore(WorkspaceDatabase(Path(self.tmp.name) / 'db.sqlite'))
        self.config = json.loads((Path(__file__).resolve().parents[1] / 'config/workflow_config.example.json').read_text(encoding='utf-8'))
        self.config['quota_routing']['enabled'] = True
        self.cooldowns = {}

    def tearDown(self):
        self.tmp.cleanup()

    def router(self):
        return QuotaRouter(self.config, self.store, lambda: NOW, lambda key: self.cooldowns.get(key, 0))

    def five(self, account, used, started_ago):
        """Record a 5h window that began `started_ago` seconds before NOW."""
        resets = NOW - started_ago + 18000
        self.store.put([Snapshot(account, 'five_hour', used, resets, 'fixture', NOW - 30)])
        return resets

    def limit(self, account, resets_in):
        self.store.put([Snapshot(account, 'limit', 100.0, NOW + resets_in, 'limit_error', NOW - 5, 'inferred')])

    def test_prefers_most_recently_reset_window(self):
        self.five('claude', 30.0, started_ago=1 * self.HOUR)
        self.five('codex', 5.0, started_ago=4 * self.HOUR)
        decision = self.router().select('operator')
        self.assertEqual((decision.action, decision.provider, decision.model, decision.source), ('run', 'claude_cli', 'sonnet', 'class'))
        self.five('codex', 5.0, started_ago=0.5 * self.HOUR)
        self.assertEqual(self.router().select('operator').provider, 'codex_cli')

    def test_unknown_usage_is_ranked_after_measured_then_config_order(self):
        # Ranking semantics of an unordered class (the planner itself uses the ordered D3 chain).
        self.config['quota_routing']['role_classes']['planner'] = 'frontier'
        self.assertEqual(self.router().select('planner').provider, 'claude_cli')
        self.five('codex', 20.0, started_ago=2 * self.HOUR)
        self.assertEqual(self.router().select('planner').provider, 'codex_cli')

    def test_last_ten_percent_is_reserved_not_spent(self):
        self.five('claude', 90.0, started_ago=self.HOUR)
        self.five('codex', 89.0, started_ago=3 * self.HOUR)
        decision = self.router().select('operator')
        self.assertEqual((decision.provider, decision.reserved_use), ('codex_cli', False))
        self.assertEqual({c['account']: c['state'] for c in decision.considered}['claude'], 'reserved')

    def test_all_reserved_falls_to_agy_hosted_claude_then_argon(self):
        self.five('claude', 95.0, 1 * self.HOUR)
        self.five('codex', 99.0, 1 * self.HOUR)
        self.assertEqual(self.router().select('operator').account, 'agy:claude')
        self.five('agy:claude', 100.0, 1 * self.HOUR)
        decision = self.router().select('operator')
        self.assertEqual((decision.source, decision.provider, decision.model), ('reserve', 'agy_headless_auto', 'gemini-4-argon-medium'))
        self.assertEqual(self.router().select('planner').model, 'gemini-4-argon-high')

    def test_requires_may_spend_reserve_but_not_exhausted(self):
        self.five('claude', 95.0, self.HOUR)
        decision = self.router().select('operator', requires=['claude'])
        self.assertEqual((decision.source, decision.account, decision.reserved_use), ('requires', 'claude', True))
        resets = self.five('claude', 100.0, self.HOUR)
        decision = self.router().select('operator', requires=['claude'])
        self.assertEqual((decision.action, decision.account, decision.until), ('wait', 'claude', resets))
        self.assertNotEqual(decision.source, 'reserve')

    def test_configured_workaround_used_when_account_unavailable(self):
        self.config['quota_routing']['classes']['worker'] = [{'provider': 'claude_cli', 'model': 'sonnet'}]
        self.config['quota_routing']['workarounds'] = {'claude': [{'provider': 'agy_headless_auto', 'model': 'claude-sonnet-5-5-low'}]}
        self.five('claude', 100.0, self.HOUR)
        decision = self.router().select('operator')
        self.assertEqual((decision.source, decision.model, decision.account), ('workaround', 'claude-sonnet-5-5-low', 'agy:claude'))
        self.assertIn('claude', decision.reason)

    def test_waits_until_earliest_reset_when_everything_is_exhausted(self):
        self.limit('claude', 7200)
        self.limit('codex', 3600)
        self.limit('agy:claude', 5400)
        self.limit('agy:gemini-4-argon', 9000)
        decision = self.router().select('operator')
        self.assertEqual((decision.action, decision.account, decision.until), ('wait', 'codex', NOW + 3600))
        self.assertEqual(decision.provider, '')

    def test_wait_without_known_reset_uses_default_cooldown(self):
        for account in ('claude', 'codex', 'agy:claude', 'agy:gemini-4-argon'):
            self.store.put([Snapshot(account, 'limit', 100.0, None, 'fixture', NOW)])
        decision = self.router().select('operator')
        self.assertEqual((decision.action, decision.until), ('wait', NOW + 900))

    def test_unlimited_argon_ignores_percentages_but_not_limit_errors(self):
        self.five('agy:gemini-4-argon', 100.0, 0)
        for account in ('claude', 'codex', 'agy:claude'):
            self.limit(account, 3600)
        self.assertEqual(self.router().select('operator').model, 'gemini-4-argon-medium')
        self.limit('agy:gemini-4-argon', 600)
        self.assertEqual(self.router().select('operator').action, 'wait')

    def test_light_roles_use_flash_and_simple_validator_follows_flag(self):
        for role in ('communicator', 'remote_manager', 'workspace_watcher'):
            self.assertEqual(self.router().select(role).model, 'gemini-3.8-flash-medium', role)
        self.assertEqual(self.router().select('validator').model, 'sonnet')
        simple = SimpleNamespace(plan_json=json.dumps({'mode': 'direct', 'simple': True}), provider_override='')
        self.assertEqual(self.router().select('validator', simple).model, 'gemini-3.8-flash-medium')
        self.assertEqual(self.router().select('operator', simple).model, 'sonnet')
        self.config['quota_routing']['light_validator'] = True
        self.assertEqual(self.router().select('validator').quota_class, 'light')
        self.limit('agy:gemini-3.8-flash', 1800)
        self.assertEqual(self.router().select('communicator').model, 'gemini-4-argon-low')

    def test_explicit_provider_is_never_rerouted(self):
        task = SimpleNamespace(provider_override='claude_cli', plan_json='{}')
        self.five('codex', 0.0, 0)
        first = self.router().select('operator', task)
        self.assertEqual((first.action, first.provider, first.source), ('run', 'claude_cli', 'pinned'))
        self.five('claude', 95.0, self.HOUR)
        decision = self.router().select('operator', task)
        self.assertEqual((decision.action, decision.provider, decision.reserved_use), ('run', 'claude_cli', True))
        self.five('claude', 100.0, self.HOUR)
        decision = self.router().select('operator', task)
        self.assertEqual((decision.action, decision.source, decision.provider), ('wait', 'pinned', 'claude_cli'))
        self.assertIn('not rerouted', decision.reason)

    def test_sticky_prefers_previous_route_while_eligible(self):
        self.five('claude', 30.0, 1 * self.HOUR)
        self.five('codex', 5.0, 0.2 * self.HOUR)
        self.assertEqual(self.router().select('operator').provider, 'codex_cli')
        self.assertEqual(self.router().select('operator', prefer=('claude_cli', 'sonnet')).provider, 'claude_cli')
        self.config['quota_routing']['sticky_routes'] = False
        self.assertEqual(self.router().select('operator', prefer=('claude_cli', 'sonnet')).provider, 'codex_cli')
        self.config['quota_routing']['sticky_routes'] = True
        self.five('claude', 97.0, 1 * self.HOUR)
        self.assertEqual(self.router().select('operator', prefer=('claude_cli', 'sonnet')).provider, 'codex_cli')

    def test_manual_cooldown_and_elapsed_windows(self):
        self.cooldowns['codex_cli'] = NOW + 100
        self.assertEqual(self.router().select('operator').provider, 'claude_cli')
        self.cooldowns['quota:claude'] = NOW + 100
        self.assertEqual(self.router().select('operator').account, 'agy:claude')
        self.cooldowns.clear()
        self.store.put([Snapshot('claude', 'five_hour', 99.0, NOW - 60, 'fixture', NOW - 4000)])
        self.five('codex', 50.0, 3 * self.HOUR)
        self.assertEqual(self.router().select('operator').provider, 'claude_cli')  # reset 60s ago: usable and freshest

    def test_unrouted_role_and_disabled_router(self):
        self.config['quota_routing']['role_classes'].pop('world_watcher')
        self.assertIsNone(self.router().select('world_watcher'))
        self.assertIsNone(self.router().select('unknown'))
        self.assertFalse(QuotaRouter({}, self.store).enabled)

    def test_workaround_routes_to_remote_claude_operator_when_claude_exhausted(self):
        self.config['quota_routing']['workarounds'] = {'claude': [{'provider': 'remote_claude_operator', 'model': ''}]}
        self.limit('claude', 3600)
        self.limit('codex', 3600)
        self.limit('agy:claude', 3600)
        decision = self.router().select('operator')
        self.assertEqual((decision.action, decision.provider, decision.account, decision.source),
                         ('run', 'remote_claude_operator', 'remote_claude', 'workaround'))




class ClaudeCliProbeTests(unittest.TestCase):
    EVENT = {'type': 'rate_limit_event', 'rate_limit_info': {'unifiedWindows': {
        'five_hour': {'utilization': 0.2, 'resetsAt': 1794247800, 'status': 'allowed'},
        'seven_day': {'utilization': 0.43, 'resetsAt': 1794686400, 'status': 'allowed'}}}}

    def test_throttled_probe_reads_cache_without_running_cli(self):
        import json, tempfile
        from pathlib import Path
        from core.quota import ClaudeCliProbe
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / 'claude_probe.json'
            cache.write_text(json.dumps({'captured_at': 1000.0, 'event': self.EVENT}), encoding='utf-8')
            probe = ClaudeCliProbe(cache, executable='definitely-missing-claude', min_interval=1800, clock=lambda: 1500.0)
            snaps = {s.window: s for s in probe.probe()}
            self.assertAlmostEqual(snaps['five_hour'].used_percent, 20.0)
            self.assertAlmostEqual(snaps['seven_day'].used_percent, 43.0)
            # A due probe whose CLI is missing keeps the last figures instead of failing.
            probe.clock = lambda: 99999.0
            self.assertEqual(len(probe.probe()), 2)


class AgyProbeTests(unittest.TestCase):
    def test_throttled_probe_reads_cache_without_running_cli(self):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / 'agy_probe.json'
            cached_snaps = [
                {'account': 'agy:gemini-4-argon', 'window': 'five_hour', 'used_percent': None, 'resets_at': None,
                 'source': 'agy_probe', 'observed_at': 1000.0, 'confidence': 'inferred'},
                {'account': 'agy:gemini-3.8-flash', 'window': 'five_hour', 'used_percent': None, 'resets_at': None,
                 'source': 'agy_probe', 'observed_at': 1000.0, 'confidence': 'inferred'},
                {'account': 'agy:claude', 'window': 'five_hour', 'used_percent': None, 'resets_at': None,
                 'source': 'agy_probe', 'observed_at': 1000.0, 'confidence': 'inferred'},
            ]
            cache.write_text(json.dumps({'captured_at': 1000.0, 'tier_id': 'g1-pro-tier',
                                        'snapshots': cached_snaps}), encoding='utf-8')
            probe = AgyProbe(cache, executable='definitely-missing-agy', min_interval=1800, clock=lambda: 1500.0)
            snaps = {s.account: s for s in probe.probe()}
            self.assertEqual(len(snaps), 3)
            self.assertEqual(snaps['agy:gemini-4-argon'].confidence, 'inferred')
            self.assertEqual(snaps['agy:gemini-3.8-flash'].source, 'agy_probe')
            self.assertEqual(snaps['agy:claude'].observed_at, 1000.0)

    def test_probe_runs_mock_acp_when_cache_stale(self):
        import json, tempfile
        from pathlib import Path
        from unittest.mock import patch, MagicMock
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / 'agy_probe.json'
            probe = AgyProbe(cache, executable='agy', min_interval=1800, clock=lambda: 2000.0)
            mock_proc = MagicMock()
            acp_response = (
                json.dumps({'jsonrpc': '2.0', 'result': {'agentInfo': {'name': 'Antigravity ACP Server'}}, 'id': 1}) + '\n' +
                json.dumps({'jsonrpc': '2.0', 'result': {
                    '_meta': {'antigravity': {'tier': {'id': 'g1-pro-tier', 'name': 'Google AI Pro'}}},
                    'models': {'availableModels': [{'modelId': 'gemini-4-argon-medium'}]}
                }, 'id': 2}) + '\n'
            )
            mock_proc.communicate.return_value = (acp_response, '')
            with patch('core.executables.find_executable', return_value='agy'), \
                 patch('subprocess.Popen', return_value=mock_proc):
                snaps = {s.account: s for s in probe.probe()}
                self.assertEqual(len(snaps), 3)
                self.assertIn('agy:gemini-4-argon', snaps)
                self.assertIn('agy:gemini-3.8-flash', snaps)
                self.assertIn('agy:claude', snaps)
                saved = json.loads(cache.read_text(encoding='utf-8'))
                self.assertEqual(saved['tier_id'], 'g1-pro-tier')


class RemoteClaudeOperatorProviderTests(unittest.TestCase):
    def test_fails_closed_without_touching_colab(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import MagicMock
        from core.remote_claude import RemoteClaudeOperatorProvider
        with tempfile.TemporaryDirectory() as tmp:
            cloud = MagicMock()
            result = RemoteClaudeOperatorProvider(Path(tmp), {}, cloud=cloud).run(
                role='operator', prompt='Do something', workspace=Path(tmp), timeout=60,
                model='', log_path=None, cancel_event=None)
            self.assertFalse(result.success)
            self.assertTrue(result.structured_data['unsupported'])
            self.assertEqual(result.structured_data['quota_account'], 'remote_claude')
            self.assertEqual(cloud.method_calls, [])


if __name__ == '__main__':
    unittest.main()
