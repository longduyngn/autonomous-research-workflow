"""P2.3 (D1'): communicator/watchers pinned to AGY Gemini 3.8 Flash low with AGY-only fallbacks."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.configuration import validate_config
from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.models import AgentRunResult
from core.orchestrator import HybridOrchestrator
from core.providers import EphemeralAgentRunner
from core.quota import QuotaRouter, QuotaStore, Snapshot

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = json.loads((ROOT / 'config/workflow_config.example.json').read_text(encoding='utf-8'))
CHAIN = [('agy_headless_safe', 'gemini-3.8-flash-low'), ('agy_headless_safe', 'claude-sonnet-5-5-low'),
         ('agy_headless_safe', 'gpt-oss-120b-medium')]
LIMIT = {'quota': {'exhausted': True, 'matched': 'usage limit', 'resets_at': time.time() + 600}}


def scripted(outcomes, calls):
    """Fake per-attempt execution keyed by model; unknown models succeed."""
    def run(self, role, prompt, log=None, provider=None, model=None):
        calls.append((role, provider, model))
        outcome = outcomes.get((role, model)) or outcomes.get(role)
        if outcome == 'limit':
            return AgentRunResult(role, provider, False, '', error='Provider quota exhausted (usage limit)',
                                  structured_data=json.loads(json.dumps(LIMIT)))
        if outcome == 'auth':
            return AgentRunResult(role, provider, False, '', error='CLI authentication required in this execution environment')
        output = {'validator': 'VERDICT: PASS\nEvidence: inspected'}.get(role, f'{role} ok via {model}')
        return AgentRunResult(role, provider, True, output, structured_data={'model': model or ''})
    return run


class PinnedRunnerTests(unittest.TestCase):
    def runner(self):
        return EphemeralAgentRunner(tempfile.mkdtemp(), {k: EXAMPLE[k] for k in ('providers', 'roles')})

    def test_example_config_pins_three_roles(self):
        for role in ('communicator', 'workspace_watcher', 'world_watcher'):
            self.assertEqual(self.runner().pinned_chain(role), CHAIN)
        self.assertIsNone(self.runner().pinned_chain('operator'))

    def test_limit_falls_back_inside_agy_in_order(self):
        calls = []
        with patch.object(EphemeralAgentRunner, '_run_role_with_memory',
                          scripted({('communicator', 'gemini-3.8-flash-low'): 'limit'}, calls)):
            result = self.runner().run_role('communicator', 'hi')
        self.assertTrue(result.success)
        self.assertEqual([c[2] for c in calls], ['gemini-3.8-flash-low', 'claude-sonnet-5-5-low'])
        self.assertEqual(len(result.structured_data['pinned_route']['attempts']), 2)

    def test_task_override_never_moves_pinned_role_out_of_agy(self):
        calls = []
        with patch.object(EphemeralAgentRunner, '_run_role_with_memory', scripted({}, calls)):
            self.runner().run_role('communicator', 'hi', provider_override='codex_cli', model_override='gpt-6-astra')
        self.assertEqual(calls, [('communicator', 'agy_headless_safe', 'gemini-3.8-flash-low')])

    def test_router_choice_inside_chain_is_the_starting_point(self):
        calls = []
        with patch.object(EphemeralAgentRunner, '_run_role_with_memory', scripted({}, calls)):
            self.runner().run_role('communicator', 'hi', provider_override='agy_headless_safe', model_override='gpt-oss-120b-medium')
        self.assertEqual(calls[0][2], 'gpt-oss-120b-medium')

    def test_auth_failure_does_not_try_other_models(self):
        calls = []
        with patch.object(EphemeralAgentRunner, '_run_role_with_memory', scripted({'communicator': 'auth'}, calls)):
            result = self.runner().run_role('communicator', 'hi')
        self.assertFalse(result.success)
        self.assertEqual(len(calls), 1)
        self.assertNotIn('pinned_exhausted', result.structured_data)

    def test_whole_chain_exhausted_is_marked_and_does_not_request_a_wait(self):
        calls = []
        with patch.object(EphemeralAgentRunner, '_run_role_with_memory', scripted({'communicator': 'limit'}, calls)):
            result = self.runner().run_role('communicator', 'hi')
        self.assertEqual(len(calls), 3)
        self.assertTrue(result.structured_data['pinned_exhausted'])
        self.assertNotIn('quota', result.structured_data)

    def test_fallback_outside_provider_type_is_rejected(self):
        config = json.loads(json.dumps(EXAMPLE))
        config['roles']['communicator']['fallbacks'] = [{'provider': 'codex_cli', 'model': 'gpt-6-astra'}]
        with self.assertRaises(ValueError):
            validate_config(config)
        config['roles']['communicator'].pop('pinned')
        config['roles']['communicator']['fallbacks'] = [{'provider': 'agy_headless_safe', 'model': 'x'}]
        with self.assertRaises(ValueError):
            validate_config(config)


class PinnedRouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = QuotaStore(WorkspaceDatabase(Path(self.tmp.name) / 'db'))
        self.config = json.loads(json.dumps(EXAMPLE))
        self.config['quota_routing']['enabled'] = True

    def tearDown(self):
        self.tmp.cleanup()

    def limit(self, account):
        self.store.put([Snapshot(account, 'limit', 100.0, time.time() + 600, 'fixture', time.time(), 'inferred')])

    def test_pinned_precedes_task_override(self):
        task = SimpleNamespace(provider_override='claude_cli', plan_json='{}')
        decision = QuotaRouter(self.config, self.store).select('communicator', task)
        self.assertEqual((decision.source, decision.provider, decision.model),
                         ('role_pinned', 'agy_headless_safe', 'gemini-3.8-flash-low'))

    def test_codex_and_claude_out_do_not_affect_pinned_roles(self):
        for account in ('codex', 'claude'):
            self.limit(account)
        for role in ('communicator', 'workspace_watcher', 'world_watcher'):
            self.assertEqual(QuotaRouter(self.config, self.store).select(role).action, 'run')

    def test_exhausted_chain_communicator_runs_watchers_wait(self):
        for account in ('agy:gemini-3.8-flash', 'agy:claude', 'agy:gpt-oss'):
            self.limit(account)
        router = QuotaRouter(self.config, self.store)
        self.assertEqual(router.select('communicator').action, 'run')
        self.assertEqual(router.select('world_watcher').action, 'wait')


class DiscordStillAnswersTests(unittest.TestCase):
    def test_task_report_uses_template_when_every_provider_is_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {k: EXAMPLE[k] for k in ('providers', 'roles', 'quota_routing')}
            config.update(logs_dir='logs', isolation={'enabled': False})
            config['quota_routing'] = json.loads(json.dumps(config['quota_routing']))
            config['quota_routing']['enabled'] = True
            db = WorkspaceDatabase(root / 'db')
            calls = []
            fake = scripted({'communicator': 'limit'}, calls)
            with patch.object(EphemeralAgentRunner, '_run_role_with_memory', fake):
                orch = HybridOrchestrator(root, config, db, ContextLogManager(root / 'logs'),
                                          EphemeralAgentRunner(root, config))
                task = orch.run_task_to_completion(orch.submit_task('report me').id)
            self.assertEqual(task.status, 'COMPLETED')  # not parked in WAITING_PROVIDER
            communicator_calls = [c for c in calls if c[0] == 'communicator']
            self.assertEqual([c[2] for c in communicator_calls], [m for _, m in CHAIN])
            final = db.get_task(task.id).final_response
            # No-LLM template built from controller facts: status, workspace and operator output.
            self.assertTrue(final.startswith(f'Task #{task.id}: COMPLETED'))
            self.assertIn('operator ok via', final)
            self.assertNotIn('communicator ok', final)


if __name__ == '__main__':
    unittest.main()


class NoModelChatTests(unittest.TestCase):
    def test_chat_without_model_answers_from_db_facts_and_creates_nothing(self):
        from core.conversation import Conversation
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = WorkspaceDatabase(root / 'db')
            orch = HybridOrchestrator(root, {'isolation': {'enabled': False}}, db, ContextLogManager(root / 'logs'),
                                      EphemeralAgentRunner(root, {}))
            chat = Conversation(db, orch.submit_task, reply=lambda *args: None)  # pinned chain unavailable
            first = chat.receive('!task reproduce DDPM', 'scope', 'user', 'k1', '')
            before = len(db.get_all_tasks())
            answer = chat.receive('how is it going?', 'scope', 'user', 'k2', '')
            self.assertEqual(len(db.get_all_tasks()), before)
            self.assertIn(f"#{first['task_id']} PENDING", answer['response'])
            self.assertIn('no new task was started', answer['response'])
