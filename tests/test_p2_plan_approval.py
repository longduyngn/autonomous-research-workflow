"""P2.6-01 (D4'): plan approval only when the user asks; exact revision/hash; no duplicate runs."""
import json
import tempfile
import unittest
from pathlib import Path

from core.approvals import consume_plan_approval, plan_action, request_plan_approval, wants_plan_approval
from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.models import AgentRunResult
from core.orchestrator import HybridOrchestrator

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = json.loads((ROOT / 'config/workflow_config.example.json').read_text(encoding='utf-8'))


class Runner:
    def __init__(self, verdicts=None):
        self.calls, self.verdicts = [], list(verdicts or [])

    def run_role(self, role, **kwargs):
        self.calls.append(role)
        if role == 'validator':
            verdict = self.verdicts.pop(0) if self.verdicts else 'PASS'
            return AgentRunResult(role, 'fake', True, f'VERDICT: {verdict}\nEvidence: inspected')
        return AgentRunResult(role, 'fake', True, 'Plan: write the file and check it' if role == 'planner' else 'done')


class PlanApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = {k: EXAMPLE[k] for k in ('providers', 'roles')}
        self.config.update(logs_dir='logs', isolation={'enabled': False})
        self.db = WorkspaceDatabase(self.root / 'db')

    def tearDown(self):
        self.tmp.cleanup()

    def orch(self, runner):
        return HybridOrchestrator(self.root, self.config, self.db, ContextLogManager(self.root / 'logs'), runner)

    def plan_rows(self, task_id):
        with self.db._connect() as c:
            rows = [dict(r) for r in c.execute('SELECT * FROM approvals WHERE task_id=? ORDER BY created', (task_id,))]
        return [r for r in rows if json.loads(r['action']).get('kind') == 'PLAN_APPROVAL']

    def test_detection_is_explicit(self):
        for text in ('Reproduce DDPM --approve-plan', 'duyệt plan trước khi chạy', 'Hãy chờ tôi duyệt kế hoạch',
                     'please approve the plan first', 'with plan approval'):
            self.assertTrue(wants_plan_approval(text), text)
        for text in ('Reproduce DDPM on Colab', 'approve the PR after tests', 'write a plan.md file'):
            self.assertFalse(wants_plan_approval(text), text)

    def test_default_task_runs_without_approval(self):
        runner = Runner()
        orch = self.orch(runner)
        task = orch.run_task_to_completion(orch.submit_task('write hello').id)
        self.assertEqual(task.status, 'COMPLETED')
        self.assertEqual(task.plan_approval, 'none')
        self.assertEqual(self.plan_rows(task.id), [])

    def test_required_plan_waits_then_runs_once_after_owner_approval(self):
        runner = Runner(verdicts=['FAIL', 'PASS'])  # one technical retry after approval
        orch = self.orch(runner)
        task = orch.run_task_to_completion(orch.submit_task('write hello, duyệt plan trước').id)
        self.assertEqual(task.status, 'WAITING_APPROVAL')
        self.assertEqual(runner.calls, ['planner'])  # operator never ran
        [row] = self.plan_rows(task.id)
        self.assertEqual(json.loads(row['action'])['plan_revision'], task.plan_revision)
        self.assertFalse(orch.control.decide(row['id'], 'someone_else', 'APPROVED'))
        self.assertTrue(orch.control.decide(row['id'], 'local_user', 'APPROVED'))
        orch.control.advance_waits()
        done = orch.run_task_to_completion(task.id)
        self.assertEqual(done.status, 'COMPLETED')
        self.assertEqual(runner.calls.count('planner'), 1)
        self.assertEqual(runner.calls.count('operator'), 2)  # retry of the approved plan did not ask again
        self.assertEqual([r['decision'] for r in self.plan_rows(task.id)], ['CONSUMED'])

    def test_denied_plan_escalates_without_running_operator(self):
        runner = Runner()
        orch = self.orch(runner)
        task = orch.run_task_to_completion(orch.submit_task('x', plan_approval='required').id)
        [row] = self.plan_rows(task.id)
        orch.control.decide(row['id'], 'local_user', 'DENIED')
        orch.control.advance_waits()
        self.assertEqual(self.db.get_task(task.id).status, 'ESCALATED')
        self.assertNotIn('operator', runner.calls)

    def test_changed_plan_needs_new_approval_and_supersedes_old(self):
        runner = Runner()
        orch = self.orch(runner)
        task = orch.run_task_to_completion(orch.submit_task('x', plan_approval='required').id)
        [row] = self.plan_rows(task.id)
        orch.control.decide(row['id'], 'local_user', 'APPROVED')
        orch.control.advance_waits()
        # The plan changes after approval (e.g. feedback replan) before the operator starts.
        self.db.update_task(task.id, research_notes='A different plan', plan_revision=task.plan_revision + 1)
        changed = orch.process_next_step(self.db.get_task(task.id))
        self.assertEqual(changed.status, 'WAITING_APPROVAL')
        self.assertNotIn('operator', runner.calls)
        decisions = [r['decision'] for r in self.plan_rows(task.id)]
        self.assertEqual(decisions, ['SUPERSEDED', 'PENDING'])

    def test_duplicate_request_reuses_pending_and_notifies_once(self):
        runner = Runner()
        orch = self.orch(runner)
        task = orch.submit_task('x', plan_approval='required', source_channel='discord', source_user='chan',
                                source_actor='user-1')
        task = self.db.update_task(task.id, research_notes='plan text', plan_json='{}', plan_revision=1)
        first = request_plan_approval(self.db, orch.control, task)
        second = request_plan_approval(self.db, orch.control, task)
        self.assertEqual(first, second)
        with self.db._connect() as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'intake_messages' in tables:
                messages = c.execute("SELECT response FROM intake_messages WHERE event_key LIKE 'plan-approval:%'").fetchall()
                self.assertEqual(len(messages), 1)
                self.assertIn('!approve ' + first, messages[0][0])
        self.assertEqual(consume_plan_approval(self.db, orch.control, task), 'missing')  # still pending
        self.assertEqual(plan_action(task)['plan_revision'], 1)


if __name__ == '__main__':
    unittest.main()
