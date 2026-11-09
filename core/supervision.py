"""Code-driven polling and isolated, bounded read-only stall consultations."""
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from core.monitoring import ACTIVE, ProgressStore, poll_job
from core.role_memory import atomic_json


class Supervisor:
    def __init__(self, orchestrator):
        self.orch=orchestrator
        self.db=orchestrator.db
        self.store=ProgressStore(self.db)
        self.options=orchestrator.config.get('monitoring',{})
        with self.db._connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS agent_reviews(
              run_id INTEGER PRIMARY KEY,task_id INTEGER NOT NULL,owner TEXT NOT NULL,
              claimed_at REAL NOT NULL,finished_at REAL NOT NULL DEFAULT 0,
              verdict TEXT NOT NULL DEFAULT '{}')''')

    def poll(self):
        if not self.options.get('enabled',True): return
        now=time.time()
        for job in self.store.jobs():
            if job['state'] not in ACTIVE or job['due']>now: continue
            task=self.db.get_task(job['task_id'])
            if not task or task.status in {'COMPLETED','FAILED','ESCALATED'}: continue
            with self.db._connect() as c:
                claimed=c.execute('UPDATE experiment_jobs SET due=? WHERE id=? AND due<=?',(now+60,job['id'],now)).rowcount
            if claimed:
                try:
                    latest=poll_job(self.store,job,task.workspace_path,self.orch.config)
                    recovery=getattr(self.orch,'recovery',None)
                    if recovery:recovery.verify(task,latest)
                except Exception as exc:
                    self.db.log_event('SUPERVISION_POLL_ERROR','supervisor',type(exc).__name__,task_id=task.id,severity='WARN')

    def notice(self,task,reason,episode_id=None):
        """A deduplicated nonterminal outbox notice only when recovery needs input."""
        current=self.db.get_task(task.id)
        if not current or current.status in {'COMPLETED','FAILED','ESCALATED'}:return
        message=(f'Task #{task.id}: WAITING_INPUT. '+reason+
                 ' Existing job identity and outputs are retained; no automatic cancellation or resubmission was performed.')
        with self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT status FROM tasks WHERE id=?',(task.id,)).fetchone()
            if not row or row['status'] in {'COMPLETED','FAILED','ESCALATED'}:return
            if episode_id:
                claimed=c.execute('UPDATE recovery_episodes SET notice_sent=1 WHERE id=? AND task_id=? AND notice_sent=0',
                                  (episode_id,task.id)).rowcount
                if not claimed:return
            c.execute('''INSERT INTO message_outbox(task_id,channel,recipient,message) VALUES(?,?,?,?)
              ON CONFLICT(task_id) DO UPDATE SET message=excluded.message,delivered=0,attempts=0,next_attempt=0,
              lease_until=0,revision=message_outbox.revision+1
              WHERE message_outbox.message!=excluded.message''',
              (task.id,task.source_channel,task.source_user,message))

    def diagnose(self):
        if not self.options.get('enabled',True): return
        owner=uuid.uuid4().hex
        for job in self.store.jobs():
            if job['state'] not in ACTIVE:continue
            task=self.db.get_task(job['task_id'])
            if not task or task.status in {'COMPLETED','FAILED','ESCALATED'}: continue
            if (job['diagnostic_count']>=self.options.get('max_diagnostics_per_job',2)
                    or (job['diagnosed_progress']==job['progress_at'] and not job['diagnostic_owner'])): continue
            if (time.time()-job['progress_at']<job['stall_seconds'] and time.time()<job['budget_until']): continue
            try:
                if not self._checkin_ready(task,job['id']+'-'+str(job['progress_at'])): continue
            except Exception as exc:
                self.db.log_event('SUPERVISION_CHECKIN_ERROR','supervisor',type(exc).__name__,task_id=task.id,severity='WARN')
                continue
            recovery=getattr(self.orch,'recovery',None)
            if recovery:
                # One task-owned reasoning call replaces watcher+researcher stacking.
                episode=recovery.open(task,'UNKNOWN_JOB',job['id'],job,
                    'No useful progress after cooperative check-in; task owner reviews bounded facts')
                if episode and episode['phase']=='OBSERVING':recovery.queue(episode)
                continue
            if not self.store.claim_diagnostic(job['id'],owner,max_count=self.options.get('max_diagnostics_per_job',2)):
                continue
            try:verdict=self._consult(task,{'kind':'job','job':job})
            except Exception as exc:verdict={'assessment':'UNKNOWN','reason':'Consultation failed: '+type(exc).__name__}
            if verdict.get('deferred_until'):
                with self.db._connect() as c:
                    c.execute("UPDATE experiment_jobs SET diagnostic_owner='',diagnostic_until=?,diagnostic_count=MAX(0,diagnostic_count-1),diagnosed_progress=-1 WHERE id=? AND diagnostic_owner=?",
                              (verdict['deferred_until'],job['id'],owner))
                return
            if self.store.finish_diagnostic(job['id'],owner,verdict):
                self.db.log_event('JOB_DIAGNOSIS','supervisor',verdict.get('assessment','UNKNOWN'),task_id=task.id,payload=verdict)
            return  # Bound model spend; one consultation per pass.
        with self.db._connect() as c:
            rows=c.execute('''SELECT a.* FROM agent_runs a JOIN tasks t ON t.id=a.task_id
              WHERE a.status='RUNNING' AND a.role IN ('operator','researcher','validator')
              AND t.lease_owner!='' AND t.status NOT IN ('COMPLETED','FAILED','ESCALATED')
              AND NOT EXISTS(SELECT 1 FROM agent_reviews r WHERE r.run_id=a.id)
              ORDER BY a.id''').fetchall()
        for row in rows:
            age=time.time()-datetime.fromisoformat(row['spawned_at']).timestamp()
            if age<self.options.get('agent_review_seconds',900): continue
            task=self.db.get_task(row['task_id'])
            if not task or task.status in {'COMPLETED','FAILED','ESCALATED'}:continue
            try:
                if not self._checkin_ready(task,'agent-'+str(row['id'])): continue
            except Exception as exc:
                self.db.log_event('SUPERVISION_CHECKIN_ERROR','supervisor',type(exc).__name__,task_id=task.id,severity='WARN')
                continue
            with self.db._connect() as c:
                claimed=c.execute('INSERT OR IGNORE INTO agent_reviews(run_id,task_id,owner,claimed_at) VALUES(?,?,?,?)',
                                  (row['id'],row['task_id'],owner,time.time())).rowcount
            if not claimed: continue
            task=self.db.get_task(row['task_id'])
            try:
                verdict=self._consult(task,{'kind':'agent','run_id':row['id'],'role':row['role'],
                                           'age_seconds':age,'worker_heartbeat_at':task.heartbeat_at,
                                           'jobs':self.store.jobs(task.id)})
            except Exception as exc:verdict={'assessment':'UNKNOWN','reason':'Consultation failed: '+type(exc).__name__}
            if verdict.get('deferred_until'):
                with self.db._connect() as c:c.execute('DELETE FROM agent_reviews WHERE run_id=? AND owner=?',(row['id'],owner))
                return
            with self.db._connect() as c:
                # A response after the original agent ended is historical only.
                c.execute('UPDATE agent_reviews SET verdict=?,finished_at=? WHERE run_id=? AND owner=?',
                          (json.dumps(verdict),time.time(),row['id'],owner))
            self.db.log_event('AGENT_DIAGNOSIS','supervisor',verdict.get('assessment','UNKNOWN'),task_id=task.id,payload=verdict)
            return

    def _checkin_ready(self,task,subject):
        path=Path(task.workspace_path)/'.workflow/checkin-request.json'
        try: request=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError): request={}
        grace=self.options.get('checkin_grace_seconds',60)
        if request.get('subject')!=subject:
            if request and time.time()-request.get('requested_at',0)<grace: return False
            atomic_json(path,{'subject':subject,'nonce':uuid.uuid4().hex,'requested_at':time.time(),
                              'reason':'Please report factual progress through agent_checkin; do not restart compute.'})
            self.db.log_event('AGENT_CHECKIN_REQUESTED','supervisor','Requested cooperative status before diagnosis',task_id=task.id)
            return False
        return time.time()-request['requested_at']>=grace

    def _consult(self,task,facts):
        """No role lock reuse, no mutation privileges, no cancel/relaunch authority."""
        import copy
        from core.integrations import prepare_integrations
        from core.providers import EphemeralAgentRunner
        from core.quota import route_call,record_result
        override,extra,decision=route_call(self.orch.quota,self.db,self.orch.config,'workspace_watcher',
                                          self.orch.provider_override,self.orch._route_seen)
        if decision is not None and decision.action=='wait':
            return {'assessment':'UNKNOWN','reason':'Diagnostic provider quota deferred',
                    'deferred_until':decision.until or time.time()+900}
        reason='No useful progress or long agent session; request a factual check-in.'
        path=Path(task.workspace_path)/'.workflow/checkin-response.json'
        try:
            response=json.loads(path.read_text(encoding='utf-8'))
            request=json.loads(path.with_name('checkin-request.json').read_text(encoding='utf-8'))
            if response.get('nonce')==request.get('nonce'):
                facts['agent_self_report_unverified']=response
        except (OSError,ValueError): pass
        import hashlib
        database_id=hashlib.sha256(str(self.db.db_path.resolve()).encode()).hexdigest()[:12]
        directory=self.orch.workspace_root/'storage/diagnostics'/database_id/f'task_{task.id:05d}'
        directory.mkdir(parents=True,exist_ok=True)
        if not (directory/'.workflow/integration.json').exists():
            prepare_integrations(self.orch.workspace_root,directory,directory)
        cfg=copy.deepcopy(self.orch.config)
        role=cfg.setdefault('roles',{}).setdefault('workspace_watcher',{})
        if not role.get('provider'):
            return {'assessment':'UNKNOWN','reason':'No diagnostic provider configured'}
        role.update(prompt_file='roles/stall_diagnostician.md',timeout_seconds=120)
        provider=override or role['provider']
        cfg['providers'][provider]={**cfg['providers'][provider], 'mode':'plan',
                                    'skip_permissions':False,'sandbox':True}
        runner=EphemeralAgentRunner(self.orch.workspace_root,cfg).for_workspace(directory)
        runner.control_environment={'WORKFLOW_DIAGNOSTIC_READONLY':'1'}
        runner.run_label=f'task-{task.id:05d}/stall-diagnosis'
        prompt=('Read-only diagnostic consultation. The original agent may still be active. '
                'Do not change task files, cancel jobs, submit compute, send messages or read credentials. '
                'Heartbeat proves liveness only. Diagnose from these controller facts and state uncertainty. '
                'Reply with one JSON object: {"assessment":"HEALTHY|SLOW|STALLED|UNKNOWN",'
                '"reason":"evidence", "recommendation":"next safe observation/repair"}.\n'+json.dumps(facts))
        try:
            log=directory/'.workflow'/f'diagnosis-{uuid.uuid4().hex}.log'
            result=runner.run_role('workspace_watcher',prompt,log,provider_override=override,**extra)
            if self.orch.quota:
                exhausted=record_result(self.orch.quota.store,self.orch.config,'workspace_watcher',result,decision,task.id)
                if exhausted:return {'assessment':'UNKNOWN','reason':'Diagnostic provider quota deferred',
                                      'deferred_until':exhausted['resets_at']}
            if result.success:
                text=result.output.strip()
                if text.startswith('```'):
                    text=text.split('\n',1)[1].rsplit('```',1)[0]
                verdict=json.loads(text)
                if verdict.get('assessment') in {'HEALTHY','SLOW','STALLED','UNKNOWN'}:
                    return {k:str(verdict.get(k,''))[:1600] for k in ('assessment','reason','recommendation')}
            return {'assessment':'UNKNOWN','reason':'Diagnostic failed or did not return a valid verdict; existing compute retained.'}
        except Exception as exc:
            return {'assessment':'UNKNOWN','reason':'Diagnostic unavailable: '+type(exc).__name__}


def extension_for_progress(root,db_path,task_id,now=None):
    """Grant a short lease from independent useful progress, never log chatter."""
    from core.database import WorkspaceDatabase
    from core.monitoring import task_jobs
    now=time.time() if now is None else now
    db=WorkspaceDatabase(db_path)
    for job in task_jobs(db,task_id):
        if job['state'] in ACTIVE and job['completed']>0 and now-job['progress_at']<job['stall_seconds']:
            return 300
    with db._connect() as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='agent_reviews'").fetchone(): return 0
        row=c.execute('''SELECT r.verdict,r.finished_at FROM agent_reviews r JOIN agent_runs a ON a.id=r.run_id
          WHERE r.task_id=? AND a.status='RUNNING' ORDER BY r.run_id DESC LIMIT 1''',(task_id,)).fetchone()
    if row and now-row['finished_at']<600 and json.loads(row['verdict']).get('assessment') in {'HEALTHY','SLOW'}:
        return 300
    return 0
