import tempfile
import unittest
from pathlib import Path
from core.database import WorkspaceDatabase
from core.control import ControlPlane
from core.approvals import command_decision
from core.orchestrator import HybridOrchestrator
from core.logger import ContextLogManager
from core.models import AgentRunResult
from test_workflow import FakeRunner


class ApprovalTests(unittest.TestCase):
    def test_native_hook_denial_and_one_matching_approved_invocation(self):
        from unittest.mock import patch
        from scripts.agent_hooks import handle
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);work=root/'work';work.mkdir()
            db=WorkspaceDatabase(root/'db');cp=ControlPlane(db)
            task=db.create_task('task',source_user='owner');db.update_task(task.id,workspace_path=str(work))
            payload={'toolCall':{'name':'run_command','args':{'CommandLine':'shutdown /s'}}}
            with patch.dict('os.environ',{'WORKFLOW_DB_PATH':str(db.db_path),'WORKFLOW_TASK_ID':str(task.id)}):
                self.assertEqual(handle('agy','PreToolUse',payload,work)['decision'],'deny')
                with db._connect() as c:key=c.execute('SELECT id FROM approvals').fetchone()[0]
                cp.decide(key,'owner','APPROVED')
                self.assertEqual(handle('agy','PreToolUse',payload,work)['decision'],'allow')
                self.assertEqual(handle('agy','PreToolUse',payload,work)['decision'],'deny')

    def test_exact_action_source_revision_and_one_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);workspace=root/'work';workspace.mkdir()
            db=WorkspaceDatabase(root/'db');cp=ControlPlane(db)
            task=db.create_task('task',source_user='owner')
            db.update_task(task.id,workspace_path=str(workspace))
            source=workspace/'script.py';source.write_text('version1')
            args={'command':'python script.py'}
            self.assertFalse(command_decision(db.db_path,task.id,workspace,'Bash',args)[0])
            with db._connect() as c:key=c.execute('SELECT id FROM approvals').fetchone()[0]
            self.assertFalse(cp.decide(key,'intruder','APPROVED'))
            self.assertTrue(cp.decide(key,'owner','APPROVED'))
            source.write_text('version2')
            self.assertFalse(command_decision(db.db_path,task.id,workspace,'Bash',args)[0])
            self.assertEqual(cp.approval(key)['decision'],'SUPERSEDED')
            with db._connect() as c:key=c.execute("SELECT id FROM approvals WHERE decision='PENDING'").fetchone()[0]
            cp.decide(key,'owner','APPROVED')
            self.assertTrue(command_decision(db.db_path,task.id,workspace,'Bash',args)[0])
            self.assertFalse(command_decision(db.db_path,task.id,workspace,'Bash',args)[0])

    def test_role_wait_then_approved_resume_with_no_retry_spent(self):
        class Runner(FakeRunner):
            def run_role(self,role,**kwargs):
                self.calls.append(role)
                if role=='operator':
                    allowed,_=command_decision(db.db_path,task.id,root,'Bash',{'command':'restricted operation'})
                    return AgentRunResult(role,'fake',allowed,'executed' if allowed else 'waiting')
                return AgentRunResult(role,'fake',True,'VERDICT: PASS\nEvidence checked' if role=='validator' else 'done')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db=WorkspaceDatabase(root/'state/db');runner=Runner()
            # Keep controller DB/logs outside the fingerprinted task directory.
            work=root/'work';work.mkdir()
            orch=HybridOrchestrator(work,{'isolation':{'enabled':False}},db,ContextLogManager(root/'logs'),runner)
            task=orch.submit_task('task',source_user='owner');root=work
            result=orch.run_task_to_completion(task.id)
            self.assertEqual(result.status,'WAITING_APPROVAL')
            self.assertEqual(db.get_task(task.id).retry_count,0)
            with db._connect() as c:key=c.execute('SELECT id FROM approvals').fetchone()[0]
            self.assertTrue(orch.control.decide(key,'owner','APPROVED'))
            orch.control.advance_waits()
            result=orch.run_task_to_completion(task.id)
            self.assertEqual(result.status,'COMPLETED',result.validation_feedback)
