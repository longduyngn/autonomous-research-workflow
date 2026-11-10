"""P2.6-02 (D6): no default task/job/agent time budget; budgets apply only when the user states one."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from core.configuration import validate_config
from core.control import ControlPlane
from core.database import WorkspaceDatabase
from core.experiment_progress import ExperimentProgress
from core.logger import ContextLogManager
from core.monitoring import ProgressStore
from core.orchestrator import HybridOrchestrator
from core.plans import parse_plan
from test_workflow import FakeRunner

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = json.loads((ROOT / 'config/workflow_config.example.json').read_text(encoding='utf-8'))
HOURS = (6, 10, 12, 24, 30)


class DefaultsTests(unittest.TestCase):
    def test_example_config_has_no_default_budgets(self):
        self.assertEqual(EXAMPLE['orchestrator']['task_timeout_seconds'], 0)
        self.assertEqual(EXAMPLE['monitoring']['max_agent_seconds'], 0)
        validate_config(json.loads(json.dumps(EXAMPLE)))
        with self.assertRaises(ValueError):
            validate_config({'orchestrator': {'task_timeout_seconds': -1}})

    def test_plan_budget_only_when_stated(self):
        self.assertNotIn('budget_seconds', parse_plan('```planner_plan\n{"mode":"direct","checks":[]}\n```'))
        plan = parse_plan('```planner_plan\n{"mode":"direct","checks":[],"budget_seconds":7200}\n```')
        self.assertEqual(plan['budget_seconds'], 7200)
        with self.assertRaises(ValueError):
            parse_plan('```planner_plan\n{"mode":"direct","checks":[],"budget_seconds":0}\n```')


class TaskBudgetTests(unittest.TestCase):
    def orch(self, root, extra=None):
        config = {'logs_dir': 'logs', 'isolation': {'enabled': False}, **(extra or {})}
        db = WorkspaceDatabase(root / 'db')
        return HybridOrchestrator(root, config, db, ContextLogManager(root / 'logs'), FakeRunner()), db

    def test_long_running_task_without_budget_is_never_cut(self):
        for hours in HOURS:
            with self.subTest(hours=hours), tempfile.TemporaryDirectory() as tmp:
                orch, db = self.orch(Path(tmp))
                task = orch.submit_task('long job')
                db.update_task(task.id, started_at=time.time() - hours * 3600)
                self.assertEqual(orch._task_budget(db.get_task(task.id)), 0)
                done = orch.run_task_to_completion(task.id)
                self.assertEqual(done.status, 'COMPLETED')

    def test_configured_and_plan_budgets_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            orch, db = self.orch(Path(tmp), {'orchestrator': {'task_timeout_seconds': 3600}})
            task = orch.submit_task('bounded')
            self.assertEqual(orch._task_budget(task), 3600)
            db.update_task(task.id, plan_json=json.dumps({'mode': 'direct', 'budget_seconds': 600}))
            self.assertEqual(orch._task_budget(db.get_task(task.id)), 600)  # the stricter user budget wins


class WaitAndJobTests(unittest.TestCase):
    def test_remote_wait_without_timeout_has_no_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = WorkspaceDatabase(Path(tmp) / 'db')
            control = ControlPlane(db)
            task = db.create_task('remote')
            control.wait(task.id, 'REMOTE', 'OPERATING', 'job running', 'job-1', delay=10 ** 9, timeout=None)
            self.assertEqual(db.get_task(task.id).wait_deadline, 0)
            with patch('core.control.time.time', return_value=time.time() + 30 * 3600):
                control.advance_waits()
            self.assertEqual(db.get_task(task.id).status, 'WAITING_REMOTE')  # not escalated after 30 h

    def test_job_without_budget_is_not_over_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = WorkspaceDatabase(Path(tmp) / 'db')
            task = db.create_task('train')
            store = ProgressStore(db)
            job, _ = store.register(task.id, 'r1', 'local', now=1000.0)
            self.assertEqual(job['budget_until'], 0)
            other = db.create_task('bounded train')  # one active job per task
            bounded, _ = store.register(other.id, 'r2', 'local', budget_seconds=3600, now=1000.0)
            self.assertEqual(bounded['budget_until'], 4600.0)

    def test_progress_tracker_defaults_to_no_budget(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {}, clear=False):
            import os
            os.environ.pop('WORKFLOW_JOB_BUDGET_SECONDS', None)
            tracker = ExperimentProgress('job1', tmp, mode='disabled', heartbeat_seconds=0)
            self.assertEqual(tracker.budget_seconds, 0)


if __name__ == '__main__':
    unittest.main()
