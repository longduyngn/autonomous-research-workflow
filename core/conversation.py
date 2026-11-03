"""Durable, conservative intake. Conversation does not implicitly create work."""
import json
import re
import threading
import time
import uuid

TERMINAL = {'COMPLETED', 'FAILED', 'ESCALATED'}


class Conversation:
    def __init__(self, db, submit=None, reply=None):
        self.db, self.submit = db, submit
        self.reply=reply
        self.lock=threading.RLock()
        with db._connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS intake_messages(
              event_key TEXT PRIMARY KEY,scope TEXT NOT NULL,actor TEXT NOT NULL,
              message TEXT NOT NULL,reply_key TEXT NOT NULL DEFAULT '',
              response TEXT NOT NULL DEFAULT '',task_id INTEGER, delivered INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS task_actors(task_id INTEGER PRIMARY KEY,actor TEXT NOT NULL,scope TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS task_feedback(
              id INTEGER PRIMARY KEY,task_id INTEGER NOT NULL,actor TEXT NOT NULL,
              message TEXT NOT NULL,event_key TEXT UNIQUE NOT NULL,applied INTEGER NOT NULL DEFAULT 0);
            ''')
            columns = {r[1] for r in c.execute('PRAGMA table_info(intake_messages)')}
            if 'options' not in columns:
                c.execute("ALTER TABLE intake_messages ADD COLUMN options TEXT NOT NULL DEFAULT '{}'")
            if 'delivery_channel' not in columns:
                c.execute("ALTER TABLE intake_messages ADD COLUMN delivery_channel TEXT NOT NULL DEFAULT ''")
            for name,definition in {'delivery_owner':"TEXT NOT NULL DEFAULT ''",'delivery_until':'REAL NOT NULL DEFAULT 0'}.items():
                if name not in columns:c.execute(f'ALTER TABLE intake_messages ADD COLUMN {name} {definition}')

    def receive(self, message, scope, actor, event_key, reply_key='', explicit=False, input_files=None,delivery_channel=''):
        with self.lock:
            return self._receive(message,scope,actor,event_key,reply_key,explicit,input_files,delivery_channel)

    def _receive(self, message, scope, actor, event_key, reply_key='', explicit=False, input_files=None,delivery_channel=''):
        if not actor or not event_key or not scope:
            raise ValueError('Stable actor, channel and message IDs are required')
        with self.db._connect() as c:
            c.execute('INSERT OR IGNORE INTO intake_messages(event_key,scope,actor,message,reply_key,options,delivery_channel) VALUES(?,?,?,?,?,?,?)',
                      (event_key, scope, actor, message, reply_key, json.dumps({'explicit':explicit, 'input_files':[str(p) for p in input_files or []]}),delivery_channel or scope))
            row = c.execute('SELECT * FROM intake_messages WHERE event_key=?', (event_key,)).fetchone()
            if (row['scope'], row['actor'], row['message']) != (scope, actor, message):
                raise ValueError('Message ID reused with different content or identity')
            if row['response']:
                return dict(row)
            saved = json.loads(row['options'])
            explicit, input_files = saved.get('explicit', False), saved.get('input_files', [])
        text = message.strip()
        command = re.match(r'^!(task|status|search|feedback|retry|cancel|approve|deny|approvals)\b\s*(.*)', text, re.I | re.S)
        intent, body = (command[1].lower(), command[2]) if command else ('task' if explicit else 'chat', text)
        task_id = None
        with self.db._connect() as c:
            rows = c.execute('SELECT t.* FROM tasks t JOIN task_actors a ON a.task_id=t.id WHERE a.actor=? AND a.scope=? ORDER BY t.id DESC', (actor, scope)).fetchall()
            linked = c.execute('SELECT task_id FROM intake_messages WHERE event_key=? AND scope=? AND actor=?', (reply_key, scope, actor)).fetchone()
        tasks = {r['id']: dict(r) for r in rows}
        match = re.match(r'^(?:task\s*)?#?(\d+)\b\s*(.*)', body, re.I | re.S) if command else None
        target = int(match[1]) if match else (linked['task_id'] if linked else None)
        if intent == 'chat' and linked and linked['task_id'] in tasks:
            intent = 'feedback'
        if intent in {'approve','deny','approvals'}:
            from core.control import ControlPlane
            control=ControlPlane(self.db)
            with self.db._connect() as c:
                decisions=[dict(r) for r in c.execute('SELECT p.* FROM approvals p JOIN task_actors a ON a.task_id=p.task_id WHERE a.actor=? AND a.scope=? AND p.actor=? ORDER BY p.created DESC LIMIT 10',(actor,scope,actor))]
            if intent=='approvals':
                response='\n'.join(f"{r['id']} task #{r['task_id']} {r['decision']} expires {r['expires']}: {r['action']}" for r in decisions) or 'No approvals in this conversation.'
            else:
                record=next((r for r in decisions if r['id']==body.strip()),None)
                accepted=bool(record) and control.decide(record['id'],actor,'APPROVED' if intent=='approve' else 'DENIED')
                response='Decision recorded for this exact action.' if accepted else 'Approval missing, expired, already decided, or belongs to another actor/channel.'
        elif intent in {'feedback', 'retry', 'cancel'}:
            if target not in tasks:
                response = 'Please identify one of your tasks in this channel: !feedback <id> <change>, !retry <id>, or !cancel <id>.'
            elif intent == 'retry':
                task = self.submit(f'retry task {target}', source_channel='discord', source_user=scope,
                                   event_key=event_key, input_files=input_files,source_actor=actor)
                task_id, response = task.id, f'Task #{task.id} queued as a new run of #{target}. History is preserved.'
            elif intent == 'cancel':
                # Applied by the owner at a role boundary, never lie about stopping a remote VM.
                response = self._feedback(target, actor, 'CANCEL_REQUESTED', event_key)
                task_id = target
            else:
                change = match[2] if match else body
                response = self._feedback(target, actor, change, event_key)
                task_id = target
        elif intent == 'task':
            if not body.strip():
                response = 'Use !task <instructions> to start work.'
            else:
                task = self.submit(body, source_channel='discord', source_user=scope, event_key=event_key, input_files=input_files,source_actor=actor)
                task_id, response = task.id, f'Task #{task.id} queued. Reply to this message with changes, or use !status.'
                similar=[r['id'] for r in tasks.values() if r['user_prompt'].strip().casefold()==body.strip().casefold()]
                if similar:
                    response+=f' Matching prior request: #{similar[0]}; this explicit new request is preserved.'
        elif intent in {'status', 'search'}:
            found = [r for r in tasks.values() if (not body or body.lower() in (str(r['id'])+' '+r['title']+' '+r['user_prompt']).lower())]
            response = '\n'.join(f"#{r['id']} {r['status']}: {r['title']}" for r in found[:10]) or 'No matching tasks in your conversation.'
        else:
            response = ('I can follow your task history and record changes. Use !task <instructions> for new work, '
                        '!status, !search <words>, or reply to a task message with feedback. '
                        'This message was saved; no new task was started.')
            if self.reply:
                with self.db._connect() as c:
                    history=[dict(r) for r in c.execute('SELECT message,response FROM intake_messages WHERE scope=? AND actor=? AND event_key!=? ORDER BY rowid DESC LIMIT 8',(scope,actor,event_key))]
                try:
                    response=self.reply(scope,actor,text,list(reversed(history)),list(tasks.values())[:10]) or response
                except Exception as exc:
                    self.db.log_event('CONVERSATION_REPLY_ERROR','communicator',type(exc).__name__,severity='WARN')
        with self.db._connect() as c:
            if task_id:
                c.execute('INSERT OR IGNORE INTO task_actors VALUES(?,?,?)', (task_id, actor, scope))
            c.execute('UPDATE intake_messages SET response=?,task_id=? WHERE event_key=?', (response, task_id, event_key))
            return dict(c.execute('SELECT * FROM intake_messages WHERE event_key=?', (event_key,)).fetchone())

    def _feedback(self, task_id, actor, message, key):
        if not message.strip():
            return 'Feedback cannot be empty.'
        with self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            task = c.execute('SELECT status FROM tasks WHERE id=?', (task_id,)).fetchone()
            if task['status'] in TERMINAL:
                return f'Task #{task_id} is finished. Use !retry {task_id} for a new run.'
            c.execute('INSERT OR IGNORE INTO task_feedback(task_id,actor,message,event_key) VALUES(?,?,?,?)', (task_id, actor, message, key))
        return f'Feedback saved for #{task_id}; it will be reviewed at the next role boundary. Running commands and remote machines are not interrupted.'

    def apply_feedback(self, task_id):
        with self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            task = c.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            # Do not abandon an outstanding remote job to replan/re-submit it.
            if task['status'] in TERMINAL or task['status'].startswith('WAITING_'):
                return False
            rows = c.execute('SELECT * FROM task_feedback WHERE task_id=? AND applied=0 ORDER BY id', (task_id,)).fetchall()
            if not rows:
                return False
            cancelled = any(r['message'] == 'CANCEL_REQUESTED' for r in rows)
            c.executemany('UPDATE task_feedback SET applied=1 WHERE id=?', [(r['id'],) for r in rows])
            c.execute("UPDATE tasks SET status=?,current_role=?,outcome='',feedback_revision=feedback_revision+1,review_reason='New user feedback' WHERE id=?",
                      ('COMMUNICATING' if cancelled else 'RESEARCHING', 'communicator' if cancelled else 'researcher', task_id))
            if cancelled:
                c.execute("UPDATE tasks SET validation_feedback='User cancellation requested at role boundary. Remote resources require explicit reconciliation.' WHERE id=?", (task_id,))
        return True

    def feedback(self, task_id, applied_only=False):
        with self.db._connect() as c:
            query = 'SELECT id,actor,message,applied FROM task_feedback WHERE task_id=?'
            if applied_only:
                query += ' AND applied=1'
            return [dict(r) for r in c.execute(query + ' ORDER BY id', (task_id,))]

    def delivered(self, event_key, reply_id=None, owner=None):
        with self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM intake_messages WHERE event_key=?', (event_key,)).fetchone()
            if not row or (owner is not None and row['delivery_owner']!=owner):return False
            c.execute("UPDATE intake_messages SET delivered=1,delivery_owner='',delivery_until=0 WHERE event_key=?", (event_key,))
            if reply_id and row:
                c.execute('INSERT OR IGNORE INTO intake_messages(event_key,scope,actor,message,reply_key,response,task_id,delivered) VALUES(?,?,?,?,?,?,?,1)',
                          (reply_id,row['scope'],row['actor'],'[bot response]',event_key,row['response'],row['task_id']))
            return True

    def claim_delivery(self, event_key):
        owner=uuid.uuid4().hex
        with self.db._connect() as c:
            cur=c.execute("UPDATE intake_messages SET delivery_owner=?,delivery_until=? WHERE event_key=? AND delivered=0 AND response!='' AND delivery_until<?",
                          (owner,time.time()+120,event_key,time.time()))
            return owner if cur.rowcount else None

    def release_delivery(self,event_key,owner):
        with self.db._connect() as c:
            c.execute("UPDATE intake_messages SET delivery_owner='',delivery_until=0 WHERE event_key=? AND delivery_owner=?",(event_key,owner))

    def pending(self):
        with self.db._connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM intake_messages WHERE delivered=0 AND response!='' AND delivery_until<? ORDER BY rowid LIMIT 20",(time.time(),))]

    def recover(self):
        with self.db._connect() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM intake_messages WHERE response='' LIMIT 20")]
        for row in rows:
            self.receive(row['message'], row['scope'], row['actor'], row['event_key'], row['reply_key'])
