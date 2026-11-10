"""P2.1-05: API usage/quota ledger and routing effects for OpenCode API profiles."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.models import AgentRunResult
from core.orchestrator import HybridOrchestrator
from core.quota import QuotaRouter, QuotaStore, record_result

ROOT = Path(__file__).resolve().parents[1]
OPENROUTER = {'type': 'opencode_cli', 'api_profile': {'vendor': 'openrouter', 'auto_free': True, 'free_only': True}}


def api_result(success, quota=None, run_id='r1', account='api:openrouter:m:free', usage=None):
    data = {'run_id': run_id, 'quota_account': account, 'model': 'm:free',
            'usage': usage if usage is not None else {'input_tokens': 30, 'output_tokens': 7, 'context_tokens': 20},
            'total_cost_usd': 0.0, 'num_turns': 2}
    if quota:
        data['quota'] = quota
    return AgentRunResult('operator', 'opencode_api_openrouter', success, 'ok' if success else '',
                          error=None if success else 'limit', structured_data=data)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = WorkspaceDatabase(Path(self.tmp.name) / 'db')
        self.store = QuotaStore(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_success_ledger_row_has_account_tokens_and_cost(self):
        record_result(self.store, {}, 'operator', api_result(True))
        [row] = self.store.ledger()
        self.assertEqual((row['account'], row['tokens'], row['num_turns']), ('api:openrouter:m:free', 37, 2))
        self.assertEqual(json.loads(row['usage_json'])['context_tokens'], 20)

    def test_same_invocation_recorded_once(self):
        record_result(self.store, {}, 'operator', api_result(True, run_id='same'))
        record_result(self.store, {}, 'operator', api_result(True, run_id='same'))
        self.assertEqual(len(self.store.ledger()), 1)

    def test_missing_usage_stays_empty_not_invented(self):
        record_result(self.store, {}, 'operator', api_result(True, run_id='nousage', usage={}))
        [row] = self.store.ledger()
        self.assertEqual((row['tokens'], json.loads(row['usage_json'])), (0, {}))

    def test_free_model_limit_does_not_block_the_openrouter_account(self):
        resets = time.time() + 600
        exhausted = record_result(self.store, {}, 'operator', api_result(False, {
            'exhausted': True, 'matched': 'HTTP 429', 'resets_at': resets, 'account': 'api:openrouter:m:free'}))
        self.assertEqual(exhausted['account'], 'api:openrouter:m:free')
        config = {'providers': {'or': OPENROUTER, 'codex_cli': {'type': 'codex_cli'}},
                  'quota_routing': {'enabled': True, 'accounts': {}, 'role_classes': {'operator': 'worker'},
                                    'classes': {'worker': [{'provider': 'or'}, {'provider': 'codex_cli'}]}}}
        decision = QuotaRouter(config, self.store).select('operator')
        self.assertEqual((decision.provider, decision.account), ('or', 'api:openrouter'))

    def test_whole_free_pool_exhausted_routes_to_next_provider(self):
        record_result(self.store, {}, 'operator', api_result(False, {
            'exhausted': True, 'matched': 'all free models cooling down', 'resets_at': time.time() + 600,
            'account': 'api:openrouter'}, account='api:openrouter'))
        config = {'providers': {'or': OPENROUTER, 'codex_cli': {'type': 'codex_cli'}},
                  'quota_routing': {'enabled': True, 'accounts': {}, 'role_classes': {'operator': 'worker'},
                                    'classes': {'worker': [{'provider': 'or'}, {'provider': 'codex_cli'}]}}}
        decision = QuotaRouter(config, self.store).select('operator')
        self.assertEqual(decision.provider, 'codex_cli')


class Runner:
    def run_role(self, role, **kwargs):
        output = {'validator': 'VERDICT: PASS\nEvidence: inspected'}.get(role, 'done')
        return AgentRunResult(role, 'opencode_api_openrouter', True, output, structured_data={
            'run_id': f'{role}-{time.time_ns()}', 'quota_account': 'api:openrouter:m:free', 'model': 'm:free',
            'usage': {'input_tokens': 10, 'output_tokens': 5}})


class DisabledRoutingLedgerTests(unittest.TestCase):
    def test_ledger_written_once_per_invocation_with_routing_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            example = json.loads((ROOT / 'config/workflow_config.example.json').read_text(encoding='utf-8'))
            config = {k: example[k] for k in ('providers', 'roles', 'quota_routing')}
            config.update(logs_dir='logs', isolation={'enabled': False})
            config['quota_routing']['enabled'] = False
            db = WorkspaceDatabase(root / 'db')
            orch = HybridOrchestrator(root, config, db, ContextLogManager(root / 'logs'), Runner())
            self.assertIsNone(orch.quota)
            task = orch.run_task_to_completion(orch.submit_task('plain').id)
            self.assertEqual(task.status, 'COMPLETED')
            rows = QuotaStore(db).ledger(limit=100)
            roles = [r['role'] for r in rows if r['task_id'] == task.id]
            self.assertTrue({'planner', 'operator', 'validator'} <= set(roles))
            self.assertEqual(len(roles), len({r['run_key'] for r in rows if r['task_id'] == task.id}))
            self.assertTrue(all(r['account'] == 'api:openrouter:m:free' for r in rows if r['task_id'] == task.id))


if __name__ == '__main__':
    unittest.main()
