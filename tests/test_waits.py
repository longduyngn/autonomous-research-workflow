import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from core.database import WorkspaceDatabase
from core.control import ControlPlane
from core.orchestrator import HybridOrchestrator
from core.logger import ContextLogManager
from test_workflow import FakeRunner


class WaitTests(unittest.TestCase):
    def test_first_http_rate_limit_is_safe_but_after_tools_is_not(self):
        from core.providers import EphemeralAgentRunner, ModelHTTPError
        with tempfile.TemporaryDirectory() as tmp:
            runner=EphemeralAgentRunner(tmp,{})
            provider={'base_url':'https://fixture.invalid','supports_tools':True,'require_api_key':False,'default_model':'fixture'}
            # _post converts urllib errors into ModelHTTPError (allow-listed fields only).
            error=ModelHTTPError(429,30.0,'slow down')
            with patch.object(runner,'_post',side_effect=error):
                result=runner._run_http('operator','fixture',provider,{},'','request',60,None)
            self.assertTrue(result.structured_data['retry_safe'])
            self.assertEqual(result.structured_data['retry_after'],30)
            first={'choices':[{'message':{'tool_calls':[{'id':'call1','function':{'name':'list_files','arguments':'{}'}}]}}]}
            with patch.object(runner,'_post',side_effect=[first,error]):
                result=runner._run_http('operator','fixture',provider,{},'','request',60,None)
            # After a tool ran: no replay flag, a reconciliation checkpoint instead.
            self.assertFalse(result.success)
            self.assertNotIn('retry_safe',result.structured_data)
            self.assertTrue(result.structured_data['reconcile_required'])
            self.assertTrue(Path(result.structured_data['handoff']).exists())

    def test_crash_after_wait_commit_releases_stale_lease_without_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=WorkspaceDatabase(Path(tmp)/'db');cp=ControlPlane(db)
            task=db.create_task('remote');db.claim_task('dead',1)
            cp.wait(task.id,'REMOTE','WAITING_REMOTE','remote','job',delay=100)
            with db._connect() as c:c.execute('UPDATE tasks SET lease_expires=0')
            self.assertIsNone(db.claim_task('new',1))
            self.assertEqual(db.get_task(task.id).status,'WAITING_REMOTE')
            self.assertFalse(db.get_task(task.id).lease_owner)

    def test_remote_wait_frees_slot_and_resumes_without_operator_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db=WorkspaceDatabase(root/'db');runner=FakeRunner()
            orch=HybridOrchestrator(root,{'isolation':{'enabled':False},'cloud':{'poll_interval_seconds':0}},db,ContextLogManager(root/'logs'),runner)
            t=orch.submit_task('remote')
            db.update_task(t.id,status='WAITING_REMOTE',remote_job_id='a'*32,workspace_path=str(root))
            with patch('core.cloud.CloudTools.kaggle_job_status',return_value={'state':'running'}):
                result=orch.run_task_to_completion(t.id)
            self.assertEqual(result.status,'WAITING_REMOTE')
            self.assertFalse(db.get_task(t.id).lease_owner)
            other=db.create_task('local')
            self.assertEqual(db.claim_task('other',1).id,other.id)
            db.release_lease(other.id,'other')
            start=db.get_task(t.id).started_at
            ControlPlane(WorkspaceDatabase(root/'db')).advance_waits()
            self.assertEqual(db.get_task(t.id).started_at,start)
            with patch('core.cloud.CloudTools.kaggle_job_status',return_value={'state':'complete'}),patch('core.cloud.CloudTools.kaggle_export_data',return_value={'status':'completed'}):
                result=orch.run_task_to_completion(t.id)
            self.assertEqual(result.status,'COMPLETED')
            self.assertNotIn('operator',runner.calls)

    def test_provider_wait_and_expired_approval_do_not_spend_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db=WorkspaceDatabase(root/'db');cp=ControlPlane(db)
            t=db.create_task('task');cp.cool_down('provider',30,'quota')
            cp.wait(t.id,'PROVIDER','PLANNING','quota','provider',0)
            cp.advance_waits()
            self.assertIsNone(db.claim_task('worker',2))
            with db._connect() as c:c.execute('UPDATE provider_cooldowns SET until_time=0')
            cp.advance_waits()
            self.assertEqual(db.get_task(t.id).status,'PLANNING')
            key=cp.request_approval(t.id,'owner',{'argv':['a'],'revision':'b'})
            cp.wait(t.id,'APPROVAL','OPERATING','approval',key,0)
            with db._connect() as c:c.execute('UPDATE approvals SET expires=0')
            cp.advance_waits()
            self.assertEqual(db.get_task(t.id).status,'ESCALATED')
            self.assertEqual(db.get_task(t.id).retry_count,0)
