"""P2.4-03: the validator prefers a different quota account than the operator that did the work."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.models import AgentRunResult
from core.orchestrator import HybridOrchestrator
from core.quota import QuotaRouter, QuotaStore, Snapshot

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = json.loads((ROOT / 'config/workflow_config.example.json').read_text(encoding='utf-8'))


class RouterAvoidanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = QuotaStore(WorkspaceDatabase(Path(self.tmp.name) / 'db'))
        self.config = json.loads(json.dumps(EXAMPLE))
        self.config['quota_routing']['enabled'] = True

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, account, used, window='five_hour', resets_in=3600):
        self.store.put([Snapshot(account, window, used, time.time() + resets_in, 'fixture', time.time())])

    def test_avoided_account_ranks_last_even_with_more_headroom(self):
        self.put('claude', 1.0, resets_in=3700)  # freshest window and most headroom
        self.put('codex', 60.0)
        router = QuotaRouter(self.config, self.store)
        self.assertEqual(router.select('validator').account, 'claude')
        decision = router.select('validator', avoid={'claude'})
        self.assertNotEqual(decision.account, 'claude')
        self.assertIn('independent of claude', decision.reason)

    def test_same_account_only_when_nothing_else_and_it_is_reported(self):
        self.put('claude', 1.0)
        for account in ('codex', 'agy:claude'):
            self.put(account, 100.0, window='limit', resets_in=600)
        decision = QuotaRouter(self.config, self.store).select('validator', avoid={'claude'})
        self.assertEqual(decision.account, 'claude')
        self.assertIn('NOT independent', decision.reason)


class Runner:
    def __init__(self):
        self.calls = []

    def run_role(self, role, task_prompt='', cli_log_path=None, provider_override=None, model_override=None):
        self.calls.append((role, provider_override))
        output = {'validator': 'VERDICT: PASS\nEvidence: inspected'}.get(role, 'done')
        return AgentRunResult(role, provider_override or 'p', True, output,
                              structured_data={'usage': {'input_tokens': 1, 'output_tokens': 1}, 'model': model_override or ''})


class OrchestratorIndependenceTests(unittest.TestCase):
    def test_validator_runs_on_a_different_account_than_operator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {k: json.loads(json.dumps(EXAMPLE[k])) for k in ('providers', 'roles', 'quota_routing')}
            config.update(logs_dir='logs', isolation={'enabled': False})
            config['quota_routing']['enabled'] = True
            db = WorkspaceDatabase(root / 'db')
            QuotaStore(db).put([Snapshot('claude', 'five_hour', 1.0, time.time() + 3700, 'fixture', time.time()),
                                Snapshot('codex', 'five_hour', 60.0, time.time() + 3600, 'fixture', time.time())])
            runner = Runner()
            orch = HybridOrchestrator(root, config, db, ContextLogManager(root / 'logs'), runner)
            task = orch.run_task_to_completion(orch.submit_task('cross-check me').id)
            self.assertEqual(task.status, 'COMPLETED')
            providers = dict((role, provider) for role, provider in runner.calls)
            self.assertEqual(providers['operator'], 'claude_cli')
            self.assertNotEqual(providers['validator'], 'claude_cli')
            routes = [json.loads(e['payload_json']) for e in db.get_recent_events(limit=100) if e['event_type'] == 'QUOTA_ROUTE']
            self.assertTrue(any(r['role'] == 'validator' and 'independent of claude' in r['reason'] for r in routes))


if __name__ == '__main__':
    unittest.main()
