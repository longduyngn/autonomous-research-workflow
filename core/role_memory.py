"""Role-scoped portable memory. Native sessions are replaceable caches."""
import hashlib
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path


def atomic_json(path, value):
    import time
    import uuid
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    try:
        for attempt in range(10):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        # Never truncate a valid state file when replacement remains blocked.
        tmp.unlink(missing_ok=True)


class RoleMemory:
    def __init__(self, root, role):
        if role not in {'researcher', 'operator', 'validator', 'communicator', 'workspace_watcher', 'world_watcher', 'remote_manager'}:
            raise ValueError('Unknown role')
        self.directory = Path(root) / '.workflow' / 'roles' / role
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'state.json'

    @contextmanager
    def locked(self):
        # OS lock is released on process death; never resume a role concurrently.
        with (self.directory / 'lock').open('a+b') as f:
            f.seek(0)
            if not f.read(1):
                f.write(b'0'); f.flush()
            f.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                f.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(f, fcntl.LOCK_UN)

    def load(self, identity):
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        state = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        reason=('provider/model/instructions changed' if state.get('identity') != key else
                'context limit' if state.get('context_tokens',0)>=64000 else
                'fallback pass limit' if state.get('passes',0)>=12 else '')
        if reason:
            if state:
                atomic_json(self.directory/'handover.json',{'previous_generation':state.get('generation',0),
                    'reason':reason,'recent_self_reports':self.context(),
                    'current_facts':'Re-read current handoff and DB; past self-reports are not validation.'})
            state = {'identity': key, 'session_id': '', 'passes': 0,
                     'generation': state.get('generation', 0) + 1,'rotation_reason':reason}
        if not isinstance(state.get('session_id'),str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}',state.get('session_id','')):
            state['session_id']=''
        return state

    def context(self):
        path = self.directory / 'history.jsonl'
        if not path.exists():
            return ''
        with path.open('rb') as stream:
            size=stream.seek(0,2)
            stream.seek(max(0,size-65536))
            rows=stream.read().decode('utf8',errors='replace').splitlines()[-4:]
        return '\n'.join(rows)[-12000:]

    def save(self, state, result):
        from core.models import utc_now_iso
        state['passes'] += 1
        # Failed native resumes are not automatically replayed (side effects).
        state['session_id'] = (result.structured_data.get('session_id') or state.get('session_id','')) if result.success else ''
        usage=result.structured_data.get('usage',{})
        if isinstance(usage,dict):
            context=usage.get('input_tokens')
            if isinstance(context,int) and context>=0:
                state['context_tokens']=context+int(usage.get('cache_read_input_tokens') or usage.get('cache_read_tokens') or 0)+int(usage.get('cache_creation_input_tokens') or 0)
        if not isinstance(state['session_id'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}',state['session_id']):
            state['session_id']=''
        atomic_json(self.path, state)
        record = {'at': utc_now_iso(), 'success': result.success,
                  'generation': state['generation'], 'output': result.output[-4000:],
                  'error': result.error, 'evidence': result.log_file}
        with (self.directory / 'history.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
