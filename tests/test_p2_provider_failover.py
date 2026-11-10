"""P2.4-02: quota hit -> handoff -> next provider at the Ralph boundary, without burning retries."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.models import AgentRunResult
from core.orchestrator import HybridOrchestrator

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = json.loads((ROOT / 'config/workflow_config.example.json').read_text(encoding='utf-8'))


class Runner:
    """Operator fails once on Codex with a quota error after a tool ran; everything else succeeds."""

    def __init__(self):
        self.calls, self.prompts, self.failed = [], [], False

    def run_role(self, role, task_prompt='', cli_log_path=None, provider_override=None, model_override=None):
        self.calls.append((role, provider_override, model_override))
        self.prompts.append(task_prompt)
        if role == 'operator' and provider_override == 'codex_cli' and not self.failed:
            self.failed = True
            return AgentRunResult(role, 'codex_cli', False, 'wrote part1.txt', error='Provider quota exhausted (usage limit)',
                                  structured_data={'native_tools': ['write'], 'run_id': 'r-codex',
                                                   'quota': {'exhausted': True, 'matched': 'usage limit',
                                                             'resets_at': time.time() + 7200, 'account': 'codex'}})
        output = {'validator': 'VERDICT: PASS\nEvidence: inspected'}.get(role, 'done')
        return AgentRunResult(role, provider_override or 'p', True, output,
                              structured_data={'usage': {'input_tokens': 1, 'output_tokens': 1}, 'run_id': f'{role}-{len(self.calls)}'})


class FailoverTests(unittest.TestCase):
    def test_quota_hit_fails_over_at_boundary_with_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {k: json.loads(json.dumps(EXAMPLE[k])) for k in ('providers', 'roles', 'quota_routing')}
            config.update(logs_dir='logs', isolation={'enabled': True, 'directory': str(root / 'ws')})
            config['quota_routing']['enabled'] = True
            # Make Codex the operator's first choice so the failover target is Claude.
            config['quota_routing']['classes']['worker'] = [{'provider': 'codex_cli', 'model': 'gpt-6.1-sol'},
                                                            {'provider': 'claude_cli', 'model': 'sonnet'}]
            config['quota_routing']['ordered_classes'] = ['planner', 'worker']
            db = WorkspaceDatabase(root / 'db')
            runner = Runner()
            orch = HybridOrchestrator(root, config, db, ContextLogManager(root / 'logs'), runner)
            task = orch.run_task_to_completion(orch.submit_task('two-part job').id)
            self.assertEqual(task.status, 'WAITING_PROVIDER')
            wait = orch.control.waits()[0]
            self.assertEqual(wait['dependency'], '')  # not waiting for the Codex reset
            self.assertLess(wait['due'] - time.time(), 60)
            self.assertEqual(db.get_task(task.id).retry_count, 0)
            handoff_path = Path(task.workspace_path) / '.workflow' / 'failover' / 'operator.json'
            handoff = json.loads(handoff_path.read_text(encoding='utf-8'))
            extra = handoff['extra']
            self.assertEqual((extra['failed_provider'], extra['account'], extra['tools_observed']),
                             ('codex_cli', 'codex', ['write']))
            self.assertEqual(handoff['completed_actions'], ['tool write ran'])  # standard handoff schema
            self.assertTrue(handoff['unresolved_effects'])
            self.assertTrue([e for e in db.get_recent_events(limit=100) if e['event_type'] == 'QUOTA_FAILOVER'])
            with db._connect() as c:
                c.execute('UPDATE task_waits SET due=0')
            orch.control.advance_waits()
            done = orch.run_task_to_completion(task.id)
            self.assertEqual((done.status, done.retry_count), ('COMPLETED', 0))
            operator_calls = [c for c in runner.calls if c[0] == 'operator']
            self.assertEqual([c[1] for c in operator_calls], ['codex_cli', 'claude_cli'])
            continuation = runner.prompts[runner.calls.index(operator_calls[1])]
            self.assertIn('stopped at a provider quota limit', continuation)
            self.assertFalse(handoff_path.exists())  # archived after the successful continuation
            self.assertTrue(list(handoff_path.parent.glob('operator.*.consumed.json')))


if __name__ == '__main__':
    unittest.main()
