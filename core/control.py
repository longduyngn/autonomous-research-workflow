"""Durable waits and control records; no model calls or external side effects."""
import hashlib
import json
import time
import uuid


def check_worker_lease(context):
    """Fence native/API tool calls from a worker whose controller lease expired."""
    owner=context.get('WORKFLOW_WORKER_OWNER')
    if not owner: return
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(context['WORKFLOW_DB_PATH'],timeout=5)) as c:
        row=c.execute('SELECT lease_owner,lease_expires,status FROM tasks WHERE id=?',
                      (int(context['WORKFLOW_TASK_ID']),)).fetchone()
    if not row or row[0]!=owner or row[1]<time.time() or row[2] in {'COMPLETED','FAILED','ESCALATED'}:
        raise PermissionError('Worker lease is stale; existing compute must be observed by the current controller')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class ControlPlane:
    def __init__(self, db):
        self.db = db
        with db._connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS task_waits(task_id INTEGER PRIMARY KEY,kind TEXT NOT NULL,
              resume_status TEXT NOT NULL,reason TEXT NOT NULL,dependency TEXT NOT NULL,
              since REAL NOT NULL,due REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS resources(name TEXT PRIMARY KEY,owner TEXT NOT NULL DEFAULT '',
              generation INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL DEFAULT 'FREE',observation TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY,task_id INTEGER NOT NULL,
              actor TEXT NOT NULL,action TEXT NOT NULL,action_hash TEXT NOT NULL,expires REAL NOT NULL,
              decision TEXT NOT NULL DEFAULT 'PENDING',created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY,task_id INTEGER NOT NULL,
              actor TEXT NOT NULL,message TEXT NOT NULL,event_key TEXT UNIQUE NOT NULL,applied INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS conversations(id INTEGER PRIMARY KEY,event_key TEXT UNIQUE NOT NULL,
              scope TEXT NOT NULL,actor TEXT NOT NULL,message TEXT NOT NULL,response TEXT NOT NULL DEFAULT '',task_id INTEGER);
            CREATE TABLE IF NOT EXISTS provider_cooldowns(account TEXT PRIMARY KEY,until_time REAL NOT NULL,reason TEXT NOT NULL);
            ''')

    def wait(self, task_id, kind, resume, reason, dependency='', delay=15, timeout=86400):
        if kind not in {'REMOTE', 'RESOURCE', 'APPROVAL', 'PROVIDER', 'INPUT'}:
            raise ValueError('Unknown wait kind')
        with self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            task = c.execute('SELECT status FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not task or task['status'] in {'COMPLETED','FAILED','ESCALATED'}:
                raise ValueError('Cannot wait a missing or terminal task')
            c.execute('INSERT OR IGNORE INTO task_waits VALUES(?,?,?,?,?,?,?)',
                      (task_id, kind, resume, reason, dependency, time.time(), time.time()+delay))
            c.execute('UPDATE tasks SET status=?,current_role=NULL,wait_deadline=CASE WHEN wait_deadline=0 THEN ? ELSE wait_deadline END WHERE id=?',
                      ('WAITING_'+kind, time.time()+timeout, task_id))
        return self.db.get_task(task_id)

    def waits(self):
        with self.db._connect() as c:
            return [dict(r) for r in c.execute('SELECT * FROM task_waits')]

    def wake(self, task_id):
        with self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM task_waits WHERE task_id=?', (task_id,)).fetchone()
            task=c.execute('SELECT status,lease_owner FROM tasks WHERE id=?',(task_id,)).fetchone()
            if not row or not task or task['lease_owner'] or task['status'] != 'WAITING_'+row['kind']:
                return False
            # Keep the original start timestamp; account explicitly for time spent waiting.
            c.execute('UPDATE tasks SET status=?,waiting_seconds=waiting_seconds+CASE WHEN started_at>0 THEN ? ELSE 0 END WHERE id=?',
                      (row['resume_status'], max(0,time.time()-row['since']), task_id))
            c.execute('DELETE FROM task_waits WHERE task_id=?', (task_id,))
        return True

    def advance_waits(self):
        now=time.time()
        for row in self.waits():
            task=self.db.get_task(row['task_id'])
            if not task or task.lease_owner:
                continue
            if task.status != 'WAITING_'+row['kind']:
                with self.db._connect() as c:
                    c.execute('DELETE FROM task_waits WHERE task_id=?',(row['task_id'],))
                continue
            if task.wait_deadline and now >= task.wait_deadline:
                self.db.finalize_task(task.id,'ESCALATED','Wait deadline exceeded; inspect pending actions and remote resources before recovery.')
                continue
            if row['kind'] == 'APPROVAL':
                approval=self.approval(row['dependency'])
                if approval and approval['decision']=='APPROVED' and approval['expires']>now:
                    self.wake(task.id)
                elif not approval or approval['decision']=='DENIED' or approval['expires']<=now:
                    self.db.finalize_task(task.id,'ESCALATED','Approval denied or expired. No action was replayed.')
            elif row['kind']=='INPUT':
                continue  # Explicit reconciliation, never a timer-based action replay.
            elif row['due']<=now and (row['kind']!='PROVIDER' or self.available(row['dependency'])):
                self.wake(task.id)

    def claim_resource(self, name, owner):
        with self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('INSERT OR IGNORE INTO resources(name) VALUES(?)', (name,))
            r = c.execute('SELECT * FROM resources WHERE name=?', (name,)).fetchone()
            if r['state'] == 'OWNED' and r['owner'] == owner:
                return r['generation']
            if r['state'] != 'FREE':
                return None
            generation = r['generation'] + 1
            c.execute("UPDATE resources SET owner=?,generation=?,state='OWNED' WHERE name=?", (owner,generation,name))
            return generation

    def check_resource(self, name, owner, generation):
        with self.db._connect() as c:
            r = c.execute('SELECT * FROM resources WHERE name=?', (name,)).fetchone()
        if not r or (r['owner'],r['generation'],r['state']) != (owner,generation,'OWNED'):
            raise PermissionError('Stale or foreign resource ownership')

    def observe_resource(self, name, observation, owner=None, generation=None):
        with self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            r = c.execute('SELECT * FROM resources WHERE name=?', (name,)).fetchone()
            if owner is not None and (not r or r['owner'] != owner or r['generation'] != generation):
                raise PermissionError('Stale resource observation')
            stopped = observation.get('runtime_stopped') is True and observation.get('active_session_count') == 0
            if stopped:
                c.execute("UPDATE resources SET state='FREE',owner='',generation=generation+1,observation=? WHERE name=?",
                          (json.dumps(observation),name))
            else:
                c.execute("UPDATE resources SET state='QUARANTINED',observation=? WHERE name=?",(json.dumps(observation),name))

    def resource(self, name):
        with self.db._connect() as c:
            r=c.execute('SELECT * FROM resources WHERE name=?',(name,)).fetchone()
            return dict(r) if r else None

    def request_approval(self, task_id, actor, action, ttl=86400):
        if not actor or not isinstance(action,dict) or ttl <= 0:
            raise ValueError('An exact action, actor and positive expiry are required')
        key=uuid.uuid4().hex
        with self.db._connect() as c:
            c.execute('INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?)',
                      (key,task_id,actor,json.dumps(action,sort_keys=True),digest(action),time.time()+ttl,'PENDING',time.time()))
        return key

    def decide(self, key, actor, decision):
        if decision not in {'APPROVED','DENIED'}:
            raise ValueError('Invalid decision')
        with self.db._connect() as c:
            cur=c.execute("UPDATE approvals SET decision=? WHERE id=? AND actor=? AND decision='PENDING' AND expires>?",
                          (decision,key,actor,time.time()))
            return cur.rowcount == 1

    def consume_approval(self, key, task_id, action):
        with self.db._connect() as c:
            cur=c.execute("UPDATE approvals SET decision='CONSUMED' WHERE id=? AND task_id=? AND action_hash=? AND decision='APPROVED' AND expires>?",
                          (key,task_id,digest(action),time.time()))
            return cur.rowcount == 1

    def approval(self, key):
        with self.db._connect() as c:
            r=c.execute('SELECT * FROM approvals WHERE id=?',(key,)).fetchone()
            return dict(r) if r else None

    def cool_down(self, account, seconds, reason):
        with self.db._connect() as c:
            c.execute('INSERT INTO provider_cooldowns VALUES(?,?,?) ON CONFLICT(account) DO UPDATE SET until_time=MAX(until_time,excluded.until_time),reason=excluded.reason',
                      (account,time.time()+max(1,seconds),reason))

    def available(self, account):
        with self.db._connect() as c:
            r=c.execute('SELECT until_time FROM provider_cooldowns WHERE account=?',(account,)).fetchone()
            return not r or r[0] <= time.time()
