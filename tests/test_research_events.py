import json
import tempfile
import unittest
from pathlib import Path
from core.database import WorkspaceDatabase
from core.orchestrator import HybridOrchestrator
from core.logger import ContextLogManager
from test_workflow import FakeRunner


class ResearchEventTests(unittest.TestCase):
    def test_unexpected_result_schedules_bounded_research_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            from core.models import AgentRunResult
            root=Path(tmp);db=WorkspaceDatabase(root/'db');runner=FakeRunner()
            orch=HybridOrchestrator(root,{'isolation':{'enabled':False}},db,ContextLogManager(root/'logs'),runner)
            task=orch.submit_task('research');task=db.update_task(task.id,status='OPERATING',workspace_path=str(root))
            runner.run_role=lambda role,**kw:AgentRunResult(role,'fake',True,'RESEARCH_REVIEW: unexpected loss divergence')
            updated=orch.process_next_step(task)
            self.assertEqual(updated.status,'RESEARCHING')
            self.assertEqual(updated.event_review_count,1)
            self.assertEqual(updated.retry_count,0)

    def test_disabled_rental_backend_waits_without_running_operator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db=WorkspaceDatabase(root/'db');runner=FakeRunner()
            orch=HybridOrchestrator(root,{},db,ContextLogManager(root/'logs'),runner)
            task=orch.submit_task('remote');task=db.update_task(task.id,status='OPERATING',plan_json='{"backend":"runpod"}')
            self.assertEqual(orch.process_next_step(task).status,'WAITING_INPUT')
            self.assertFalse(runner.calls)

    def test_verified_milestones_trigger_sequential_review_and_versioned_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db=WorkspaceDatabase(root/'db')
            plan={'mode':'steps','steps':[{'instructions':'first'},{'instructions':'second'}]}
            runner=FakeRunner(plan='```research_plan\n'+json.dumps(plan)+'\n```')
            orch=HybridOrchestrator(root,{'isolation':{'enabled':False}},db,ContextLogManager(root/'logs'),runner)
            task=orch.run_task_to_completion(orch.submit_task('two milestones').id)
            self.assertEqual(task.status,'COMPLETED')
            self.assertEqual(runner.calls,['researcher','operator','validator','researcher','operator','validator','communicator'])
            self.assertEqual(task.plan_revision,2)
            self.assertTrue((root/'.workflow/plans/plan_0001.json').exists())
            self.assertTrue((root/'.workflow/plans/plan_0002.json').exists())
            state=json.loads((root/'.workflow/state.json').read_text())
            self.assertIn('controller_facts',state)
            self.assertIn('model_notes_are_unverified',state)
