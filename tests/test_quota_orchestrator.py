import json
import tempfile
import time
import unittest
from pathlib import Path
from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.models import AgentRunResult
from core.orchestrator import HybridOrchestrator
from core.quota import QuotaStore, Snapshot


class RecordingRunner:
    """Fake adapter recording the routing arguments each role call received."""

    def __init__(self, failures=None):
        self.calls, self.failures = [], dict(failures or {})

    def run_role(self, role, **kwargs):
        self.calls.append((role, kwargs.get('provider_override'), kwargs.get('model_override', 'unset')))
        failure = self.failures.pop(role, None)
        if failure:
            return AgentRunResult(role, 'fake', False, '', exit_code=1, error='limit', structured_data=failure)
        output = {'planner': 'brief', 'validator': 'VERDICT: PASS\nEvidence: inspected'}.get(role, 'done')
        return AgentRunResult(role, 'fake', True, output, structured_data={'usage': {'input_tokens': 10, 'output_tokens': 5}, 'model': 'm'})


class QuotaOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        example = json.loads((Path(__file__).resolve().parents[1] / 'config/workflow_config.example.json').read_text(encoding='utf-8'))
        self.config = {k: example[k] for k in ('providers', 'roles', 'quota_routing')}
        self.config.update(logs_dir='logs', isolation={'enabled': False})
        self.config['quota_routing']['enabled'] = True
        self.db = WorkspaceDatabase(self.root / 'db')

    def tearDown(self):
        self.tmp.cleanup()

    def orchestrator(self, runner):
        return HybridOrchestrator(self.root, self.config, self.db, ContextLogManager(self.root / 'logs'), runner)

    def snapshot(self, account, used, resets_in=3600, window='five_hour'):
        QuotaStore(self.db).put([Snapshot(account, window, used, time.time() + resets_in, 'fixture', time.time())])

    def events(self, kind):
        return [e for e in self.db.get_recent_events(limit=200) if e['event_type'] == kind]

    def test_roles_are_routed_around_reserved_accounts_and_logged(self):
        self.snapshot('claude', 95.0)
        self.snapshot('codex', 20.0)
        runner = RecordingRunner()
        orch = self.orchestrator(runner)
        task = orch.run_task_to_completion(orch.submit_task('do it').id)
        self.assertEqual(task.status, 'COMPLETED')
        routed = {role: (provider, model) for role, provider, model in runner.calls}
        self.assertEqual(routed['planner'], ('codex_cli', 'gpt-6-astra'))
        self.assertEqual(routed['operator'], ('codex_cli', 'gpt-6.1-sol'))
        self.assertEqual(routed['communicator'], ('agy_headless_safe', 'gemini-3.8-flash-low'))  # D1 pinned
        events = self.events('QUOTA_ROUTE')
        self.assertTrue(events)
        payload = next(p for p in (json.loads(e['payload_json']) for e in events) if p['role'] == 'planner')
        self.assertEqual((payload['source'], payload['account'], payload['default_provider']), ('class', 'codex', 'agy_headless_safe'))
        self.assertTrue(all(e['task_id'] == task.id for e in events))
        from core.observability import task_detail
        self.assertEqual({r['role'] for r in task_detail(self.db, task.id)['quota_routes']}, {r['role'] for r in map(lambda e: json.loads(e['payload_json']), events)})
        ledger = QuotaStore(self.db).ledger(limit=50)
        self.assertEqual(len(ledger), len(runner.calls))
        self.assertEqual({row['tokens'] for row in ledger}, {15})

    def test_explicit_provider_is_never_rerouted_and_waits_without_retry(self):
        self.snapshot('claude', 100.0, resets_in=7200)
        self.snapshot('codex', 0.0)
        runner = RecordingRunner()
        orch = self.orchestrator(runner)
        task = orch.run_task_to_completion(orch.submit_task('pinned', provider_override='claude_cli').id)
        self.assertEqual(task.status, 'WAITING_PROVIDER')
        self.assertEqual((runner.calls, self.db.get_task(task.id).retry_count), ([], 0))
        wait = orch.control.waits()[0]
        self.assertEqual(wait['dependency'], 'quota:claude')
        self.assertIn('not rerouted', wait['reason'])
        self.assertGreater(wait['due'] - time.time(), 7000)
        payload = json.loads(self.events('QUOTA_ROUTE')[0]['payload_json'])
        self.assertEqual((payload['action'], payload['source']), ('wait', 'pinned'))

    def test_limit_error_waits_without_burning_retry_then_reroutes_after_reset(self):
        # The planner's ordered D3 chain starts with Codex.
        runner = RecordingRunner(failures={'planner': {'quota': {'exhausted': True, 'matched': 'hit your limit', 'resets_at': time.time() + 600,
                                                                     'account': 'codex', 'provider': 'codex_cli', 'model': 'gpt-6-astra'}}})
        orch = self.orchestrator(runner)
        task = orch.run_task_to_completion(orch.submit_task('limited').id)
        self.assertEqual(task.status, 'WAITING_PROVIDER')
        stored = self.db.get_task(task.id)
        self.assertEqual((stored.retry_count, orch.control.waits()[0]['dependency']), (0, 'quota:codex'))
        self.assertFalse(orch.control.available('quota:codex'))
        self.assertEqual(len(self.events('QUOTA_EXHAUSTED')), 1)
        self.assertEqual([s.window for s in QuotaStore(self.db).snapshots('codex')], ['limit'])
        orch.control.advance_waits()  # still cooling: no wake
        self.assertEqual(self.db.get_task(task.id).status, 'WAITING_PROVIDER')
        with self.db._connect() as c:  # the reset time passes
            c.execute('UPDATE provider_cooldowns SET until_time=0')
            c.execute("UPDATE quota_snapshots SET resets_at=? WHERE window='limit'", (time.time() - 1,))
            c.execute('UPDATE task_waits SET due=0')
        orch.control.advance_waits()
        self.assertNotEqual(self.db.get_task(task.id).status, 'WAITING_PROVIDER')
        resumed = orch.run_task_to_completion(task.id)
        self.assertEqual((resumed.status, resumed.retry_count), ('COMPLETED', 0))
        self.assertEqual(runner.calls[1][:2], ('planner', 'codex_cli'))

    def test_everything_exhausted_waits_for_earliest_reset(self):
        for account, reset in (('claude', 9000), ('codex', 4000), ('agy:claude', 6000), ('agy:gemini-4-argon', 8000)):
            self.snapshot(account, 100.0, reset, window='limit')
        orch = self.orchestrator(RecordingRunner())
        task = orch.run_task_to_completion(orch.submit_task('nothing left').id)
        wait = orch.control.waits()[0]
        self.assertEqual((task.status, wait['dependency']), ('WAITING_PROVIDER', 'quota:codex'))
        self.assertAlmostEqual(wait['due'] - time.time(), 4000, delta=30)

    def test_disabled_router_changes_nothing(self):
        self.config['quota_routing']['enabled'] = False
        self.snapshot('claude', 100.0)
        runner = RecordingRunner()
        orch = self.orchestrator(runner)
        self.assertIsNone(orch.quota)
        task = orch.run_task_to_completion(orch.submit_task('plain').id)
        self.assertEqual(task.status, 'COMPLETED')
        self.assertTrue(all(call[1] is None and call[2] == 'unset' for call in runner.calls))
        self.assertEqual(self.events('QUOTA_ROUTE'), [])


class LightRoleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        example = json.loads((Path(__file__).resolve().parents[1] / 'config/workflow_config.example.json').read_text(encoding='utf-8'))
        self.config = {k: example[k] for k in ('providers', 'roles', 'quota_routing', 'watchers')}
        self.config['quota_routing']['enabled'] = True
        self.db = WorkspaceDatabase(self.root / 'db')

    def tearDown(self):
        self.tmp.cleanup()

    def test_example_config_assigns_light_roles_to_flash_with_30_minute_review(self):
        roles = self.config['roles']
        for role in ('communicator', 'remote_manager', 'workspace_watcher', 'world_watcher'):
            self.assertTrue(roles[role]['model'].startswith('gemini-3.8-flash'), role)
            self.assertEqual(self.config['quota_routing']['role_classes'][role], 'light')
        self.assertEqual(self.config['watchers']['workspace']['llm_review_seconds'], 1800)
        self.assertEqual(roles['operator']['model'], '')

    def test_simple_plan_flag_survives_parsing_and_selects_light_validator(self):
        from core.plans import parse_plan
        fence = chr(96) * 3
        simple = parse_plan(fence + 'planner_plan\n{"mode":"direct","simple":true,"checks":[]}\n' + fence)
        self.assertIs(simple['simple'], True)
        self.assertNotIn('simple', parse_plan(fence + 'planner_plan\n{"mode":"direct","simple":"yes"}\n' + fence))
        from core.quota import QuotaRouter
        router = QuotaRouter(self.config, QuotaStore(self.db))
        task = type('T', (), {'plan_json': json.dumps(simple), 'provider_override': ''})()
        self.assertEqual(router.select('validator', task).model, 'gemini-3.8-flash-medium')
        self.assertEqual(router.select('operator', task).model, 'sonnet')

    def test_workspace_watcher_review_cadence_and_light_routing(self):
        from watchers.workspace_watcher import WorkspaceWatcherDaemon
        from core.quota import build_router
        runner = RecordingRunner()
        self.config['watchers']['workspace'].update(stuck_task_threshold_seconds=0, llm_review_seconds=1800)
        task = self.db.create_task('stuck')
        self.db.claim_task('owner', 1)
        with self.db._connect() as c:
            c.execute("UPDATE tasks SET status='OPERATING',heartbeat_at=1")
        watcher = WorkspaceWatcherDaemon(self.root, self.config, self.db, ContextLogManager(self.root / 'logs'), runner,
                                         quota_router=build_router(self.config, self.db))
        watcher.check_once()
        watcher.check_once()  # inside the 30-minute LLM cadence
        self.assertEqual(runner.calls, [('workspace_watcher', 'agy_headless_safe', 'gemini-3.8-flash-low')])
        self.assertEqual(QuotaStore(self.db).ledger()[0]['role'], 'workspace_watcher')
        # Light and uncapped accounts both exhausted: skip the review without consuming the cadence.
        for account in ('agy:gemini-3.8-flash', 'agy:claude', 'agy:gpt-oss', 'agy:gemini-4-argon'):  # whole D1 chain out
            QuotaStore(self.db).put([Snapshot(account, 'limit', 100.0, time.time() + 600, 'fixture', time.time(), 'inferred')])
        watcher._last_anomaly_at = 0.0
        watcher.check_once()
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(watcher._last_anomaly_at, 0.0)
        waits = [json.loads(e['payload_json']) for e in self.db.get_recent_events(limit=50) if e['event_type'] == 'QUOTA_ROUTE']
        self.assertEqual(waits[0]['action'], 'wait')

    def test_world_watcher_leaves_items_unseen_while_quota_is_out(self):
        from watchers.world_watcher import WorldWatcherDaemon
        from core.quota import build_router
        runner = RecordingRunner()
        for account in ('agy:gemini-3.8-flash', 'agy:claude', 'agy:gpt-oss', 'agy:gemini-4-argon'):  # whole D1 chain out
            QuotaStore(self.db).put([Snapshot(account, 'limit', 100.0, time.time() + 600, 'fixture', time.time(), 'inferred')])
        watcher = WorldWatcherDaemon(self.root, self.config, self.db, ContextLogManager(self.root / 'logs'), runner,
                                     quota_router=build_router(self.config, self.db))
        item = {'source': 's', 'title': 't', 'url': 'https://example.invalid/x'}
        watcher.check_once(force_items=[dict(item)])
        self.assertEqual(runner.calls, [])
        with self.db._connect() as c:
            c.execute('DELETE FROM quota_snapshots')
        watcher.check_once(force_items=[dict(item)])
        self.assertEqual([c[:2] for c in runner.calls], [('world_watcher', 'agy_headless_safe')])


if __name__ == '__main__':
    unittest.main()
