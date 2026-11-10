"""Durable scoped intake; controller validates communicator proposals before commit."""
import json
import re
import threading
import time
import uuid

TERMINAL = {'COMPLETED', 'FAILED', 'ESCALATED'}


class Conversation:
    def __init__(self, db, submit=None, reply=None, workspace_root=None, logs_dir=None):
        self.db, self.submit = db, submit
        self.reply=reply
        self.workspace_root,self.logs_dir=workspace_root,logs_dir
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
            for name,definition in {'delivery_owner':"TEXT NOT NULL DEFAULT ''",'delivery_until':'REAL NOT NULL DEFAULT 0',
                                    'intake_owner':"TEXT NOT NULL DEFAULT ''",'intake_until':'REAL NOT NULL DEFAULT 0',
                                    'intent_json':"TEXT NOT NULL DEFAULT ''"}.items():
                if name not in columns:c.execute(f'ALTER TABLE intake_messages ADD COLUMN {name} {definition}')

    def receive(self, message, scope, actor, event_key, reply_key='', explicit=False, input_files=None,delivery_channel=''):
        with self.lock:
            owner = uuid.uuid4().hex
            with self.db._connect() as c:
                c.execute('BEGIN IMMEDIATE')
                c.execute('INSERT OR IGNORE INTO intake_messages(event_key,scope,actor,message,reply_key,options,delivery_channel) VALUES(?,?,?,?,?,?,?)',
                          (event_key,scope,actor,message,reply_key,json.dumps({'explicit':explicit,'input_files':[str(p) for p in input_files or []]}),delivery_channel or scope))
                row = c.execute('SELECT * FROM intake_messages WHERE event_key=?',(event_key,)).fetchone()
                if not actor or not event_key or not scope or (row['scope'],row['actor'],row['message'],row['reply_key']) != (scope,actor,message,reply_key):
                    raise ValueError('Stable message identity/content is required')
                if row['response'] or row['intake_until'] > time.time():
                    return dict(row)
                c.execute('UPDATE intake_messages SET intake_owner=?,intake_until=? WHERE event_key=?',
                          (owner,time.time()+3600,event_key))
            try:
                self._receive(message,scope,actor,event_key,reply_key,explicit,input_files,delivery_channel,owner)
            finally:
                with self.db._connect() as c:
                    c.execute("UPDATE intake_messages SET intake_owner='',intake_until=0 WHERE event_key=? AND intake_owner=?",(event_key,owner))
            with self.db._connect() as c:
                return dict(c.execute('SELECT * FROM intake_messages WHERE event_key=?',(event_key,)).fetchone())

    def _receive(self, message, scope, actor, event_key, reply_key='', explicit=False, input_files=None,delivery_channel='',owner=''):
        with self.db._connect() as c:
            row = c.execute('SELECT * FROM intake_messages WHERE event_key=?', (event_key,)).fetchone()
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
        proposed=False
        if intent == 'chat' and (self.reply or row['intent_json']):
            proposed=True
            from core.conversation_intents import parse_intent, public_text, task_facts
            fallback = {'version':1,'intent':'clarify','task_id':None,
                        'reply':'I could not interpret this request safely. Please restate it or use !task, !feedback <id>, or !status.'}
            proposal = json.loads(row['intent_json']) if row['intent_json'] else None
            if proposal is None:
                with self.db._connect() as c:
                    history=[{'message':public_text(r['message']), 'response':public_text(r['response'])} for r in c.execute(
                        'SELECT message,response FROM intake_messages WHERE scope=? AND actor=? AND event_key!=? AND response!=\'\' ORDER BY rowid DESC LIMIT 8',
                        (scope,actor,event_key))]
                try:
                    candidate=self.reply(scope,actor,public_text(text,6000),list(reversed(history)),task_facts(self.db,list(tasks.values())))
                    # Old reply callbacks may return ordinary conversational text. They never mutate.
                    if candidate is None:
                        proposal={**fallback,'intent':'chat','reply':
                            'This message was saved; no new task was started. Use !task <instructions>, !feedback <id> <change>, or !status.'}
                    elif isinstance(candidate,str) and not candidate.lstrip().startswith(('{','[')):
                        proposal={**fallback,'intent':'chat','reply':candidate[:1800]} if candidate else fallback
                    else:
                        proposal=parse_intent(candidate)
                except Exception as exc:
                    proposal=fallback
                    self.db.log_event('CONVERSATION_REPLY_ERROR','communicator',type(exc).__name__,severity='WARN')
                proposal['revision'] = tasks.get(proposal['task_id'],{}).get('feedback_revision')
                with self.db._connect() as c:
                    cur=c.execute('UPDATE intake_messages SET intent_json=? WHERE event_key=? AND intake_owner=?',
                                  (json.dumps(proposal),event_key,owner))
                    if not cur.rowcount:
                        return dict(c.execute('SELECT * FROM intake_messages WHERE event_key=?',(event_key,)).fetchone())
            proposed_target=proposal['task_id']
            with self.db._connect() as c:
                recorded_feedback=c.execute('SELECT 1 FROM task_feedback WHERE event_key=? AND task_id=? AND actor=?',
                                            (event_key,proposed_target,actor)).fetchone()
            references={int(x) for x in re.findall(r'(?:#|\btask\s+)(\d+)\b',text,re.I)}
            allowed_target = (proposed_target in tasks and
                              (references == {proposed_target} or (not references and
                               ((linked and linked['task_id']==proposed_target) or len(tasks)==1))))
            if proposal['intent'] in {'feedback','status','evidence'} and proposed_target is not None and not allowed_target:
                proposal={**fallback,'reply':'Please identify one of your tasks in this channel with its task number.'}
            if proposal['intent']=='feedback' and (not allowed_target or
                    (not recorded_feedback and proposal['revision'] != tasks[proposed_target]['feedback_revision'])):
                proposal={**fallback,'reply':'Please identify the current task and the change you want.'}
            if proposal['intent']=='task' and re.fullmatch(r'(?:retry|reopen)\s+(?:task\s*)?#?\d+\.?',text,re.I):
                proposal={**fallback,'reply':'Use !retry <id> for an explicit new run of your existing task.'}
            intent=proposal['intent']
            target=proposal['task_id']
            # Preserve authenticated original wording, never model-authored instructions.
            body=text
            if intent in {'chat','clarify'}:
                response=proposal['reply'] or fallback['reply']
                # Action receipts belong to the controller, including in ordinary chat.
                receipt_text=re.sub(r'(?i)no new task was started|no task was created or changed','',response)
                if re.search(r'(?i)\b(queued|submitted|scheduled|created|executed|approved|started)\b|'
                             r'đã\s+(?:gửi|tạo|chạy|xếp|phê duyệt)',receipt_text):
                    response='No task was created or changed by this message. Please use a clear task request, !feedback <id> <change>, or !status.'
                intent='resolved_chat'
        with self.db._connect() as c:
            current=c.execute('SELECT * FROM intake_messages WHERE event_key=?',(event_key,)).fetchone()
            if current['intake_owner'] != owner:
                return dict(current)
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
                response = self._feedback(target, actor, change, event_key,
                                          proposal['revision'] if proposed else None)
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
        elif intent in {'status', 'search', 'evidence'}:
            from core.monitoring import status_line
            from core.conversation_intents import public_text, task_facts
            found = ([tasks[target]] if target in tasks else list(tasks.values())) if not command else [r for r in tasks.values() if (not body or body.lower() in (str(r['id'])+' '+r['title']+' '+r['user_prompt']).lower())]
            if intent=='evidence':
                from core.conversation_intents import log_excerpt
                if target not in tasks:
                    response='Please identify one task number for a bounded log excerpt.'
                else:
                    evidence={'id':target,'status':public_text(tasks[target]['status'],60),
                              'feedback_revision':tasks[target]['feedback_revision'],
                              'log':log_excerpt(tasks[target],self.workspace_root,self.logs_dir)}
                    response=json.dumps(evidence,ensure_ascii=False)
                    while len(response)>1800 and evidence['log'].get('text'):
                        evidence['log']['text']=evidence['log']['text'][max(1,len(response)-1800):]
                        evidence['log']['truncated']=True
                        response=json.dumps(evidence,ensure_ascii=False)
            else:
                response = '\n'.join(f"#{r['id']} {r['status']}: {public_text(r['title'],160)}"+public_text(status_line(self.db,r['id']),400) for r in found[:10])[:1800] or 'No matching tasks in your conversation.'
            task_id=target if target in tasks else None
        elif intent != 'resolved_chat':
            response = ('I can follow your task history and record changes. Use !task <instructions> for new work, '
                        '!status, !search <words>, or reply to a task message with feedback. '
                        'This message was saved; no new task was started.')
        with self.db._connect() as c:
            if task_id:
                c.execute('INSERT OR IGNORE INTO task_actors VALUES(?,?,?)', (task_id, actor, scope))
            c.execute('UPDATE intake_messages SET response=?,task_id=? WHERE event_key=? AND intake_owner=?', (response, task_id, event_key,owner))
            return dict(c.execute('SELECT * FROM intake_messages WHERE event_key=?', (event_key,)).fetchone())

    def _feedback(self, task_id, actor, message, key, expected_revision=None):
        if not message.strip():
            return 'Feedback cannot be empty.'
        with self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            recorded=c.execute('SELECT * FROM task_feedback WHERE event_key=?',(key,)).fetchone()
            if recorded:
                return f'Feedback already recorded for #{task_id}; applied at a role boundary.' if recorded['applied'] else f'Feedback saved for #{task_id}; it will be reviewed at the next role boundary. Running commands and remote machines are not interrupted.'
            task = c.execute('SELECT status,feedback_revision FROM tasks WHERE id=?', (task_id,)).fetchone()
            if task['status'] in TERMINAL:
                return f'Task #{task_id} is finished. Use !retry {task_id} for a new run.'
            if expected_revision is not None and task['feedback_revision'] != expected_revision:
                return f'Task #{task_id} changed revision. Please restate your feedback for the current task.'
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
                      ('COMMUNICATING' if cancelled else 'PLANNING', 'communicator' if cancelled else 'planner', task_id))
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
