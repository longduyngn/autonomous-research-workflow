import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.cloud import CloudTools
from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.models import AgentRunResult
from core.orchestrator import HybridOrchestrator
from core.recovery import RecoveryStore,recovery_summary
from core.tools import AgentTools
from test_workflow import FakeRunner


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.db=WorkspaceDatabase(self.root/'db')
        self.runner=FakeRunner()
        self.config={'logs_dir':'logs','isolation':{'enabled':False},'monitoring':{'max_recovery_reviews':2},
                     'database_path':str(self.db.db_path.resolve())}
        self.orch=HybridOrchestrator(self.root,self.config,self.db,ContextLogManager(self.root/'logs'),self.runner)
        self.task=self.orch.submit_task('Preserve original full cohort and checks')
        self.db.update_task(self.task.id,status='OPERATING',workspace_path=str(self.root))
        self.store=self.orch.supervisor.store

    def job(self,state='RUNNING',now=None):
        job,_=self.store.register(self.task.id,'first','local',total=2,stall_seconds=1,now=now)
        return self.store.observe(job['id'],{'state':state},now=now)

    def poll(self):
        with patch('core.monitoring.poll_job',side_effect=lambda store,job,*args:store.get(job['id'])):
            return self.orch._poll_remote(self.db.get_task(self.task.id))

    def count(self,table):
        with self.db._connect() as c:return c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]

    def claim(self):
        with self.db._connect() as c:c.execute('DELETE FROM task_waits WHERE task_id=?',(self.task.id,))
        task=self.db.claim_task('worker',2,task_id=self.task.id)
        self.assertIsNotNone(task)
        self.orch._worker_local.owner='worker'
        return task

    def test_transient_telemetry_recovers_with_progress_and_no_model_or_notice(self):
        job=self.job(now=time.time()-10)
        self.store.unavailable(job['id'],'TimeoutError')
        self.poll()
        self.assertEqual(self.orch.recovery.active(self.task.id)['phase'],'OBSERVING')
        self.assertFalse(self.runner.calls)
        self.store.observe(job['id'],{'completed':1,'state':'RUNNING'})
        self.poll()
        with self.db._connect() as c:row=c.execute('SELECT * FROM recovery_episodes').fetchone()
        self.assertEqual(row['phase'],'RECOVERED')
        self.assertEqual(json.loads(row['evidence_json'])['completed_after'],1)
        self.assertEqual(self.db.get_task(self.task.id).remote_job_id,job['id'])
        self.assertEqual(self.count('message_outbox'),0)
        self.assertEqual(self.db.get_task(self.task.id).retry_count,0)

    def test_restart_repeated_failure_reuses_episode_and_does_not_spend_retry(self):
        job=self.job('FAILED')
        for _ in range(4):self.poll()
        restarted=HybridOrchestrator(self.root,self.config,self.db,ContextLogManager(self.root/'logs'),self.runner)
        with patch('core.monitoring.poll_job',side_effect=lambda store,job,*args:store.get(job['id'])):
            restarted._poll_remote(self.db.get_task(self.task.id))
        self.assertEqual(self.count('recovery_episodes'),1)
        self.assertEqual(self.db.get_task(self.task.id).retry_count,0)
        self.assertEqual(self.db.get_task(self.task.id).remote_job_id,job['id'])
        self.assertEqual(restarted.recovery.active(self.task.id)['reviews'],0)
        self.assertFalse(self.runner.calls)

    def test_unregistered_timeout_gets_reasoning_before_one_notice(self):
        self.runner.run_role=lambda role,**kw:AgentRunResult(role,'fake',False,'',error='Timed out after 5s')
        first=self.orch._execute_operator(self.db.get_task(self.task.id))
        self.assertEqual(first.status,'PLANNING')
        self.assertEqual(self.count('message_outbox'),0)
        calls=[]
        def review(role,**kw):
            calls.append((role,kw))
            self.assertEqual(self.runner.control_environment['WORKFLOW_DIAGNOSTIC_READONLY'],'1')
            return AgentRunResult(role,'fake',True,json.dumps({'action':'needs_input','reason':'Missing external job identity; cannot prove stopped'}))
        self.runner.run_role=review
        final=self.orch.run_task_to_completion(self.task.id)
        self.assertEqual(final.status,'WAITING_INPUT')
        self.assertEqual([role for role,_ in calls],['planner'])
        self.assertEqual(final.retry_count,0)
        self.assertEqual(self.count('message_outbox'),1)
        self.orch.supervisor.notice(final,'Missing external job identity; cannot prove stopped')
        with self.db._connect() as c:outbox=c.execute('SELECT revision FROM message_outbox').fetchone()
        self.assertEqual(outbox['revision'],1)

    def test_unknown_job_reasoning_preserves_identity_and_waits_for_observation(self):
        job=self.job('SUBMISSION_UNKNOWN',now=time.time()-10)
        self.store.claim_diagnostic(job['id'],'diagnostic')
        self.store.finish_diagnostic(job['id'],'diagnostic',{'assessment':'UNKNOWN','reason':'transport missing'})
        self.poll()
        self.runner.run_role=lambda role,**kw:AgentRunResult(role,'fake',True,json.dumps({'action':'observe','reason':'Re-read registered job status'}))
        result=self.orch.run_task_to_completion(self.task.id)
        self.assertEqual(result.status,'WAITING_REMOTE')
        self.assertEqual(result.remote_job_id,job['id'])
        self.assertEqual(self.store.get(job['id'])['state'],'SUBMISSION_UNKNOWN')
        self.assertEqual(self.orch.recovery.active(self.task.id)['phase'],'VERIFYING')
        self.assertEqual(self.count('message_outbox'),0)

    def test_confirmed_failure_repairs_through_checkpoint_then_verified_progress(self):
        from core.checkpoints import CheckpointStore
        job=self.job('FAILED',now=time.time()-10)
        (self.root/'state.json').write_text('{"step":1}',encoding='utf-8')
        checkpoints=CheckpointStore(self.root/'checkpoints')
        checkpoint=checkpoints.publish(self.root,['state.json'],{'job_id':job['id'],'step':1,
            'code_sha256':'a'*64,'data_sha256':'b'*64,'config_sha256':'c'*64})
        original_check={'argv':[sys.executable,'-c','assert 2 == 2']}
        self.db.update_task(self.task.id,plan_json=json.dumps({'mode':'direct','backend':'local','checks':[original_check]}))
        self.poll()
        calls=[]
        def run(role,**kwargs):
            calls.append(role)
            if role=='operator':
                cloud=CloudTools(self.root,self.config)
                with patch.object(cloud,'_checkpoint_store',return_value=checkpoints):
                    cloud.experiment_job_resume_checkpoint({'job_id':job['id'],'checkpoint_id':checkpoint['id']})
                next_job,_=self.store.register(self.task.id,'repaired','local',total=2)
                self.store.observe(next_job['id'],{'completed':1,'state':'RUNNING'})
            return AgentRunResult(role,'fake',True,'brief' if role=='planner' else 'repaired')
        self.runner.run_role=run
        result=self.orch.run_task_to_completion(self.task.id)
        self.assertEqual(calls,['planner','operator'])
        self.assertEqual(result.status,'WAITING_REMOTE')
        self.assertNotEqual(result.remote_job_id,job['id'])
        self.assertEqual(self.store.get(job['id'])['state'],'RECONCILED')
        self.poll()
        with self.db._connect() as c:episode=c.execute('SELECT * FROM recovery_episodes').fetchone()
        self.assertEqual(episode['phase'],'RECOVERED')
        self.assertEqual((episode['reviews'],episode['repairs']),(1,1))
        self.assertEqual(self.count('message_outbox'),0)
        self.assertEqual(self.db.get_task(self.task.id).user_prompt,self.task.user_prompt)
        self.assertIn(original_check,json.loads(self.db.get_task(self.task.id).plan_json)['checks'])

    def test_two_owners_and_late_result_after_progress_are_fenced(self):
        job=self.job('UNKNOWN',now=time.time()-10)
        ep=self.orch.recovery.open(self.db.get_task(self.task.id),'UNKNOWN_JOB',job['id'],job)
        self.orch.recovery.queue(ep)
        task=self.claim()
        current=self.orch.recovery.begin(ep,'worker')
        self.assertIsNotNone(current)
        other=RecoveryStore(WorkspaceDatabase(self.db.db_path))
        self.assertIsNone(other.begin(ep,'intruder'))
        self.assertIsNone(other.begin(ep,'worker'))
        self.store.observe(job['id'],{'completed':1,'state':'RUNNING'})
        self.assertFalse(self.orch.recovery.finish_review(current,'worker','REPAIRING','obsolete'))
        self.orch.recovery.verify(task,self.store.get(job['id']))
        self.assertIn('RECOVERED',recovery_summary(self.db,task.id))

    def test_lease_loss_revision_change_and_terminal_task_fence_results(self):
        job=self.job('UNKNOWN',now=time.time()-10)
        ep=self.orch.recovery.open(self.db.get_task(self.task.id),'UNKNOWN_JOB',job['id'],job)
        self.orch.recovery.queue(ep)
        self.claim()
        current=self.orch.recovery.begin(ep,'worker')
        self.db.update_task(self.task.id,feedback_revision=1)
        self.assertFalse(self.orch.recovery.finish_review(current,'worker','REPAIRING'))
        self.db.update_task(self.task.id,feedback_revision=0,lease_owner='other')
        self.assertFalse(self.orch.recovery.finish_review(current,'worker','REPAIRING'))
        self.db.update_task(self.task.id,lease_owner='worker',status='COMPLETED')
        self.assertFalse(self.orch.recovery.finish_review(current,'worker','REPAIRING'))
        self.assertIsNone(self.orch.recovery.open(self.db.get_task(self.task.id),'UNKNOWN_JOB',job['id'],job))
        self.orch.supervisor.notice(self.db.get_task(self.task.id),'late')
        self.assertEqual(self.count('message_outbox'),0)

    def test_heartbeat_and_logs_cannot_establish_recovery(self):
        job=self.job(now=time.time()-10)
        self.store.unavailable(job['id'],'TimeoutError')
        self.poll()
        ep=self.orch.recovery.active(self.task.id)
        self.store.observe(job['id'],{'completed':0,'heartbeat_at':time.time()})
        self.db.log_event('CHATTER','operator','still working',task_id=self.task.id)
        self.assertFalse(self.orch.recovery.verify(self.db.get_task(self.task.id),self.store.get(job['id'])))
        self.assertEqual(self.orch.recovery.active(self.task.id)['id'],ep['id'])
        self.assertFalse(self.runner.calls)

    def test_bad_poll_and_checkin_do_not_abort_other_jobs(self):
        first=self.job(now=time.time()-10)
        other_task=self.orch.submit_task('independent')
        self.db.update_task(other_task.id,status='OPERATING',workspace_path=str(self.root/'other'))
        second,_=self.store.register(other_task.id,'other','local',total=2,stall_seconds=1,now=time.time()-10)
        with self.db._connect() as c:c.execute('UPDATE experiment_jobs SET due=0')
        seen=[]
        def poll(store,job,*args):
            seen.append(job['id'])
            if job['id']==first['id']:raise OSError('fake unreachable')
            return store.observe(job['id'],{'completed':1})
        with patch('core.supervision.poll_job',side_effect=poll):self.orch.supervisor.poll()
        self.assertEqual(set(seen),{first['id'],second['id']})
        self.assertEqual(self.store.get(second['id'])['completed'],1)
        with self.db._connect() as c:c.execute('UPDATE experiment_jobs SET progress_at=?',(time.time()-10,))
        with patch.object(self.orch.supervisor,'_checkin_ready',side_effect=[OSError('fake path'),True]), \
             patch.object(self.orch.supervisor,'_consult',return_value={'assessment':'SLOW'}) as consult:
            self.orch.supervisor.diagnose()
        consult.assert_not_called()
        self.assertEqual(self.orch.recovery.active(other_task.id)['phase'],'REVIEW_QUEUED')

    def test_native_and_api_recovery_modes_deny_effects(self):
        from scripts.agent_hooks import handle
        tools=AgentTools(self.root,'operator')
        tools.control_environment={'WORKFLOW_DIAGNOSTIC_READONLY':'1'}
        for tool,args in [('write_file',{'path':'x','content':'x'}),('run_command',{'argv':[sys.executable,'-c','pass']}),
                          ('cloud_tool',{'tool':'colab_job_start','arguments':{}}),('fetch_url',{'url':'https://example.com'})]:
            with self.assertRaises(PermissionError):tools.call(tool,args)
        (self.root/'facts.json').write_text('{}')
        self.assertEqual(tools.call('read_file',{'path':'facts.json'})['text'],'{}')
        with self.assertRaises(PermissionError):tools.call('read_file',{'path':'../outside'})
        with patch.dict('os.environ',{'WORKFLOW_DIAGNOSTIC_READONLY':'1'}):
            for name in ['run_command','write_file','call_mcp_tool']:
                result=handle('agy','PreToolUse',{'toolCall':{'name':name,'args':{'CommandLine':'echo hello'}}},self.root)
                self.assertEqual(result['decision'],'deny')
            for args in ({'Path':'../outside'},{'Path':str(self.root.parent/'outside')},
                         {'Path':'auth.json'},{'Path':'.credentials'},{'Path':'.env'},
                         {'Path':['facts.json']},{'unexpected_path':'facts.json'},{}):
                result=handle('agy','PreToolUse',{'toolCall':{'name':'read_file','args':args}},self.root)
                self.assertEqual(result['decision'],'deny',args)
            result=handle('agy','PreToolUse',{'toolCall':{'name':'read_file','args':{'Path':str(self.root/'facts.json')}}},self.root)
            self.assertEqual(result['decision'],'allow')
        for name in ['auth.json','.credentials','.env']:
            with self.assertRaises(PermissionError):tools.call('read_file',{'path':name})

    def test_late_diagnosis_after_state_change_or_terminal_is_rejected(self):
        job=self.job(now=time.time()-10)
        self.assertTrue(self.store.claim_diagnostic(job['id'],'diagnostic'))
        self.store.observe(job['id'],{'state':'UNKNOWN'})
        self.assertFalse(self.store.finish_diagnostic(job['id'],'diagnostic',{'assessment':'STALLED'}))
        self.db.update_task(self.task.id,status='COMPLETED')
        self.assertFalse(self.store.finish_diagnostic(job['id'],'diagnostic',{'assessment':'STALLED'}))
        with patch.object(self.orch.supervisor,'_consult') as consult:
            self.orch.supervisor.diagnose()
            consult.assert_not_called()

    def test_quota_deferred_diagnosis_does_not_consume_diagnostic_budget(self):
        job=self.job(now=time.time()-10)
        with patch.object(self.orch,'recovery',None), \
             patch.object(self.orch.supervisor,'_checkin_ready',return_value=True), \
             patch.object(self.orch.supervisor,'_consult',return_value={'assessment':'UNKNOWN','deferred_until':time.time()+600}):
            self.orch.supervisor.diagnose()
        current=self.store.get(job['id'])
        self.assertEqual(current['diagnostic_count'],0)
        self.assertFalse(current['diagnostic_owner'])
        self.assertGreater(current['diagnostic_until'],time.time()+500)
        self.assertFalse(current['diagnosis'])
        self.assertEqual(self.db.get_task(self.task.id).retry_count,0)

    def test_default_stall_queues_one_owning_researcher_without_watcher_stack(self):
        job=self.job('UNKNOWN',now=time.time()-10)
        with patch.object(self.orch.supervisor,'_checkin_ready',return_value=True), \
             patch.object(self.orch.supervisor,'_consult') as consult:
            self.orch.supervisor.diagnose()
            self.orch.supervisor.diagnose()
            consult.assert_not_called()
        self.poll()
        calls=[]
        self.runner.run_role=lambda role,**kw:calls.append(role) or AgentRunResult(role,'fake',True,
            json.dumps({'action':'observe','reason':'Safe status refresh of existing job'}))
        result=self.orch.run_task_to_completion(self.task.id)
        self.assertEqual(result.status,'WAITING_REMOTE')
        for _ in range(4):self.poll()
        restarted=HybridOrchestrator(self.root,self.config,self.db,ContextLogManager(self.root/'logs'),self.runner)
        with patch.object(restarted.supervisor,'_checkin_ready',return_value=True), \
             patch.object(restarted.supervisor,'_consult') as consult:
            restarted.supervisor.diagnose()
            consult.assert_not_called()
        self.assertEqual(calls,['planner'])
        self.assertEqual(self.count('recovery_episodes'),1)
        self.assertEqual(self.db.get_task(self.task.id).remote_job_id,job['id'])

    def test_recovery_quota_wait_uses_existing_router_and_resume_ledger(self):
        from core.quota import QuotaStore,Snapshot
        example=json.loads((Path(__file__).resolve().parents[1]/'config/workflow_config.example.json').read_text(encoding='utf-8'))
        config={**self.config,**{k:example[k] for k in ('providers','roles','quota_routing')}}
        config['quota_routing']['enabled']=True
        job=self.job('UNKNOWN',now=time.time()-10)
        ep=self.orch.recovery.open(self.db.get_task(self.task.id),'UNKNOWN_JOB',job['id'],job)
        self.orch._queue_recovery(self.db.get_task(self.task.id),ep,'Unknown effects need observation')
        ledger=QuotaStore(self.db)
        for account in ('claude','codex','agy:claude','agy:gemini-4-argon'):
            ledger.put([Snapshot(account,'limit',100,time.time()+600,'fixture',time.time())])
        calls=[]
        self.runner.run_role=lambda role,**kw:calls.append(role) or AgentRunResult(role,'fake',True,
            json.dumps({'action':'observe','reason':'Recheck status'}),structured_data={'model':'fake-model','usage':{'input_tokens':10,'output_tokens':2}})
        orch=HybridOrchestrator(self.root,config,self.db,ContextLogManager(self.root/'logs'),self.runner)
        result=orch.run_task_to_completion(self.task.id)
        self.assertEqual(result.status,'WAITING_PROVIDER')
        self.assertFalse(calls)
        self.assertEqual(orch.recovery.active(self.task.id)['reviews'],0)
        self.assertEqual(result.retry_count,0)
        with self.db._connect() as c:
            c.execute('UPDATE provider_cooldowns SET until_time=0')
            c.execute('UPDATE quota_snapshots SET resets_at=?',(time.time()-1,))
            c.execute('UPDATE task_waits SET due=0')
        orch.control.advance_waits()
        result=orch.run_task_to_completion(self.task.id)
        self.assertEqual(result.status,'WAITING_REMOTE')
        self.assertEqual(calls,['planner'])
        row=ledger.ledger(limit=1)[0]
        self.assertEqual((row['role'],row['model'],row['tokens']),('planner','fake-model',12))

    def test_expired_unregistered_operator_queues_readonly_reconciliation_not_replay(self):
        self.db.claim_task('expired',2,task_id=self.task.id)
        with self.db._connect() as c:c.execute('UPDATE tasks SET lease_expires=0 WHERE id=?',(self.task.id,))
        reclaimed=self.db.claim_task('replacement',2,task_id=self.task.id)
        self.assertEqual(reclaimed.status,'PLANNING')
        self.assertEqual(reclaimed.retry_count,0)
        self.assertEqual(self.orch.recovery.active(self.task.id)['kind'],'UNKNOWN_EFFECTS')
        self.assertEqual(self.count('message_outbox'),0)
        self.db.release_lease(self.task.id,'replacement')
        calls=[]
        def review(role,**kwargs):
            calls.append(role)
            self.assertEqual(self.runner.control_environment['WORKFLOW_DIAGNOSTIC_READONLY'],'1')
            return AgentRunResult(role,'fake',True,json.dumps({'action':'needs_input','reason':'Cannot establish remote identity safely'}))
        self.runner.run_role=review
        final=self.orch.run_task_to_completion(self.task.id)
        self.assertEqual(calls,['planner'])
        self.assertEqual(final.status,'WAITING_INPUT')
        self.assertEqual(self.count('message_outbox'),1)

    def test_restart_during_claim_does_not_repeat_live_consultation(self):
        job=self.job('UNKNOWN',now=time.time()-10)
        ep=self.orch.recovery.open(self.db.get_task(self.task.id),'UNKNOWN_JOB',job['id'],job)
        self.orch._queue_recovery(self.db.get_task(self.task.id),ep,'Reconcile')
        self.claim()
        self.assertIsNotNone(self.orch.recovery.begin(ep,'worker'))
        with self.db._connect() as c:c.execute('UPDATE tasks SET lease_expires=0 WHERE id=?',(self.task.id,))
        # The registered-job restart path returns to polling and preserves identity.
        self.assertIsNone(self.db.claim_task('replacement',2,task_id=self.task.id))
        with self.db._connect() as c:c.execute('UPDATE task_waits SET due=0')
        self.orch.control.advance_waits()
        result=self.orch.run_task_to_completion(self.task.id)
        self.assertEqual(result.status,'WAITING_REMOTE')
        self.assertFalse(self.runner.calls)
        self.assertEqual(self.orch.recovery.active(self.task.id)['reviews'],1)

    def test_progress_before_dispatch_avoids_obsolete_model_call(self):
        job=self.job('UNKNOWN',now=time.time()-10)
        ep=self.orch.recovery.open(self.db.get_task(self.task.id),'UNKNOWN_JOB',job['id'],job)
        self.orch._queue_recovery(self.db.get_task(self.task.id),ep,'Review stale progress')
        self.store.observe(job['id'],{'completed':1,'state':'RUNNING'})
        result=self.orch.run_task_to_completion(self.task.id)
        self.assertEqual(result.status,'WAITING_REMOTE')
        self.assertFalse(self.runner.calls)
        self.assertIn('RECOVERED',recovery_summary(self.db,self.task.id))

    def test_malformed_unknown_review_is_bounded_and_never_launches_operator(self):
        self.runner.run_role=lambda role,**kw:AgentRunResult(role,'fake',False,'',error='Timed out after 5s')
        self.orch._execute_operator(self.db.get_task(self.task.id))
        calls=[]
        self.runner.run_role=lambda role,**kw:calls.append(role) or AgentRunResult(role,'fake',True,'not JSON')
        final=self.orch.run_task_to_completion(self.task.id)
        self.assertEqual(calls,['planner','planner'])
        self.assertEqual(final.status,'WAITING_INPUT')
        self.assertEqual(final.retry_count,0)
        self.assertEqual(self.count('message_outbox'),1)

    def test_successor_smaller_cohort_does_not_establish_recovery(self):
        job=self.job('FAILED',now=time.time()-10)
        ep=self.orch.recovery.open(self.db.get_task(self.task.id),'CONFIRMED_FAILURE',job['id'],job)
        with self.db._connect() as c:
            c.execute("UPDATE experiment_jobs SET state='RECONCILED' WHERE id=?",(job['id'],))
            c.execute("UPDATE tasks SET remote_job_id='' WHERE id=?",(self.task.id,))
        self.db.log_event('JOB_CHECKPOINT_RECONCILED','operator','verified checkpoint',task_id=self.task.id,payload={'job_id':job['id']})
        smaller,_=self.store.register(self.task.id,'smaller','local',total=1)
        observed=self.store.observe(smaller['id'],{'completed':1,'state':'COMPLETED'})
        self.assertFalse(self.orch.recovery.verify(self.db.get_task(self.task.id),observed))
        self.assertEqual(self.orch.recovery.active(self.task.id)['id'],ep['id'])

    def test_notice_dedup_is_per_episode_even_if_reason_text_changes(self):
        job=self.job('UNKNOWN',now=time.time()-10)
        ep=self.orch.recovery.open(self.db.get_task(self.task.id),'UNKNOWN_JOB',job['id'],job)
        self.orch._recovery_wait(self.db.get_task(self.task.id),ep,'Need exact remote identity')
        self.orch._recovery_wait(self.db.get_task(self.task.id),ep,'Same incident reworded')
        with self.db._connect() as c:
            outbox=c.execute('SELECT revision,message FROM message_outbox').fetchone()
            row=c.execute('SELECT notice_sent FROM recovery_episodes').fetchone()
        self.assertEqual(outbox['revision'],1)
        self.assertIn('Need exact remote identity',outbox['message'])
        self.assertEqual(row['notice_sent'],1)

    def test_recovery_packet_log_is_bounded_redacted_and_unverified(self):
        job=self.job('UNKNOWN',now=time.time()-10)
        ep=self.orch.recovery.open(self.db.get_task(self.task.id),'UNKNOWN_JOB',job['id'],job)
        self.orch._queue_recovery(self.db.get_task(self.task.id),ep,'Inspect log safely')
        path=Path(self.db.get_task(self.task.id).context_log_path)
        with path.open('a',encoding='utf-8') as stream:
            stream.write('old logs\n'*1000)
            stream.write('API_KEY=hidden-api-key\n-----BEGIN PRIVATE KEY-----\nhidden-key-body\n-----END PRIVATE KEY-----\n')
            stream.write('ValueError: empty input at parser.py:42\n')
        self.runner.run_role=lambda role,**kw:AgentRunResult(role,'fake',True,json.dumps({'action':'observe','reason':'Check next observation'}))
        self.orch.run_task_to_completion(self.task.id)
        packet=json.loads((self.root/'storage/recovery'/ep['id']/'facts.json').read_text(encoding='utf-8'))
        rendered=json.dumps(packet)
        self.assertNotIn('hidden-api-key',rendered)
        self.assertNotIn('hidden-key-body',rendered)
        self.assertLessEqual(len(packet['log']['text']),1000)
        self.assertIn('ValueError',packet['log']['text'])
        self.assertIn('unverified',packet['log']['verification'])
        self.assertEqual(packet['task_id'],self.task.id)

    def test_lost_worker_cannot_apply_late_reasoning_or_wait_state(self):
        job=self.job('UNKNOWN',now=time.time()-10)
        ep=self.orch.recovery.open(self.db.get_task(self.task.id),'UNKNOWN_JOB',job['id'],job)
        self.orch._queue_recovery(self.db.get_task(self.task.id),ep,'Check stale owner')
        def review(role,**kwargs):
            self.db.update_task(self.task.id,lease_owner='successor',status='WAITING_REMOTE')
            return AgentRunResult(role,'fake',True,json.dumps({'action':'needs_input','reason':'stale decision'}))
        self.runner.run_role=review
        with self.assertRaisesRegex(RuntimeError,'Lease lost'):
            self.orch.run_task_to_completion(self.task.id)
        current=self.db.get_task(self.task.id)
        self.assertEqual((current.lease_owner,current.status),('successor','WAITING_REMOTE'))
        self.assertEqual(self.count('message_outbox'),0)

    def test_failing_diagnostic_is_contained_and_next_job_is_reviewed(self):
        first=self.job(now=time.time()-10)
        task=self.orch.submit_task('other stalled task')
        self.db.update_task(task.id,status='OPERATING',workspace_path=str(self.root/'other'))
        second,_=self.store.register(task.id,'other','local',total=2,stall_seconds=1,now=time.time()-10)
        with patch.object(self.orch,'recovery',None), \
             patch.object(self.orch.supervisor,'_checkin_ready',return_value=True), \
             patch.object(self.orch.supervisor,'_consult',side_effect=[OSError('fake diagnostic failure'),{'assessment':'SLOW'}]) as consult:
            self.orch.supervisor.diagnose()
            self.orch.supervisor.diagnose()
        self.assertEqual(consult.call_count,2)
        self.assertEqual(self.store.get(first['id'])['diagnosis']['assessment'],'UNKNOWN')
        self.assertEqual(self.store.get(second['id'])['diagnosis']['assessment'],'SLOW')
        self.assertEqual(self.count('message_outbox'),0)


if __name__=='__main__':unittest.main()
