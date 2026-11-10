"""Durable jobs and factual progress. Network and diagnostic failures are UNKNOWN."""
import hashlib
import json
import math
import re
import time
import uuid
from core.experiment_progress import numeric_metrics

ACTIVE = {'REGISTERED','RUNNING','UNKNOWN','SUBMISSION_UNKNOWN'}
TERMINAL = {'COMPLETED','FAILED','ARTIFACTS_READY','RECONCILED'}


def wandb_path(value):
    if value and not re.fullmatch(r'[a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+/[a-zA-Z0-9_-]+', value):
        raise ValueError('W&B path must be entity/project/run_id')
    return value or ''


def public_job(row, now=None):
    now = time.time() if now is None else now
    item = dict(row)
    item['progress_age_seconds'] = max(0,now-item['progress_at'])
    item['telemetry_age_seconds'] = max(0,now-item['observed_at']) if item['observed_at'] else None
    item['heartbeat_age_seconds'] = max(0,now-item['heartbeat_at']) if item['heartbeat_at'] else None
    item['percent'] = min(100,100*item['completed']/item['total']) if item['total'] else None
    for field in ['metrics','diagnosis']:
        item[field] = json.loads(item.pop(field+'_json','{}'))
    return item


class ProgressStore:
    def __init__(self, db):
        self.db = db
        with db._connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS experiment_jobs(
              id TEXT PRIMARY KEY,task_id INTEGER NOT NULL REFERENCES tasks(id),
              request_key TEXT NOT NULL,fingerprint TEXT NOT NULL,backend TEXT NOT NULL,
              external_id TEXT NOT NULL DEFAULT '',wandb_path TEXT NOT NULL DEFAULT '',
              wandb_url TEXT NOT NULL DEFAULT '',state TEXT NOT NULL DEFAULT 'REGISTERED',
              stage TEXT NOT NULL DEFAULT 'starting',completed REAL NOT NULL DEFAULT 0,
              total REAL NOT NULL DEFAULT 0,metrics_json TEXT NOT NULL DEFAULT '{}',
              heartbeat_at REAL NOT NULL DEFAULT 0,progress_at REAL NOT NULL,observed_at REAL NOT NULL DEFAULT 0,
              created_at REAL NOT NULL,budget_until REAL NOT NULL,stall_seconds REAL NOT NULL,
              due REAL NOT NULL,telemetry_status TEXT NOT NULL DEFAULT 'unobserved',
              diagnosis_json TEXT NOT NULL DEFAULT '{}',diagnosed_progress REAL NOT NULL DEFAULT -1,
              diagnostic_owner TEXT NOT NULL DEFAULT '',diagnostic_until REAL NOT NULL DEFAULT 0,
              diagnostic_count INTEGER NOT NULL DEFAULT 0,UNIQUE(task_id,request_key));''')
            columns={r[1] for r in c.execute('PRAGMA table_info(experiment_jobs)')}
            for key,definition in {'started_at':'REAL NOT NULL DEFAULT 0','finished_at':'REAL NOT NULL DEFAULT 0',
                                   'diagnostic_state':"TEXT NOT NULL DEFAULT ''",
                                   'wandb_status':"TEXT NOT NULL DEFAULT 'unconfigured'"}.items():
                if key not in columns: c.execute(f'ALTER TABLE experiment_jobs ADD COLUMN {key} {definition}')

    def register(self, task_id, request_key, backend, external_id='', path='', total=0,
                 stall_seconds=900, budget_seconds=0, now=None):
        now = time.time() if now is None else now
        if backend not in {'colab','kaggle','local'} or not re.fullmatch('[a-zA-Z0-9_.-]{1,100}',request_key):
            raise ValueError('Invalid backend/request key')
        path = wandb_path(path)
        if (not all(isinstance(x,(int,float)) and math.isfinite(x) for x in (total,stall_seconds,budget_seconds))
                or total<0 or stall_seconds<1 or not (budget_seconds == 0 or 1<=budget_seconds<=7*86400)):
            raise ValueError('Invalid job limits')
        identity = [backend,external_id,path,total,stall_seconds,budget_seconds]
        fingerprint = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        with self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            t=c.execute('SELECT status FROM tasks WHERE id=?',(task_id,)).fetchone()
            if not t or t['status'] in {'COMPLETED','FAILED','ESCALATED'}:
                raise ValueError('Missing or terminal task')
            old=c.execute('SELECT * FROM experiment_jobs WHERE task_id=? AND request_key=?',(task_id,request_key)).fetchone()
            if old:
                if old['fingerprint']!=fingerprint:
                    raise ValueError('Request key reused with changed configuration')
                return public_job(old,now),False
            if c.execute("SELECT 1 FROM experiment_jobs WHERE task_id=? AND state NOT IN ('ARTIFACTS_READY','RECONCILED')",(task_id,)).fetchone():
                raise ValueError('Reconcile the existing task job first')
            key=uuid.uuid4().hex
            c.execute('''INSERT INTO experiment_jobs(id,task_id,request_key,fingerprint,backend,external_id,
              wandb_path,total,progress_at,created_at,budget_until,stall_seconds,due)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
              (key,task_id,request_key,fingerprint,backend,external_id,path,total,now,now,now+budget_seconds if budget_seconds else 0,stall_seconds,now))
            c.execute('UPDATE tasks SET remote_job_id=? WHERE id=?',(key,task_id))
        return self.get(key),True

    def get(self,key,task_id=None):
        with self.db._connect() as c:
            r=c.execute('SELECT * FROM experiment_jobs WHERE id=?',(key,)).fetchone()
        if not r or (task_id is not None and r['task_id']!=task_id):
            raise ValueError('Unknown or foreign job')
        return public_job(r)

    def attach_wandb(self,key,path):
        path=wandb_path(path)
        if not path: raise ValueError('W&B path required')
        with self.db._connect() as c:
            cur=c.execute("UPDATE experiment_jobs SET wandb_path=? WHERE id=? AND wandb_path IN ('',?)",(path,key,path))
            if not cur.rowcount: raise ValueError('Unknown job or already bound to a different W&B run')
        return self.get(key)

    def jobs(self,task_id=None):
        with self.db._connect() as c:
            rows=c.execute('SELECT * FROM experiment_jobs'+(' WHERE task_id=?' if task_id is not None else '')+
                           ' ORDER BY created_at',(task_id,) if task_id is not None else ()).fetchall()
        return [public_job(r) for r in rows]

    def observe(self,key,observation,source='local',now=None):
        now=time.time() if now is None else now
        with self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT * FROM experiment_jobs WHERE id=?',(key,)).fetchone()
            if not old: raise ValueError('Unknown job')
            if old['state'] in TERMINAL: return public_job(old,now)
            if observation.get('job_id',key)!=key: raise ValueError('Foreign job progress')
            done,total=observation.get('completed',old['completed']),observation.get('total',old['total'])
            if (not all(isinstance(x,(int,float)) and math.isfinite(x) for x in (done,total))
                    or done<old['completed'] or total<0 or (total and done>total)
                    or (old['total'] and total!=old['total'])):
                raise ValueError('Regressing progress or changed scope')
            state=observation.get('state','RUNNING')
            if state not in ACTIVE|TERMINAL: raise ValueError('Invalid state')
            hb=observation.get('heartbeat_at',now)
            if not isinstance(hb,(int,float)) or not math.isfinite(hb) or hb<0: raise ValueError('Invalid heartbeat')
            stage=str(observation.get('stage',old['stage']))
            if not re.fullmatch(r'[a-zA-Z0-9 _./-]{1,80}',stage): raise ValueError('Invalid stage')
            metrics=numeric_metrics(observation.get('metrics',{}))
            path=wandb_path(observation.get('wandb_path',old['wandb_path']))
            if old['wandb_path'] and path!=old['wandb_path']: raise ValueError('W&B identity changed')
            url=observation.get('wandb_url',old['wandb_url']) or ''
            if url and not re.fullmatch(r'https://(?:wandb\.ai|(?:wandb\.)?forge\.coreweave\.com)/[a-zA-Z0-9_./-]+',url): url=''
            progressed=done>old['completed']
            tracking=str(observation.get('tracking_status','online' if source=='wandb' else old['wandb_status']))
            if not re.fullmatch(r'(?:online|offline|disabled|unconfigured|unavailable:[a-zA-Z]+)',tracking): tracking='unconfigured'
            c.execute('''UPDATE experiment_jobs SET state=?,stage=?,completed=?,total=?,metrics_json=?,heartbeat_at=?,
              progress_at=?,observed_at=?,telemetry_status=?,due=?,wandb_url=?,
              diagnosis_json=CASE WHEN ? THEN '{}' ELSE diagnosis_json END,wandb_path=?,wandb_status=?,
              started_at=CASE WHEN started_at=0 AND ? IN ('RUNNING','COMPLETED','FAILED') THEN ? ELSE started_at END,
              finished_at=CASE WHEN ? IN ('COMPLETED','FAILED') THEN ? ELSE finished_at END WHERE id=?''',
              (state,stage,done,total,json.dumps(metrics),min(now,hb),max(old['progress_at'],min(now,hb)) if progressed else old['progress_at'],
               now,source,now+30,url,progressed,path,tracking,state,min(now,hb),state,min(now,hb),key))
        if progressed or state!=old['state']:
            self.db.log_event('EXPERIMENT_PROGRESS','monitor',f"Job {key[:8]} {state} {done:g}/{total:g}",
                task_id=old['task_id'],payload={'job_id':key,'state':state,'completed':done,'total':total,
                                               'stage':stage,'source':source,'source_time':hb,'observed_at':now})
        return self.get(key)

    def unavailable(self,key,error_type,now=None):
        now=time.time() if now is None else now
        with self.db._connect() as c:
            c.execute("UPDATE experiment_jobs SET telemetry_status=?,due=? WHERE id=? AND state NOT IN ('COMPLETED','FAILED','ARTIFACTS_READY','RECONCILED')",
                      ('unavailable:'+error_type[:60],now+60,key))

    def claim_diagnostic(self,key,owner,now=None,max_count=2):
        now=time.time() if now is None else now
        with self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            r=c.execute('SELECT * FROM experiment_jobs WHERE id=?',(key,)).fetchone()
            if (not r or r['state'] in TERMINAL or r['diagnostic_until']>now or r['diagnostic_count']>=max_count
                    or (r['diagnosed_progress']==r['progress_at'] and not r['diagnostic_owner'])
                    or (now-r['progress_at']<r['stall_seconds'] and (not r['budget_until'] or now<r['budget_until']))):
                return False
            c.execute('''UPDATE experiment_jobs SET diagnostic_owner=?,diagnostic_until=?,
              diagnosed_progress=progress_at,diagnostic_state=state,diagnostic_count=diagnostic_count+1 WHERE id=?''',(owner,now+300,key))
            return True

    def finish_diagnostic(self,key,owner,verdict):
        if verdict.get('assessment') not in {'HEALTHY','SLOW','STALLED','UNKNOWN'}:
            verdict={'assessment':'UNKNOWN','reason':'Invalid diagnostic response'}
        allowed={k:str(verdict.get(k,''))[:1600] for k in ('assessment','reason','recommendation')}
        with self.db._connect() as c:
            cur=c.execute('''UPDATE experiment_jobs SET diagnosis_json=?,diagnostic_owner='',diagnostic_until=0
              WHERE id=? AND diagnostic_owner=? AND progress_at=diagnosed_progress
              AND state=diagnostic_state AND state IN ('REGISTERED','RUNNING','UNKNOWN','SUBMISSION_UNKNOWN')
              AND EXISTS(SELECT 1 FROM tasks t WHERE t.id=experiment_jobs.task_id AND t.status NOT IN ('COMPLETED','FAILED','ESCALATED'))''',(json.dumps(allowed),key,owner))
        return cur.rowcount==1


def wandb_observation(path):
    wandb_path(path)
    import wandb
    run=wandb.Api(timeout=10).run(path)
    s=dict(run.summary)
    if 'workflow/completed' not in s: raise ValueError('Run lacks workflow instrumentation')
    state={'finished':'COMPLETED','failed':'FAILED','crashed':'UNKNOWN','killed':'UNKNOWN'}.get(run.state,'RUNNING')
    metrics={}
    for k,v in s.items():
        try: metrics.update(numeric_metrics({k:v}))
        except (ValueError,TypeError): pass
    return {'job_id':s.get('workflow/job_id'),'completed':s['workflow/completed'],
            'total':s.get('workflow/total',0),'stage':s.get('workflow/stage','running'),
            'heartbeat_at':s.get('workflow/heartbeat_at',0),'state':state,'wandb_url':run.url,
            'metrics':{k:v for k,v in metrics.items() if not k.startswith('workflow/')}}


def poll_job(store,job,workspace,config):
    """Cheap bounded observations. Never reconnect/allocate or submit on a poll."""
    from core.cloud import CloudTools, _is_port_open
    from urllib.parse import urlsplit
    if job['state'] in TERMINAL:
        return job
    errors=[]
    wandb_seen=False
    if job['wandb_path']:
        try:
            latest=store.observe(job['id'],wandb_observation(job['wandb_path']),'wandb')
            if latest['state'] in TERMINAL: return latest
            wandb_seen=True
        except Exception as exc:
            errors.append(type(exc).__name__)
    bridge=urlsplit(config.get('cloud',{}).get('colab_bridge_url','http://127.0.0.1:8765'))
    colab_alive=(bridge.hostname in {'127.0.0.1','localhost','::1'} and _is_port_open(bridge.port or 8765,bridge.hostname)) if job['backend']=='colab' else False
    if job['backend']=='colab' and job['external_id'] and colab_alive:
        try:
            result=CloudTools(workspace,config)._colab('/call',{'name':'get_cells','arguments':{'includeOutputs':True}},timeout=20)
            cells=(result.get('structuredContent') or {}).get('cells',[])
            for cell in cells:
                if cell.get('id')!=job['external_id']:
                    continue
                text=''.join(''.join(o.get('text',[])) if isinstance(o.get('text',[]),list) else str(o.get('text','')) for o in cell.get('outputs',[]))
                for line in reversed(text.splitlines()):
                    if line.startswith('WORKFLOW_PROGRESS: '):
                        data=json.loads(line[len('WORKFLOW_PROGRESS: '):])
                        if data.get('job_id')==job['id']:
                            return store.observe(job['id'],data,'colab')
            errors.append('ProgressMarkerMissing')
        except Exception as exc:
            errors.append(type(exc).__name__)
    if job['backend']=='local':
        try:
            from pathlib import Path
            path=Path(workspace)/'.workflow/progress'/(job['id']+'.json')
            return store.observe(job['id'],json.loads(path.read_text(encoding='utf-8')),'local')
        except Exception as exc:
            errors.append(type(exc).__name__)
    if job['backend']=='kaggle' and job['external_id']:
        try:
            status=CloudTools(workspace,config).kaggle_job_status({'job_id':job['external_id']})
            state={'complete':'COMPLETED','error':'FAILED','running':'RUNNING','queued':'RUNNING'}.get(status['state'],'UNKNOWN')
            return store.observe(job['id'],{'state':state,'completed':job['completed']},'kaggle')
        except Exception as exc:
            errors.append(type(exc).__name__)
    if not wandb_seen: store.unavailable(job['id'],'/'.join(errors) or 'TransportUnavailable')
    return store.get(job['id'])


def task_jobs(db,task_id):
    with db._connect() as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='experiment_jobs'").fetchone(): return []
        return [public_job(r) for r in c.execute('SELECT * FROM experiment_jobs WHERE task_id=? ORDER BY created_at',(task_id,))]


def status_line(db,task_id):
    from core.recovery import recovery_summary
    recovery=recovery_summary(db,task_id)
    jobs=task_jobs(db,task_id)
    diagnosis=''
    with db._connect() as c:
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='agent_reviews'").fetchone():
            row=c.execute('SELECT verdict FROM agent_reviews WHERE task_id=? AND finished_at>0 ORDER BY run_id DESC LIMIT 1',(task_id,)).fetchone()
            if row: diagnosis=' | agent review '+json.loads(row['verdict']).get('assessment','UNKNOWN')
    if not jobs: return diagnosis+recovery
    j=jobs[-1]
    return (f" | job {j['state']} {j['stage']} {j['completed']:g}/{j['total']:g}"
            f" | progress {j['progress_age_seconds']:.0f}s ago | telemetry {j['telemetry_status']} | W&B {j['wandb_status']}"
            +(f" | diagnosis {j['diagnosis'].get('assessment')}" if j['diagnosis'] else '')
            +(f" | W&B {j['wandb_url']}" if j['wandb_url'] else '')+diagnosis+recovery)
