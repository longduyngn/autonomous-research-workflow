"""Durable recovery episodes. Observers queue; the task lease owner reasons/repairs."""
import hashlib
import json
import re
import time

TERMINAL={'COMPLETED','FAILED','ESCALATED'}
CLOSED={'RECOVERED','WAIT_INPUT','EXHAUSTED'}


def safe_text(value,limit=1000):
    text=str(value or '')
    text=re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|$)',
                '[redacted private key]',text)
    text=re.sub(r'^[\s\S]*?-----END [^-]*PRIVATE KEY-----','[redacted private key tail]',text)
    text=re.sub(r'\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]+)\b','[redacted]',text)
    return '\n'.join('[redacted]' if re.search(
        r'(?i)authorization|bearer\s|api[_ -]?key|password|secret|credential|private key|\btoken\s*[:=]|\.env\b|auth\.json',line)
        else line for line in text.splitlines())[-limit:]


def queue_expired_worker(connection,task,now):
    """Called inside claim_task's transaction; queue reasoning, never replay effects."""
    if task['status'] in TERMINAL:return False
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='recovery_episodes'").fetchone():return False
    episode=connection.execute("SELECT id FROM recovery_episodes WHERE task_id=? AND phase NOT IN ('RECOVERED','WAIT_INPUT','EXHAUSTED') ORDER BY created DESC LIMIT 1",(task['id'],)).fetchone()
    if not episode and task['status']!='OPERATING':return False
    reason='Worker lease expired with uncertain effects; bounded read-only reconciliation before user escalation'
    if not episode:
        run=connection.execute('SELECT id FROM agent_runs WHERE task_id=? ORDER BY id DESC LIMIT 1',(task['id'],)).fetchone()
        subject='agent-'+str(run['id']) if run else 'expired-worker-'+str(task['id'])
        key=hashlib.sha256(json.dumps([task['id'],subject,0,task['step_index']]).encode()).hexdigest()[:32]
        connection.execute('''INSERT OR IGNORE INTO recovery_episodes
          (id,task_id,subject,kind,phase,baseline_progress,baseline_completed,plan_revision,
           feedback_revision,created,updated,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
          (key,task['id'],subject,'UNKNOWN_EFFECTS','REVIEW_QUEUED',0,0,task['plan_revision'],
           task['feedback_revision'],now,now,reason))
    connection.execute("UPDATE tasks SET status='RESEARCHING',current_role='researcher',lease_owner='',lease_expires=0,outcome='',review_reason=? WHERE id=?",(reason,task['id']))
    return True


class RecoveryStore:
    def __init__(self,db):
        self.db=db
        with db._connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS recovery_episodes(
              id TEXT PRIMARY KEY,task_id INTEGER NOT NULL,subject TEXT NOT NULL,kind TEXT NOT NULL,
              phase TEXT NOT NULL,baseline_progress REAL NOT NULL,baseline_completed REAL NOT NULL,
              plan_revision INTEGER NOT NULL,feedback_revision INTEGER NOT NULL,
              reviews INTEGER NOT NULL DEFAULT 0,repairs INTEGER NOT NULL DEFAULT 0,
              owner TEXT NOT NULL DEFAULT '',claim_until REAL NOT NULL DEFAULT 0,
              notice_sent INTEGER NOT NULL DEFAULT 0,
              due REAL NOT NULL DEFAULT 0,created REAL NOT NULL,updated REAL NOT NULL,
              reason TEXT NOT NULL,evidence_json TEXT NOT NULL DEFAULT '{}')''')
            columns={row[1] for row in c.execute('PRAGMA table_info(recovery_episodes)')}
            if 'notice_sent' not in columns:c.execute('ALTER TABLE recovery_episodes ADD COLUMN notice_sent INTEGER NOT NULL DEFAULT 0')

    def open(self,task,kind,subject,job=None,reason=''):
        progress=job['progress_at'] if job else 0
        completed=job['completed'] if job else 0
        key=hashlib.sha256(json.dumps([task.id,subject,progress,task.step_index]).encode()).hexdigest()[:32]
        now=time.time()
        with self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            current=c.execute('SELECT status FROM tasks WHERE id=?',(task.id,)).fetchone()
            if not current or current['status'] in TERMINAL:return None
            c.execute('''INSERT OR IGNORE INTO recovery_episodes
              (id,task_id,subject,kind,phase,baseline_progress,baseline_completed,plan_revision,
               feedback_revision,created,updated,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
              (key,task.id,subject,kind,'OBSERVING',progress,completed,task.plan_revision,
               task.feedback_revision,now,now,safe_text(reason)))
            if kind=='CONFIRMED_FAILURE':
                c.execute("UPDATE recovery_episodes SET kind=? WHERE id=? AND phase NOT IN ('RECOVERED','WAIT_INPUT','EXHAUSTED')",(kind,key))
            return dict(c.execute('SELECT * FROM recovery_episodes WHERE id=?',(key,)).fetchone())

    def active(self,task_id):
        with self.db._connect() as c:
            row=c.execute("SELECT * FROM recovery_episodes WHERE task_id=? AND phase NOT IN ('RECOVERED','WAIT_INPUT','EXHAUSTED') ORDER BY created DESC LIMIT 1",(task_id,)).fetchone()
        return dict(row) if row else None

    def renew(self,task_id,owner):
        with self.db._connect() as c:
            c.execute("UPDATE recovery_episodes SET claim_until=? WHERE task_id=? AND owner=? AND phase='REVIEWING' AND EXISTS(SELECT 1 FROM tasks t WHERE t.id=? AND t.lease_owner=? AND t.lease_expires>? AND t.status NOT IN ('COMPLETED','FAILED','ESCALATED'))",
                      (time.time()+300,task_id,owner,task_id,owner,time.time()))

    def queue(self,episode):
        with self.db._connect() as c:
            c.execute("UPDATE recovery_episodes SET phase='REVIEW_QUEUED',updated=? WHERE id=? AND phase IN ('OBSERVING','VERIFYING') AND EXISTS(SELECT 1 FROM tasks t WHERE t.id=recovery_episodes.task_id AND t.status NOT IN ('COMPLETED','FAILED','ESCALATED'))",(time.time(),episode['id']))

    def begin(self,episode,owner,max_reviews=2):
        now=time.time()
        with self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            task=c.execute('SELECT * FROM tasks WHERE id=?',(episode['task_id'],)).fetchone()
            row=c.execute('SELECT * FROM recovery_episodes WHERE id=?',(episode['id'],)).fetchone()
            if (not task or task['status'] in TERMINAL or not owner or task['lease_owner']!=owner
                    or task['lease_expires']<now or not row or row['phase'] not in {'REVIEW_QUEUED','REVIEWING'}
                    or (row['owner'] and row['claim_until']>now) or row['reviews']>=max_reviews):return None
            c.execute("UPDATE recovery_episodes SET phase='REVIEWING',owner=?,claim_until=?,reviews=reviews+1,plan_revision=?,feedback_revision=?,updated=? WHERE id=?",
                      (owner,now+300,task['plan_revision'],task['feedback_revision'],now,row['id']))
            return dict(c.execute('SELECT * FROM recovery_episodes WHERE id=?',(row['id'],)).fetchone())

    def finish_review(self,episode,owner,phase,reason='',deferred=False):
        if phase not in {'REVIEW_QUEUED','REPAIRING','VERIFYING','WAIT_INPUT','EXHAUSTED'}:raise ValueError('Invalid recovery phase')
        now=time.time()
        with self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            task=c.execute('SELECT * FROM tasks WHERE id=?',(episode['task_id'],)).fetchone()
            row=c.execute('SELECT * FROM recovery_episodes WHERE id=?',(episode['id'],)).fetchone()
            if (not task or task['status'] in TERMINAL or task['lease_owner']!=owner or task['lease_expires']<now
                    or not row or row['owner']!=owner or row['claim_until']<now or row['phase']!='REVIEWING'
                    or task['feedback_revision']!=row['feedback_revision'] or task['plan_revision']!=row['plan_revision']):return False
            job=c.execute('SELECT progress_at,state FROM experiment_jobs WHERE id=?',(row['subject'],)).fetchone()
            if job and (job['progress_at']!=row['baseline_progress'] or job['state'] in {'COMPLETED','ARTIFACTS_READY','RECONCILED'}):return False
            c.execute("UPDATE recovery_episodes SET phase=?,owner='',claim_until=0,reviews=reviews-?,repairs=repairs+?,due=?,reason=?,updated=? WHERE id=?",
                      (phase,int(deferred),int(phase=='REPAIRING'),now+30,safe_text(reason or row['reason']),now,row['id']))
            return True

    def verify(self,task,job):
        """A code observation, never model prose, establishes recovery."""
        now=time.time()
        with self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            current=c.execute('SELECT status FROM tasks WHERE id=?',(task.id,)).fetchone()
            if not current or current['status'] in TERMINAL:return False
            rows=c.execute("SELECT * FROM recovery_episodes WHERE task_id=? AND phase NOT IN ('RECOVERED','WAIT_INPUT','EXHAUSTED')",(task.id,)).fetchall()
            recovered=False
            for row in rows:
                same=job['id']==row['subject']
                prior=c.execute('SELECT state,total FROM experiment_jobs WHERE id=?',(row['subject'],)).fetchone()
                checkpoint=c.execute("SELECT 1 FROM events WHERE task_id=? AND event_type='JOB_CHECKPOINT_RECONCILED' AND json_extract(payload_json,'$.job_id')=? LIMIT 1",(task.id,row['subject'])).fetchone()
                successor=(not same and prior and prior['state']=='RECONCILED' and checkpoint
                           and job['total']==prior['total'])
                advanced=job['completed']>row['baseline_completed']
                fresh=job['observed_at']>=row['created'] and job['progress_at']>row['baseline_progress']
                if (same or successor) and fresh and advanced and job['state'] in {'RUNNING','COMPLETED','ARTIFACTS_READY'}:
                    evidence={'job_id':job['id'],'source':job['telemetry_status'],'completed_before':row['baseline_completed'],
                              'completed_after':job['completed'],'progress_at':job['progress_at'],'observed_at':job['observed_at']}
                    c.execute("UPDATE recovery_episodes SET phase='RECOVERED',owner='',claim_until=0,evidence_json=?,updated=? WHERE id=?",
                              (json.dumps(evidence),now,row['id']))
                    recovered=True
            return recovered

    def phase(self,episode,phase,reason=''):
        if phase not in {'VERIFYING','WAIT_INPUT','EXHAUSTED'}:raise ValueError('Invalid recovery phase')
        with self.db._connect() as c:
            c.execute("UPDATE recovery_episodes SET phase=?,reason=?,owner='',claim_until=0,due=?,updated=? WHERE id=? AND phase NOT IN ('RECOVERED','WAIT_INPUT','EXHAUSTED') AND EXISTS(SELECT 1 FROM tasks t WHERE t.id=recovery_episodes.task_id AND t.status NOT IN ('COMPLETED','FAILED','ESCALATED'))",
                      (phase,safe_text(reason or episode['reason']),time.time()+30,time.time(),episode['id']))


def recovery_summary(db,task_id):
    with db._connect() as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='recovery_episodes'").fetchone():return ''
        row=c.execute('SELECT phase,reviews,repairs,reason FROM recovery_episodes WHERE task_id=? ORDER BY created DESC LIMIT 1',(task_id,)).fetchone()
    return (f" | recovery {row['phase']} reviews {row['reviews']} repairs {row['repairs']}: "+safe_text(row['reason'],160)) if row else ''
