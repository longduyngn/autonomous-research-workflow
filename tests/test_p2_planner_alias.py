"""P2.2: researcher -> planner rename with read compatibility (config, plans, DB, memory) and D3 routing."""
import json
import tempfile
import time
import unittest
import warnings
from pathlib import Path

from core.configuration import role_turn_budget, validate_config
from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.models import AgentRunResult, RoleName
from core.orchestrator import HybridOrchestrator
from core.plans import parse_plan
from core.quota import QuotaRouter, QuotaStore, Snapshot
from core.role_memory import RoleMemory
from core.role_names import canonical_role, canonical_status, normalize_config

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = json.loads((ROOT / 'config/workflow_config.example.json').read_text(encoding='utf-8'))


def legacy_config():
    """The example config as an older checkout would have written it."""
    config = json.loads(json.dumps(EXAMPLE))
    config['roles'] = {('researcher' if k == 'planner' else k): v for k, v in config['roles'].items()}
    config['roles']['researcher']['prompt_file'] = 'roles/researcher.md'
    config['role_turn_budget'] = {('researcher' if k == 'planner' else k): v for k, v in config['role_turn_budget'].items()}
    routing = config['quota_routing']
    routing['role_classes'] = {('researcher' if k == 'planner' else k): v for k, v in routing['role_classes'].items()}
    return config


class NamesAndConfigTests(unittest.TestCase):
    def test_canonical_names(self):
        self.assertEqual((canonical_role('researcher'), canonical_role('operator')), ('planner', 'operator'))
        self.assertEqual(canonical_status('RESEARCHING'), 'PLANNING')
        self.assertEqual(RoleName.RESEARCHER.value, 'planner')  # legacy enum alias
        self.assertEqual(role_turn_budget({}, 'researcher'), role_turn_budget({}, 'planner'))
        self.assertEqual(role_turn_budget({'role_turn_budget': {'researcher': 7}}, 'planner'), 7)

    def test_legacy_config_loads_as_planner_with_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            config = validate_config(legacy_config())
        self.assertTrue(any(issubclass(w.category, DeprecationWarning) for w in caught))
        self.assertIn('planner', config['roles'])
        self.assertNotIn('researcher', config['roles'])
        self.assertEqual(config['roles']['planner']['prompt_file'], 'roles/planner.md')
        self.assertEqual(config['quota_routing']['role_classes']['planner'], 'planner')
        self.assertTrue((ROOT / config['roles']['planner']['prompt_file']).exists())

    def test_identical_duplicate_merges_and_conflict_fails(self):
        same = {'roles': {'researcher': {'provider': 'p'}, 'planner': {'provider': 'p'}}}
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            self.assertEqual(list(normalize_config(same)['roles']), ['planner'])
            with self.assertRaises(ValueError):
                normalize_config({'roles': {'researcher': {'provider': 'a'}, 'planner': {'provider': 'b'}}})

    def test_example_config_has_no_legacy_role_keys(self):
        self.assertNotIn('researcher', EXAMPLE['roles'])
        self.assertNotIn('researcher', EXAMPLE['quota_routing']['role_classes'])
        self.assertFalse((ROOT / 'roles/researcher.md').exists())


class PlanBlockTests(unittest.TestCase):
    def test_both_block_names_parse_and_requires_is_canonical(self):
        body = json.dumps({'mode': 'direct', 'checks': [], 'requires': {'researcher': ['codex']}})
        for name in ('planner_plan', 'research_plan'):
            plan = parse_plan(f'```{name}\n{body}\n```')
            self.assertEqual(plan['requires'], {'planner': ['codex']})


class Runner:
    def __init__(self):
        self.calls = []

    def run_role(self, role, **kwargs):
        self.calls.append(role)
        output = {'validator': 'VERDICT: PASS\nEvidence: inspected'}.get(role, 'done')
        return AgentRunResult(role, 'fake', True, output, structured_data={'usage': {'input_tokens': 1, 'output_tokens': 1}})


class StoredLegacyTaskTests(unittest.TestCase):
    def test_task_stored_as_researching_is_claimed_and_runs_planner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {k: EXAMPLE[k] for k in ('providers', 'roles')}
            config.update(logs_dir='logs', isolation={'enabled': False})
            db = WorkspaceDatabase(root / 'db')
            runner = Runner()
            orch = HybridOrchestrator(root, config, db, ContextLogManager(root / 'logs'), runner)
            task = orch.submit_task('legacy row')
            with db._connect() as c:  # a row written by the previous release
                c.execute("UPDATE tasks SET status='RESEARCHING', current_role='researcher' WHERE id=?", (task.id,))
            db.log_event('ROLE_SPAWNED', 'researcher', 'historical event', task_id=task.id)
            done = orch.run_task_to_completion(task.id)
            self.assertEqual(done.status, 'COMPLETED')
            self.assertEqual(runner.calls[0], 'planner')
            self.assertNotIn('researcher', runner.calls)
            history = [e for e in db.get_recent_events(limit=200) if e.get('summary') == 'historical event' and e.get('actor') == 'researcher']
            self.assertEqual(len(history), 1)  # history is never rewritten


class RoleMemoryMigrationTests(unittest.TestCase):
    def test_history_copied_once_session_not_resumed_legacy_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / '.workflow/roles/researcher'
            legacy.mkdir(parents=True)
            (legacy / 'state.json').write_text(json.dumps({'identity': 'old', 'session_id': 'ses_old', 'passes': 3, 'generation': 2}), encoding='utf-8')
            (legacy / 'history.jsonl').write_text('{"output": "old brief"}\n', encoding='utf-8')
            before = sorted(p.name for p in legacy.iterdir())
            memory = RoleMemory(tmp, 'planner')
            with memory.locked():
                state = memory.load({'provider': 'x'})
            self.assertEqual(state['session_id'], '')
            self.assertIn('old brief', memory.context())
            self.assertTrue((memory.directory / 'migrated_from.json').exists())
            self.assertEqual(sorted(p.name for p in legacy.iterdir()), before)
            # Legacy constructor name maps to the same canonical directory; no second copy.
            again = RoleMemory(tmp, 'researcher')
            self.assertEqual(again.directory, memory.directory)
            with again.locked():
                again.load({'provider': 'x'})
            self.assertEqual(memory.context().count('old brief'), 1)


class PlannerChainTests(unittest.TestCase):
    """D3: Codex -> Claude Opus -> AGY Argon high, strictly in that order."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = QuotaStore(WorkspaceDatabase(Path(self.tmp.name) / 'db'))
        self.config = json.loads(json.dumps(EXAMPLE))
        self.config['quota_routing']['enabled'] = True

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, account, used, resets_in=3600):
        self.store.put([Snapshot(account, 'five_hour', used, time.time() + resets_in, 'fixture', time.time())])

    def route(self, prefer=None):
        decision = QuotaRouter(self.config, self.store).select('planner', prefer=prefer)
        return decision.provider, decision.model

    def test_order_is_fixed_regardless_of_headroom_or_sticky(self):
        self.put('claude', 1.0, resets_in=60)   # more headroom and a fresher window than codex
        self.put('codex', 70.0, resets_in=17000)
        self.assertEqual(self.route(), ('codex_cli', 'gpt-6-astra'))
        self.assertEqual(self.route(prefer=('claude_cli', 'opus')), ('codex_cli', 'gpt-6-astra'))

    def test_falls_through_the_chain(self):
        self.put('codex', 100.0)
        self.assertEqual(self.route(), ('claude_cli', 'opus'))
        self.put('claude', 100.0)
        self.assertEqual(self.route(), ('agy_headless_safe', 'gemini-4-argon-high'))


if __name__ == '__main__':
    unittest.main()
