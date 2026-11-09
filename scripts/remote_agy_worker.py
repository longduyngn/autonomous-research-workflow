"""Standalone Colab AGY worker. Standard library; secrets stay on that VM."""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ID = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}\Z')


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def boot_id():
    p = Path('/proc/sys/kernel/random/boot_id')
    return p.read_text().strip() if p.exists() else 'unsupported'


def job_path(root, request_id):
    if not isinstance(request_id, str) or not ID.fullmatch(request_id):
        raise ValueError('Invalid request ID')
    return Path(root) / 'requests' / (request_id + '.json')


def fingerprint(request):
    return hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def find_agy():
    p = Path.home() / '.local/bin/agy'
    return str(p) if p.exists() else shutil.which('agy')


def parse_native(raw, exit_code):
    try: native = json.loads(raw)
    except ValueError: native = None
    if not isinstance(native,dict):
        return {},'FAILED','','Missing valid native JSON result'
    message = next((native[k] for k in ('result','response','text')
                    if isinstance(native.get(k),str) and native[k].strip()),'')
    if native.get('denied_actions'):
        return native,'WAITING_APPROVAL',message,'Native AGY denied an action; explicit scoped permission is required'
    if exit_code or native.get('is_error') or native.get('status','SUCCESS') not in {'SUCCESS','COMPLETED','OK'}:
        return native,'FAILED','','AGY failed; inspect diagnostics on Colab'
    if not message:
        return native,'FAILED','','Native result has no final response; metadata is not task completion'
    return native,'COMPLETED',message,''


def status(root, request_id):
    p = job_path(root, request_id)
    if not p.exists():
        return {'request_id':request_id, 'state':'MISSING', 'boot_id':boot_id()}
    row = json.loads(p.read_text(encoding='utf-8'))
    # Return the result, never native diagnostics/auth files or credential data.
    result = {k:v for k,v in row.items() if k != 'request'}
    if row['state'] in {'QUEUED','RUNNING'}:
        alive = False
        if row.get('pid'):
            try:
                os.kill(row['pid'], 0)
                alive = True
                stat = Path('/proc')/str(row['pid'])/'stat'
                if stat.exists() and stat.read_text().split(') ',1)[1].startswith('Z '):
                    alive = False
            except ProcessLookupError:
                pass
        if not alive and time.time() - row['created_at'] > 10:
            result.update(state='UNKNOWN', error='Worker stopped without a terminal receipt; do not replay automatically')
    return result


def submit(root, request):
    root = Path(root).resolve()
    request = dict(request)
    rid = request['request_id']
    p = job_path(root, rid)
    if not isinstance(request.get('message'), str) or not 1 <= len(request['message']) <= 50000:
        raise ValueError('Message must contain 1..50000 characters')
    if not isinstance(request.get('model'), str) or not request['model']:
        raise ValueError('An explicit AGY model is required')
    if type(request.get('timeout_seconds',21600)) is not int or not 1 <= request.get('timeout_seconds',21600) <= 21600:
        raise ValueError('Invalid timeout')
    commands = request.get('allowed_commands',[])
    if not isinstance(commands,list) or len(commands)>10 or any(
            not isinstance(x,str) or not 1<=len(x)<=512 or any(ch in x for ch in '\n\r\x00()') for x in commands):
        raise ValueError('Invalid explicit command grants')
    digest = fingerprint(request)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with p.open('x', encoding='utf-8') as f:
            json.dump({'request_id':rid,'fingerprint':digest,'state':'QUEUED',
                       'created_at':time.time(),'boot_id':boot_id(),'request':request},f)
    except FileExistsError:
        existing = json.loads(p.read_text(encoding='utf-8'))
        if existing['fingerprint'] != digest:
            raise ValueError('Request ID already belongs to different content')
        return status(root, rid)
    # An interrupted launch remains ambiguous and is never submitted a second time.
    with (root/'launcher.log').open('ab') as log:
        proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'run',str(root),rid],
            cwd=root, stdin=subprocess.DEVNULL, stdout=log,stderr=log,start_new_session=True)
    return {'request_id':rid,'state':'QUEUED','pid':proc.pid,'boot_id':boot_id()}


def cancel(root, request_id):
    row = status(root,request_id)
    if row['state'] not in {'QUEUED','RUNNING'}:
        return row
    if row.get('boot_id') != boot_id():
        return dict(row,state='UNKNOWN',error='VM identity changed; no cancellation sent')
    # The owning worker consumes this flag and stops its own child process group.
    # Never kill an arbitrary PID obtained from an old receipt.
    atomic(job_path(root,request_id).with_suffix('.cancel.json'),{'at':time.time()})
    return {'request_id':request_id,'state':'CANCEL_REQUESTED','boot_id':boot_id()}


def run(root, request_id):
    import fcntl
    root = Path(root).resolve()
    p = job_path(root, request_id)
    row = json.loads(p.read_text(encoding='utf-8'))
    request = row['request']
    lock = (root/'worker.lock').open('a+')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        row.update(state='BUSY',error='Another turn owns this workspace')
        atomic(p,row)
        return
    row.update(state='RUNNING',pid=os.getpid(),started_at=time.time())
    atomic(p,row)
    if p.with_suffix('.cancel.json').exists():
        row.update(state='CANCELLED',finished_at=time.time(),output='')
        atomic(p,row)
        lock.close()
        return
    state_path = root/'session.json'
    identity = hashlib.sha256((request['model']+'worker-v1').encode()).hexdigest()
    session = json.loads(state_path.read_text()) if state_path.exists() else {}
    rotation = session.get('identity') != identity or session.get('passes',0) >= 12
    if rotation:
        if session:
            atomic(root/'handover.json',dict(session,notice='Re-read actual artifacts; prior self-reports are not verified current facts'))
        session = {'identity':identity,'session_id':'','passes':0}
    sid = session.get('session_id','')
    if sid and not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',sid):
        raise ValueError('Invalid native session ID')
    native_log = root/'requests'/(request_id+'.native.log')
    history_path = root/'history.jsonl'
    history = ''
    if history_path.exists():
        with history_path.open('rb') as f:
            f.seek(max(0,f.seek(0,2)-16000))
            history = '\n'.join(f.read().decode('utf-8',errors='replace').splitlines()[-4:])[-8000:]
    prompt = ('You are the Colab execution agent managed by an authenticated local AGY remote_manager. '
              'Stay in this workspace, preserve existing artifacts, and obey the requested resource limits. '
              'Never read/export credentials, change authentication, contact Discord, or alter host/controller policy. '
              'Do not allocate or change Colab runtimes. Report observed evidence and limitations honestly.\n\n'
              + ('Past self-reports, not verified current facts:\n'+history+'\n\n' if history else '')
              + request['message'])
    cmd = [find_agy() or 'agy','--mode','accept-edits','--sandbox','--output-format','json',
           '--effort','medium','--model',request['model'],'--log-file',str(native_log)]
    if request.get('allowed_commands'):
        cmd += ['--settings',json.dumps({'permissions':{'allow':[
            'command('+command+')' for command in request['allowed_commands']]}})]
    if sid:
        cmd += ['--conversation',sid]
    cmd += ['-p',prompt]
    try:
        with (p.with_suffix('.stdout')).open('w') as out, (p.with_suffix('.stderr')).open('w') as err:
            proc = subprocess.Popen(cmd,cwd=root,stdin=subprocess.DEVNULL,stdout=out,stderr=err,
                                    start_new_session=True)
            row['agent_pid'] = proc.pid
            atomic(p,row)
            while proc.poll() is None:
                cancelled = p.with_suffix('.cancel.json').exists()
                if cancelled or time.time()-row['started_at'] >= request.get('timeout_seconds',21600):
                    import signal
                    os.killpg(proc.pid, signal.SIGTERM)
                    try: proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid,signal.SIGKILL)
                        proc.wait()
                    if cancelled: raise InterruptedError('Cancellation requested by the owner; artifacts preserved')
                    raise TimeoutError('Remote turn budget reached; inspect artifacts before any new request')
                row['heartbeat_at'] = time.time()
                atomic(p,row)
                time.sleep(1)
        raw = p.with_suffix('.stdout').read_text(encoding='utf-8',errors='replace')
        native,state,text,error = parse_native(raw,proc.returncode)
        new_sid = (native.get('session_id') or native.get('conversation_id') or
                   native.get('conversationId') or native.get('sessionId') or
                   (native.get('conversation') or {}).get('id') or sid)
        if not new_sid and native_log.exists():
            match = re.search(r'(?:conversation=|Created conversation )([0-9a-fA-F-]{36})\b',native_log.read_text(errors='replace'))
            if match: new_sid = match.group(1)
        ok = state == 'COMPLETED'
        # No stdout/diagnostic export on failures: those can include OAuth URLs.
        row.update(state=state,exit_code=proc.returncode,
                   output=text[-30000:] if ok else '',session_id=new_sid if ok else '',
                   resumed=bool(sid),error=error,denied_actions=native.get('denied_actions',[]))
        if ok:
            session.update(session_id=new_sid,passes=session['passes']+1)
            atomic(state_path,session)
            with history_path.open('a',encoding='utf-8') as history_file:
                history_file.write(json.dumps({'request_id':request_id,'output':text[-2000:],
                    'at':time.time()},ensure_ascii=False)+'\n')
        else:
            session.update(session_id='')
            atomic(state_path,session)
    except Exception as exc:
        session.update(session_id='')
        atomic(state_path,session)
        row.update(state='CANCELLED' if isinstance(exc,InterruptedError) else 'UNKNOWN' if isinstance(exc,TimeoutError) else 'FAILED',
                   error=str(exc),output='')
    finally:
        row['finished_at'] = time.time()
        atomic(p,row)
        lock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action',choices=['run'])
    parser.add_argument('root')
    parser.add_argument('request_id')
    a = parser.parse_args()
    run(a.root,a.request_id)


if __name__ == '__main__': main()
