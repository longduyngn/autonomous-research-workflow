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
    root=Path(workspace).resolve();paths=AgentTools(root,'researcher');records=[];total=0
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
