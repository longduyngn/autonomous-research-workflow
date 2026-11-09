"""Small, read-only communicator view and strict untrusted intent protocol.

Only a controller-selected task context log may be read on demand. No arbitrary
paths, transcript files, event payloads or shell access are exposed.
"""
import json
import re
import time
from pathlib import Path


def public_text(value, limit=800):
    text = str(value or '')
    # Drop entire credential-bearing lines before bounding (including quoted JSON).
    text = '\n'.join('[redacted]' if re.search(
        r'(?i)(authorization|bearer\s|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|'
        r'password|secret|credential|private key|\btoken\s*[:=]|auth\.json|\.env\b|[?&](?:token|key)=)', line)
        else line for line in text.splitlines())
    text = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]+)\b', '[redacted]', text)
    return text[-limit:]


def task_facts(db, tasks):
    facts = []
    for task in tasks[:10]:
        with db._connect() as c:
            # Only structural log metadata: arbitrary event summaries can contain secrets.
            events = [dict(r) for r in c.execute(
                'SELECT timestamp,event_type,severity FROM events WHERE task_id=? ORDER BY id DESC LIMIT 5',
                (task['id'],))]
            runs = [dict(r) for r in c.execute(
                'SELECT id,role,status,exit_code,step_index,attempt FROM agent_runs '
                'WHERE task_id=? ORDER BY id DESC LIMIT 3', (task['id'],))]
        events=[{key:public_text(value,80) for key,value in event.items()} for event in events]
        runs=[{key:public_text(value,80) if isinstance(value,str) else value for key,value in run.items()} for run in runs]
        facts.append({'id': task['id'], 'title': public_text(task['title'], 160),
                      'status': task['status'], 'role': task['current_role'],
                      'feedback_revision': task['feedback_revision'],
                      'step_index': task['step_index'], 'events': events, 'runs': runs})
    return facts


def log_excerpt(task, workspace_root, logs_dir):
    """Allow only this task's controller context log, with no model-selected path.

    The excerpt is observed text, not verification of the agent's claims. Reads are
    bounded before decoding/redaction; no raw prompts or native transcripts.
    """
    unavailable={'source':'controller task context log','available':False,
                 'observed_at_unix':time.time(), 'verification':'unverified agent self-report'}
    if workspace_root is None:
        return unavailable
    try:
        root=Path(workspace_root).resolve()
        directory=Path(logs_dir).resolve()
        expected=directory/f"task_{task['id']:05d}.log"
        candidate=Path(task['context_log_path'])
        path=candidate.resolve()
        if (not directory.is_relative_to(root) or expected.is_symlink()
                or path != expected or not path.is_relative_to(directory)
                or not path.is_file()):
            return unavailable
        with path.open('rb') as stream:
            size=stream.seek(0,2)
            stream.seek(max(0,size-8192))
            raw=stream.read(8192)
        text=raw.decode('utf-8',errors='replace')
        if size>8192:
            text=text.partition('\n')[2]  # Drop a potentially truncated credential line.
        elif '## Execution Timeline & Context History' in text:
            text=text.split('## Execution Timeline & Context History',1)[1]
        text=re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|$)',
                    '[redacted private key]',text)
        lines=text.splitlines()[-20:]
        excerpt=public_text('\n'.join(lines),1200)
        return {**unavailable,'available':True,'modified_at_unix':path.stat().st_mtime,
                'truncated':size>8192 or len(text.splitlines())>20 or len('\n'.join(lines))>1200,
                'text':excerpt}
    except (OSError,ValueError):
        return unavailable


def parse_intent(value):
    if isinstance(value, str):
        if len(value) > 8000:
            raise ValueError('Oversized intent')
        value = json.loads(value)
    if not isinstance(value, dict) or set(value) != {'version', 'intent', 'task_id', 'reply'}:
        raise ValueError('Invalid intent schema')
    if type(value['version']) is not int or value['version'] != 1:
        raise ValueError('Unsupported intent version')
    if value['intent'] not in {'task', 'feedback', 'status', 'evidence', 'chat', 'clarify'}:
        raise ValueError('Unsupported intent')
    if value['task_id'] is not None and (type(value['task_id']) is not int or value['task_id'] < 1):
        raise ValueError('Invalid target')
    if not isinstance(value['reply'], str) or len(value['reply']) > 1800:
        raise ValueError('Invalid reply')
    if value['intent'] in {'task', 'chat', 'clarify'} and value['task_id'] is not None:
        raise ValueError('Unexpected target')
    return dict(value)
