import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from core.cloud import CloudTools
from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.models import AgentRunResult
from core.orchestrator import HybridOrchestrator
from core.processes import run_process
from core.supervision import extension_for_progress
from test_workflow import FakeRunner


class SupervisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=WorkspaceDatabase(self.root/'events.db')
        self.runner=FakeRunner()
        self.orch=HybridOrchestrator(self.root,{'isolation':{'enabled':False}},self.db,ContextLogManager(self.root/'logs'),self.runner)
        self.task=self.orch.submit_task('Full original cohort, do not weaken it')
        self.db.update_task(self.task.id,status='OPERATING',workspace_path=str(self.root))
        self.store=self.orch.supervisor.store

    def job(self,**kw):
        return self.store.register(self.task.id,'stage1','colab',external_id='cell',total=10,**kw)[0]

    def test_timeout_after_submission_keeps_job_and_experiment_attempt(self):
        def run(role,**kwargs):
            self.job()
            return AgentRunResult(role,'fixture',False,'',error='Timed out after 1800s')
        self.runner.run_role=run
        task=self.orch._execute_operator(self.db.get_task(self.task.id))
        self.assertEqual((task.status,task.retry_count),('WAITING_REMOTE',0))
        self.assertTrue(task.remote_job_id)
        with self.db._connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM message_outbox').fetchone()[0],0)

    def test_ambiguous_unregistered_timeout_does_not_blindly_retry(self):
        self.runner.run_role=lambda role,**kw:AgentRunResult(role,'fixture',False,'',error='Timed out after 1800s')
        task=self.orch._execute_operator(self.db.get_task(self.task.id))
        self.assertEqual((task.status,task.retry_count),('PLANNING',0))
        with self.db._connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM message_outbox').fetchone()[0],0)
        self.orch.supervisor.notice(task,'Repeated reason')
        self.orch.supervisor.notice(task,'Repeated reason')
        with self.db._connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM message_outbox').fetchone()[0],1)

    def test_budget_stop_does_not_count_as_failed_experiment_retry(self):
        job=self.job(now=time.time()-10,budget_seconds=1)
        self.store.observe(job['id'],{'state':'FAILED'})
        task=self.orch._poll_remote(self.db.get_task(self.task.id))
        self.assertEqual((task.status,task.retry_count),('WAITING_INPUT',0))

    def test_restart_existing_job_never_launches_operator_again(self):
        job=self.job()
        self.store.observe(job['id'],{'state':'SUBMISSION_UNKNOWN'})
        task=self.orch._execute_operator(self.db.get_task(self.task.id))
        self.assertEqual(task.status,'WAITING_REMOTE');self.assertEqual(self.runner.calls,[])

    def test_finished_compute_needs_download_and_recording_before_validation(self):
        job=self.job()
        self.store.observe(job['id'],{'completed':10,'state':'COMPLETED'})
        task=self.orch._poll_remote(self.db.get_task(self.task.id))
        self.assertEqual(task.status,'OPERATING')
        task=self.orch._execute_operator(task)
        self.assertEqual(task.status,'WAITING_REMOTE')
        self.assertNotIn('validator',self.runner.calls)

    def test_colab_launch_claim_precedes_effect_and_retry_does_not_click_again(self):
        job=self.job();cloud=CloudTools(self.root)
        with patch.object(cloud,'_progress_context',return_value=(self.store,self.task.id)),patch.object(cloud,'_colab',side_effect=TimeoutError) as call:
            with self.assertRaises(TimeoutError):cloud.colab_job_start({'job_id':job['id']})
            self.assertEqual(self.store.get(job['id'])['state'],'SUBMISSION_UNKNOWN')
            result=cloud.colab_job_start({'job_id':job['id']})
            self.assertEqual(result['status'],'not_resubmitted');self.assertEqual(call.call_count,1)

    def test_artifacts_cannot_hide_incomplete_cohort(self):
        job=self.job();cloud=CloudTools(self.root)
        self.store.observe(job['id'],{'completed':3,'state':'COMPLETED'})
        (self.root/'metrics.json').write_text('{"n":3}')
        with patch.object(cloud,'_progress_context',return_value=(self.store,self.task.id)):
            with self.assertRaisesRegex(ValueError,'cohort'):
                cloud.experiment_job_artifacts({'job_id':job['id'],'files':['metrics.json']})

    def test_recorded_download_unbinds_job_but_does_not_complete_task(self):
        job=self.job();self.store.observe(job['id'],{'completed':10,'state':'COMPLETED'})
        (self.root/'metrics.json').write_text('{"n":10}')
        cloud=CloudTools(self.root)
        with patch.object(cloud,'_progress_context',return_value=(self.store,self.task.id)):
            result=cloud.experiment_job_artifacts({'job_id':job['id'],'files':['metrics.json']})
        task=self.db.get_task(self.task.id)
        self.assertFalse(task.remote_job_id);self.assertNotEqual(task.status,'COMPLETED')
        self.assertEqual(result['status'],'artifacts_downloaded_unvalidated')

    def test_lease_extensions_require_work_not_log_or_heartbeat(self):
        job=self.job(now=100)
        self.store.observe(job['id'],{'completed':0,'heartbeat_at':200},now=200)
        self.assertEqual(extension_for_progress(self.root,self.db.db_path,self.task.id,now=200),0)
        self.store.observe(job['id'],{'completed':1,'heartbeat_at':201},now=201)
        self.assertEqual(extension_for_progress(self.root,self.db.db_path,self.task.id,now=202),300)

    def test_controller_crash_before_wait_commit_restores_polling_not_replay(self):
        claimed=self.db.claim_task('old',2,task_id=self.task.id)
        job=self.job()
        self.store.observe(job['id'],{'state':'SUBMISSION_UNKNOWN'})
        with self.db._connect() as c:c.execute('UPDATE tasks SET lease_expires=0 WHERE id=?',(self.task.id,))
        self.assertIsNone(self.db.claim_task('new',2,task_id=self.task.id))
        restored=self.db.get_task(self.task.id)
        self.assertEqual(restored.status,'WAITING_REMOTE');self.assertEqual(restored.retry_count,0)
        self.assertEqual(restored.remote_job_id,job['id']);self.assertFalse(restored.lease_owner)
        from core.control import check_worker_lease
        with self.assertRaises(PermissionError):
            check_worker_lease({'WORKFLOW_WORKER_OWNER':'old','WORKFLOW_DB_PATH':str(self.db.db_path),'WORKFLOW_TASK_ID':str(self.task.id)})

    def test_process_soft_deadline_renews_with_progress_but_hard_limit_remains(self):
        calls=[]
        result=run_process([sys.executable,'-c','import time;time.sleep(.2)'],self.root,timeout=.05,
                           timeout_callback=lambda:calls.append(1) or .1,hard_timeout=1)
        self.assertEqual(result['exit_code'],0);self.assertTrue(calls)
        result=run_process([sys.executable,'-c','import time;time.sleep(5)'],self.root,timeout=.05,
                           timeout_callback=lambda:.1,hard_timeout=.2)
        self.assertEqual(result['exit_code'],-1)

    def test_checkin_grace_precedes_model_and_stale_nonce_is_rejected(self):
        self.job(now=time.time()-1000)
        with patch.object(self.orch.supervisor,'_consult') as consult:
            self.orch.supervisor.diagnose();consult.assert_not_called()
        path=self.root/'.workflow/checkin-request.json'
        request=json.loads(path.read_text());cloud=CloudTools(self.root)
        with self.assertRaises(ValueError):cloud.agent_checkin({'nonce':'wrong','stage':'detect','completed':1,'blocking_on':'gpu'})
        cloud.agent_checkin({'nonce':request['nonce'],'stage':'detect','completed':1,'blocking_on':'gpu'})
        self.assertEqual(json.loads(path.with_name('checkin-response.json').read_text())['source'],'agent_self_report_unverified')

    def test_confirmed_failure_needs_compatible_checkpoint_before_new_job(self):
        from core.checkpoints import CheckpointStore
        job=self.job();self.store.observe(job['id'],{'state':'FAILED'})
        (self.root/'state.json').write_text('{"step":2}')
        checkpoints=CheckpointStore(self.root/'checkpoints')
        meta={'job_id':job['id'],'step':2,'code_sha256':'a'*64,'data_sha256':'b'*64,'config_sha256':'c'*64}
        manifest=checkpoints.publish(self.root,['state.json'],meta)
        cloud=CloudTools(self.root)
        with patch.object(cloud,'_progress_context',return_value=(self.store,self.task.id)),patch.object(cloud,'_checkpoint_store',return_value=checkpoints):
            with self.assertRaises(ValueError):self.store.register(self.task.id,'retry','colab',total=10)
            result=cloud.experiment_job_resume_checkpoint({'job_id':job['id'],'checkpoint_id':manifest['id']})
        self.assertEqual(result['status'],'checkpoint_verified')
        new,created=self.store.register(self.task.id,'retry','colab',total=10)
        self.assertTrue(created);self.assertNotEqual(new['id'],job['id'])

    def test_diagnostic_hook_cannot_execute_even_harmless_shell(self):
        from scripts.agent_hooks import handle
        with patch.dict('os.environ',{'WORKFLOW_DIAGNOSTIC_READONLY':'1'}):
            result=handle('agy','PreToolUse',{'toolCall':{'name':'run_command','args':{'CommandLine':'echo hi'}}},self.root)
        self.assertEqual(result['decision'],'deny')

    def test_kaggle_submission_is_bound_before_push_and_idempotent(self):
        (self.root/'run.ipynb').write_text(json.dumps({'nbformat':4,'nbformat_minor':5,'cells':[],'metadata':{}}))
        cloud=CloudTools(self.root,{'database_path':str(self.db.db_path.resolve())})
        def push(argv,**kwargs):
            current=self.db.get_task(self.task.id)
            self.assertTrue(current.remote_job_id)
            self.assertEqual(self.store.get(current.remote_job_id)['state'],'SUBMISSION_UNKNOWN')
            return 'https://www.kaggle.com/code/owner/experiment'
        with patch.object(cloud,'_kaggle',side_effect=push) as submit:
            first=cloud.kaggle_execute_notebook({'notebook_path':'run.ipynb','hardware':'CPU','kaggle_kernel_slug':'owner/experiment','total':2})
            second=cloud.kaggle_execute_notebook({'notebook_path':'run.ipynb','hardware':'CPU','kaggle_kernel_slug':'owner/experiment','total':2})
        self.assertTrue(second['reused_submission']);self.assertEqual(submit.call_count,1)
        self.assertEqual(first['tracking_id'],self.db.get_task(self.task.id).remote_job_id)

    def test_end_to_end_handoff_restart_download_then_validation(self):
        root,store,db=self.root,self.store,self.db
        calls=[]
        class Runner(FakeRunner):
            def run_role(inner,role,**kwargs):
                calls.append(role)
                if role=='operator':
                    if calls.count('operator')==1:
                        job,_=store.register(self.task.id,'integration','local',total=2)
                        path=root/'.workflow/progress'/(job['id']+'.json');path.parent.mkdir(parents=True,exist_ok=True)
                        path.write_text(json.dumps({'job_id':job['id'],'completed':1,'total':2,'stage':'testing','state':'RUNNING'}))
                    else:
                        task=db.get_task(self.task.id)
                        (root/'metrics.json').write_text('{"documents":2}')
                        cloud=CloudTools(root,{'database_path':str(db.db_path.resolve())})
                        cloud.experiment_job_artifacts({'job_id':task.remote_job_id,'files':['metrics.json']})
                return AgentRunResult(role,'fixture',True,'VERDICT: PASS\nEvidence: harness checked full cohort' if role=='validator' else 'done')
        runner=Runner();cfg={'isolation':{'enabled':False},'database_path':str(db.db_path.resolve())}
        orch=HybridOrchestrator(root,cfg,db,ContextLogManager(root/'logs'),runner)
        first=orch.run_task_to_completion(self.task.id)
        self.assertEqual(first.status,'WAITING_REMOTE');self.assertEqual(first.retry_count,0)
        job=store.get(first.remote_job_id)
        (root/'.workflow/progress'/(job['id']+'.json')).write_text(json.dumps(
            {'job_id':job['id'],'completed':2,'total':2,'stage':'testing','state':'COMPLETED'}))
        self.assertTrue(orch.control.wake(self.task.id))
        restarted=HybridOrchestrator(root,cfg,db,ContextLogManager(root/'logs'),runner)
        final=restarted.run_task_to_completion(self.task.id)
        self.assertEqual(final.status,'COMPLETED');self.assertEqual(final.retry_count,0)
        self.assertEqual(calls,['operator','operator','validator','communicator'])
        self.assertEqual(store.get(job['id'])['state'],'ARTIFACTS_READY')


if __name__=='__main__':unittest.main()
