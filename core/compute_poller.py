"""Model-free compute poller (P2.5-06).

Each tick claims due jobs submitted through ``JobController``, observes them (no effects), and emits a
wake only for meaningful events: job finished, observation lost, metric anomaly. Wake keys
(job id, kind, sequence) are unique, so restarts or two pollers never emit the same wake twice.
The poller never imports or calls an agent runner; waking a task only moves it out of WAITING_REMOTE.
"""
import json
import math
import time
import uuid

LOST_AFTER = 3            # consecutive UNKNOWN observations before an observation_lost wake
FINISHED = ('COMPLETED', 'FAILED', 'CANCELLED')


class ComputePoller:
    def __init__(self, db, jobs, control=None, clock=time.time, interval=60, owner=None):
        self.db, self.jobs, self.control, self.clock = db, jobs, control, clock
        self.interval, self.owner = interval, owner or uuid.uuid4().hex
        with db._connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS compute_poll_state(request_key TEXT PRIMARY KEY, next_due REAL NOT NULL DEFAULT 0,
              claimed_by TEXT NOT NULL DEFAULT '', claim_until REAL NOT NULL DEFAULT 0, unknown_streak INTEGER NOT NULL DEFAULT 0,
              last_state TEXT NOT NULL DEFAULT '', last_loss REAL, sequence INTEGER NOT NULL DEFAULT 0,
              anomaly INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS compute_wakes(wake_key TEXT PRIMARY KEY, job_id TEXT NOT NULL, task_id INTEGER NOT NULL,
              kind TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL, consumed INTEGER NOT NULL DEFAULT 0);
            ''')

    def _claim(self, request_key, now):
        with self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('INSERT OR IGNORE INTO compute_poll_state(request_key) VALUES(?)', (request_key,))
            cur = c.execute('''UPDATE compute_poll_state SET claimed_by=?, claim_until=? WHERE request_key=?
                               AND next_due<=? AND (claimed_by='' OR claim_until<?)''',
                            (self.owner, now + 300, request_key, now, now))
            return cur.rowcount == 1

    def _release(self, request_key, now, **fields):
        sets = ', '.join(f'{k}=?' for k in fields)
        with self.db._connect() as c:
            c.execute(f"UPDATE compute_poll_state SET claimed_by='', claim_until=0, next_due=?{', ' + sets if sets else ''} "
                      'WHERE request_key=? AND claimed_by=?', (now + self.interval, *fields.values(), request_key, self.owner))

    def _wake(self, row, kind, sequence, detail):
        key = f"{row['job_id']}:{kind}:{sequence}"
        with self.db._connect() as c:
            cur = c.execute('INSERT OR IGNORE INTO compute_wakes VALUES(?,?,?,?,?,?,0)',
                            (key, row['job_id'], row['task_id'], kind, json.dumps(detail), self.clock()))
            new = cur.rowcount == 1
        if new:
            self.db.log_event('COMPUTE_WAKE', 'poller', f"{kind} for job {row['job_id'][:8]}", task_id=row['task_id'],
                              payload={'wake_key': key, 'kind': kind, **detail})
            if self.control is not None and kind in ('job_finished', 'observation_lost', 'metric_anomaly'):
                task = self.db.get_task(row['task_id'])
                if task and task.status == 'WAITING_REMOTE':
                    self.control.wake(task.id)
        return new

    def tick(self):
        """One pass; returns the wake keys emitted (empty for a normal running tick)."""
        now = self.clock()
        emitted = []
        with self.db._connect() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM compute_submissions WHERE state='SUBMITTED'")]
        for row in rows:
            job = self.jobs.store.get(row['job_id'])
            if job['state'] in ('COMPLETED', 'FAILED', 'ARTIFACTS_READY', 'RECONCILED') and self._state(row)['last_state'] in FINISHED:
                continue
            if not self._claim(row['request_key'], now):
                continue
            state = self._state(row)
            try:
                observed = self.jobs.status(self.jobs._handle(row))
            except Exception as exc:  # never let one job stop the pass
                observed = {'state': 'UNKNOWN', 'error': type(exc).__name__}
            fields = {}
            kind, detail = None, {}
            if observed.get('state') == 'UNKNOWN':
                streak = state['unknown_streak'] + 1
                fields['unknown_streak'] = streak
                if streak == LOST_AFTER:
                    kind, detail = 'observation_lost', {'streak': streak}  # telemetry gap, not a failure
            else:
                fields['unknown_streak'] = 0
                if observed['state'] in FINISHED and state['last_state'] not in FINISHED:
                    kind, detail = 'job_finished', {'state': observed['state']}
                fields['last_state'] = observed['state']
                loss = (observed.get('metrics') or {}).get('loss')
                if isinstance(loss, (int, float)):
                    previous = state['last_loss']
                    anomalous = not math.isfinite(loss) or (previous is not None and previous > 0 and loss > 10 * previous)
                    if anomalous and not state['anomaly']:
                        kind = kind or 'metric_anomaly'  # once per anomalous episode
                        detail = detail or {'loss': loss if math.isfinite(loss) else str(loss), 'previous': previous}
                    fields['anomaly'] = int(anomalous)
                    if math.isfinite(loss) and not anomalous:
                        fields['last_loss'] = float(loss)
            if kind:
                fields['sequence'] = state['sequence'] + 1
                if self._wake(row, kind, fields['sequence'], detail):
                    emitted.append(f"{row['job_id']}:{kind}:{fields['sequence']}")
            self._release(row['request_key'], now, **fields)
        return emitted

    def _state(self, row):
        with self.db._connect() as c:
            c.execute('INSERT OR IGNORE INTO compute_poll_state(request_key) VALUES(?)', (row['request_key'],))
            return dict(c.execute('SELECT * FROM compute_poll_state WHERE request_key=?', (row['request_key'],)).fetchone())

    def pending_wakes(self, task_id=None):
        with self.db._connect() as c:
            query = 'SELECT * FROM compute_wakes WHERE consumed=0' + (' AND task_id=?' if task_id else '') + ' ORDER BY created'
            return [dict(r) for r in c.execute(query, (task_id,) if task_id else ())]

    def consume(self, wake_key):
        with self.db._connect() as c:
            return c.execute('UPDATE compute_wakes SET consumed=1 WHERE wake_key=? AND consumed=0', (wake_key,)).rowcount == 1
