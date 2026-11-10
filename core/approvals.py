"""Exact-action decisions for trusted local workflows; never override native permissions."""
import hashlib
import json
import os
from pathlib import Path
from core.control import ControlPlane, digest
from core.database import WorkspaceDatabase


class TaskDeferred(Exception):
    pass


def workspace_revision(workspace, task):
    """Fail closed if a bounded, secret-free source fingerprint cannot be produced."""
    from core.tools import AgentTools
    root=Path(workspace).resolve();paths=AgentTools(root,'planner');records=[];total=0
    controller_logs=Path(task.context_log_path).resolve().parent if task.context_log_path else None
    for directory,dirs,files in os.walk(root):
        dirs[:]=sorted(d for d in dirs if d not in {'.workflow','.git','.conda','.venv','__pycache__','node_modules'}
                       and not (Path(directory)/d).is_symlink() and (Path(directory)/d).resolve()!=controller_logs)
        for name in sorted(files):
            path=Path(directory)/name
            if path.is_symlink():
                raise ValueError('Approval fingerprint cannot include symlinks')
            rel=path.relative_to(root).as_posix()
            try: paths.path(rel)
            except PermissionError: continue
            total+=path.stat().st_size
            if total>64*1024*1024 or len(records)>10000:
                raise ValueError('Workspace too large for automatic approval fingerprint; use explicit host review')
            records.append((rel,hashlib.sha256(path.read_bytes()).hexdigest()))
    return digest({'files':records,'plan':task.plan_json,'feedback_revision':task.feedback_revision})


def command_decision(db_path, task_id, workspace, tool, arguments):
    db=WorkspaceDatabase(db_path);cp=ControlPlane(db);task=db.get_task(task_id)
    if not task or Path(task.workspace_path).resolve()!=Path(workspace).resolve():
        return False,'Approval workspace does not match the controller task'
    with db._connect() as c:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        owner=c.execute('SELECT actor FROM task_actors WHERE task_id=?',(task_id,)).fetchone() if 'task_actors' in tables else None
    actor=owner[0] if owner else task.source_user if task.source_channel=='console' else ''
    if not actor:
        return False,'Task has no authenticated owner; host review is required'
    action={'workspace':str(Path(workspace).resolve()),'revision':workspace_revision(workspace,task),
            'tool':tool,'arguments':arguments}
    with db._connect() as c:
        row=c.execute('SELECT * FROM approvals WHERE task_id=? AND actor=? AND action_hash=? ORDER BY created DESC LIMIT 1',
                      (task_id,actor,digest(action))).fetchone()
    if row and cp.consume_approval(row['id'],task_id,action):
        return True,'Exact action approved once by the task owner; native permissions still apply'
    if row:
        return False,f"Approval {row['id']}: {row['decision']}. Do not repeat side effects; return control to the scheduler."
    with db._connect() as c:
        c.execute("UPDATE approvals SET decision='SUPERSEDED' WHERE task_id=? AND decision IN ('PENDING','APPROVED')",(task_id,))
    key=cp.request_approval(task_id,actor,action)
    with db._connect() as c:
        if 'intake_messages' in tables and task.source_channel=='discord':
            response=f'Approval {key} for task #{task_id}. Action hash: {digest(action)}\n'+json.dumps(action,ensure_ascii=False)
            if len(response)>1700:
                response=f'Approval {key} for task #{task_id}. Full action exceeds Discord preview; inspect it with `python main.py control show {key}` before deciding. Hash: {digest(action)}'
            response+=f'\nUse !approve {key} or !deny {key}. Native permissions still apply.'
            c.execute('INSERT OR IGNORE INTO intake_messages(event_key,scope,actor,message,response,task_id) VALUES(?,?,?,?,?,?)',
                      ('approval:'+key,task.source_user,actor,'[approval request]',response,task_id))
    db.log_event('APPROVAL_REQUESTED','hook',f'Approval {key} required for exact action',task_id=task_id,
                 payload={'approval_id':key,'action_hash':digest(action)})
    return False,f'Approval {key} is pending. Stop this role and return control; do not work around the denied action.'


# ---------------------------------------------------------------- optional plan approval (P2.6-01)
import re as _re

_PLAN_APPROVAL_PATTERNS = (
    r'--approve-plan\b',
    r'\b(?:approve|review|confirm)\s+(?:the\s+)?plan\s+(?:first|before)',
    r'\bplan\s+approval\b',
    r'(?:duyệt|phê duyệt|xác nhận)\s+(?:plan|kế hoạch)\s+(?:trước|rồi mới)',
    r'(?:chờ|đợi)\s+(?:tôi\s+)?duyệt\s+(?:plan|kế hoạch)',
)


def wants_plan_approval(text):
    """True only when the request explicitly asks to approve the plan before execution."""
    text = (text or '').lower()
    return any(_re.search(pattern, text) for pattern in _PLAN_APPROVAL_PATTERNS)


def _task_owner(db, task):
    with db._connect() as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        owner = c.execute('SELECT actor FROM task_actors WHERE task_id=?', (task.id,)).fetchone() if 'task_actors' in tables else None
    return owner[0] if owner else task.source_user if task.source_channel == 'console' else ''


def plan_action(task):
    """Exact approval subject: the plan text, machine plan and the revisions they belong to."""
    return {'kind': 'PLAN_APPROVAL', 'task_id': task.id, 'plan_revision': task.plan_revision,
            'feedback_revision': task.feedback_revision,
            'plan_hash': hashlib.sha256((task.research_notes + '\n' + task.plan_json).encode('utf-8')).hexdigest()}


def request_plan_approval(db, cp, task):
    """Create one pending approval for the current plan revision; returns its key or None (no owner)."""
    actor = _task_owner(db, task)
    if not actor:
        return None
    action = plan_action(task)
    with db._connect() as c:
        rows = c.execute("SELECT id,action,decision FROM approvals WHERE task_id=? AND decision IN ('PENDING','APPROVED')",
                         (task.id,)).fetchall()
        for row in rows:
            if json.loads(row['action']).get('kind') == 'PLAN_APPROVAL':
                if row['action'] == json.dumps(action, sort_keys=True) and row['decision'] == 'PENDING':
                    return row['id']  # duplicate request (restart): reuse, do not notify twice
                c.execute("UPDATE approvals SET decision='SUPERSEDED' WHERE id=?", (row['id'],))
    key = cp.request_approval(task.id, actor, action, ttl=7 * 86400)
    path = ''
    if task.workspace_path:
        plans = Path(task.workspace_path) / '.workflow' / 'plans'
        plans.mkdir(parents=True, exist_ok=True)
        target = plans / f"approval_r{task.plan_revision:04d}.md"
        target.write_text(f"# Plan for approval (task #{task.id}, revision {task.plan_revision})\n\n"
                          f"Plan hash: {action['plan_hash']}\n\n{task.research_notes}\n", encoding='utf-8')
        path = str(target)
    with db._connect() as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'intake_messages' in tables and task.source_channel == 'discord':
            preview = task.research_notes.strip()
            preview = preview if len(preview) <= 1200 else preview[:1200] + '\n...(full plan in the task workspace)'
            response = (f'Plan for task #{task.id} (revision {task.plan_revision}) is ready for your approval.\n'
                        f'{preview}\n\nUse !approve {key} to run it or !deny {key} to stop. '
                        f'Plan hash: {action["plan_hash"][:16]}')
            c.execute('INSERT OR IGNORE INTO intake_messages(event_key,scope,actor,message,response,task_id) VALUES(?,?,?,?,?,?)',
                      ('plan-approval:' + key, task.source_user, actor, '[plan approval request]', response, task.id))
    db.log_event('PLAN_APPROVAL_REQUESTED', 'planner', f'Plan revision {task.plan_revision} awaits owner approval',
                 task_id=task.id, payload={'approval_id': key, 'plan_hash': action['plan_hash'], 'plan_file': path})
    return key


def consume_plan_approval(db, cp, task):
    """-> 'consumed' (approved now used), 'already' (this exact plan was approved earlier) or 'missing'."""
    action = plan_action(task)
    exact = json.dumps(action, sort_keys=True)
    with db._connect() as c:
        rows = c.execute("SELECT id,decision,action FROM approvals WHERE task_id=? AND action_hash=? ORDER BY created DESC",
                         (task.id, digest(action))).fetchall()
    for row in rows:
        if row['action'] != exact:
            continue
        if row['decision'] == 'CONSUMED':
            return 'already'  # technical retries of an approved plan do not ask again
        if row['decision'] == 'APPROVED' and cp.consume_approval(row['id'], task.id, action):
            db.log_event('PLAN_APPROVED', 'owner', f'Plan revision {task.plan_revision} approved', task_id=task.id,
                         payload={'approval_id': row['id'], 'plan_hash': action['plan_hash']})
            return 'consumed'
    return 'missing'
