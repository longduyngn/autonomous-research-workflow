"""Opt-in durable local AGY coordinator and fenced Colab message transport."""
import base64
import copy
import hashlib
import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from core.cloud import CloudTools, _extract_cell_id, _extract_outputs_text
from core.role_memory import RoleMemory, atomic_json

REMOTE_ROOT = '/content/agy-worker'
MARKER = 'AGY_WORKER_RESULT='
DEFAULT_MODEL = 'gemini-3.8-flash-medium'


class Mailbox:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True,exist_ok=True)
        self.path = self.directory/'mailbox.db'
        with self.connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS remote_messages(
                request_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                state TEXT NOT NULL, result TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)''')

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path,timeout=10)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def put(self, request):
        serialized = json.dumps(request,sort_keys=True,ensure_ascii=False)
        with self.connect() as c:
            c.execute('INSERT OR IGNORE INTO remote_messages VALUES(?,?,?,?,?,?)',
                      (request['request_id'],serialized,'PENDING',None,time.time(),time.time()))
            row = c.execute('SELECT * FROM remote_messages WHERE request_id=?',(request['request_id'],)).fetchone()
            if row['payload'] != serialized:
                raise ValueError('Request ID already belongs to different content')
        return self.get(request['request_id'])

    def get(self, request_id):
        with self.connect() as c:
            row = c.execute('SELECT * FROM remote_messages WHERE request_id=?',(request_id,)).fetchone()
        if row is None: raise KeyError(request_id)
        row = dict(row)
        row['payload'] = json.loads(row['payload'])
        row['result'] = json.loads(row['result']) if row['result'] else None
        return row

    def update(self, request_id, result):
        with self.connect() as c:
            if not c.execute('UPDATE remote_messages SET state=?,result=?,updated_at=? WHERE request_id=?',
                (result['state'],json.dumps(result,ensure_ascii=False),time.time(),request_id)).rowcount:
                raise KeyError(request_id)
        return result

    def recent(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute('SELECT request_id,state,created_at,updated_at FROM remote_messages ORDER BY created_at DESC LIMIT 10')]


class ColabAgentTransport:
    def __init__(self, controller, directory, config, cloud=None):
        self.controller = Path(controller).resolve()
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True,exist_ok=True)
        self.cloud = cloud or CloudTools(self.directory,config)

    def rpc(self, body):
        # Serialize notebook control-cell changes across host processes. Independent
        # remote turns are detached; this lock covers transport, not model runtime.
        with RoleMemory(self.directory/'transport','remote_manager').locked():
            code = ('import json,traceback\ntry:\n' +
                    '\n'.join('    '+line for line in body.splitlines()) +
                    '\nexcept Exception as exc:\n    _result={"state":"RPC_ERROR","error_type":type(exc).__name__}\n'
                    +f'print({MARKER!r}+json.dumps(_result))\n')
            pointer = self.directory/'control-cell.json'
            if pointer.exists():
                cid = json.loads(pointer.read_text())['cell_id']
                result = self.cloud.colab_call({'name':'update_cell','arguments':{'cellId':cid,'content':code}})
                if result.get('isError'):
                    raise RuntimeError('Control cell missing; inspect/reconnect before reinitializing transport')
            else:
                cid = _extract_cell_id(self.cloud.colab_call({'name':'add_code_cell','arguments':{
                    'cellIndex':0,'language':'python','code':code}}))
                if not cid: raise RuntimeError('No control cell ID received')
                atomic_json(pointer,{'cell_id':cid})
            result = self.cloud.colab_call({'name':'run_code_cell','arguments':{'cellId':cid}})
            text = _extract_outputs_text(result)
            matches = [line[len(MARKER):] for line in text.splitlines() if line.startswith(MARKER)]
            if result.get('isError') or len(matches) != 1:
                raise RuntimeError('Remote result missing or ambiguous; reconcile before another submission')
            value = json.loads(matches[0])
            if value.get('state') == 'RPC_ERROR':
                raise RuntimeError('Remote operation failed: '+value.get('error_type','unknown'))
            return value

    def deploy(self):
        files = {}
        for name in ('remote_agy_worker.py','remote_agy_auth.py'):
            files[name] = base64.b64encode((self.controller/'scripts'/name).read_bytes()).decode()
        return self.rpc('import pathlib,base64,importlib.util,sys,subprocess\n'
            f'_root=pathlib.Path({REMOTE_ROOT!r}); _root.mkdir(exist_ok=True)\n'
            f'for _name,_data in {files!r}.items():\n'
            '    (_root/_name).write_bytes(base64.b64decode(_data))\n'
            "_spec=importlib.util.spec_from_file_location('agy_worker',_root/'remote_agy_worker.py')\n"
            '_agy_worker=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_agy_worker)\n'
            "if '_agy_auth' not in globals():\n"
            "    _spec=importlib.util.spec_from_file_location('agy_auth',_root/'remote_agy_auth.py')\n"
            '    _agy_auth=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_agy_auth)\n'
            '_exe=_agy_worker.find_agy(); _installer_sha=None\n'
            'if not _exe:\n'
            '    import urllib.request,hashlib\n'
            '    _installer=urllib.request.urlopen("https://antigravity.google/cli/install.sh",timeout=30).read()\n'
            '    _installer_sha=hashlib.sha256(_installer).hexdigest()\n'
            '    (_root/"install.sh").write_bytes(_installer)\n'
            '    with (_root/"install.log").open("w") as _log:\n'
            '        subprocess.run(["bash",str(_root/"install.sh")],stdout=_log,stderr=_log,timeout=120,check=True)\n'
            '    _exe=_agy_worker.find_agy()\n'
            '_version=subprocess.run([_exe,"--version"],capture_output=True,text=True,timeout=15).stdout.strip() if _exe else None\n'
            '_result={"state":"DEPLOYED","agy_version":_version,"boot_id":_agy_worker.boot_id(),"installer_sha256":_installer_sha}')

    def auth_check(self):
        return self.rpc('import subprocess\n'
            '_p=subprocess.run([_agy_worker.find_agy(),"models"],capture_output=True,text=True,timeout=45)\n'
            '_models=[line.split("\\t")[0] for line in _p.stdout.splitlines() if "\\t" in line]\n'
            '_result={"state":"AUTHENTICATED" if _p.returncode==0 and _models else "LOGIN_REQUIRED", "models":_models}')

    def login_start(self):
        return self._auth_status(self.rpc('_result=_agy_auth.start(_agy_worker.find_agy())'))

    def login_status(self):
        return self._auth_status(self.rpc('_result=_agy_auth.status()'))

    @staticmethod
    def _auth_status(value):
        # Terminal OSC hyperlinks terminate in BEL, which is not whitespace.
        if value.get('authorization_url'):
            value['authorization_url'] = re.split(r'[\x00-\x20\x7f]',value['authorization_url'])[0]
        return value

    def login_personal(self):
        return self.rpc('_result=_agy_auth.choose_personal()')

    def login_code(self, code):
        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        state = self.login_status()
        key = serialization.load_pem_public_key(state['public_key'].encode())
        ciphertext = base64.b64encode(key.encrypt(code.strip().encode(),padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),algorithm=hashes.SHA256(),label=None))).decode()
        return self.rpc(f'_result=_agy_auth.submit({ciphertext!r})')

    def send(self, request):
        return self.rpc(f'_result=_agy_worker.submit({REMOTE_ROOT!r},json.loads({json.dumps(request,ensure_ascii=True)!r}))')

    def poll(self, request_id):
        return self.rpc(f'_result=_agy_worker.status({REMOTE_ROOT!r},{request_id!r})')

    def cancel(self, request_id):
        return self.rpc(f'_result=_agy_worker.cancel({REMOTE_ROOT!r},{request_id!r})')


class RemoteManager:
    def __init__(self, controller, directory, config, transport=None):
        self.controller = Path(controller).resolve()
        self.directory = Path(directory).resolve()
        self.config = config
        self.mailbox = Mailbox(self.directory)
        self.transport = transport or ColabAgentTransport(controller,directory,config)

    def local_turn(self, phase, message, current=None):
        from core.providers import EphemeralAgentRunner
        cfg = copy.deepcopy(self.config)
        existing = cfg.get('roles',{}).get('remote_manager',{})
        cfg.setdefault('providers',{})['remote_manager_agy'] = {
            'type':'agy_cli','executable':'agy','mode':'plan','sandbox':True,'skip_permissions':False,
            'inline_prompt':True}
        cfg.setdefault('roles',{})['remote_manager'] = {
            'provider':'remote_manager_agy','model':existing.get('model') or DEFAULT_MODEL,'effort':'medium',
            'tier':'fast','timeout_seconds':existing.get('timeout_seconds',300),
            'prompt_file':'roles/remote_manager.md'}
        workspace = self.directory/'manager'
        workspace.mkdir(parents=True,exist_ok=True)
        runner = EphemeralAgentRunner(workspace,cfg)
        runner.instructions_root = self.controller
        if not isinstance(message,str) or not 1 <= len(message) <= 6000:
            raise ValueError('Local management instructions must contain 1..6000 characters; attach artifacts for larger work')
        if current:
            current = copy.deepcopy(current)
            if isinstance(current.get('output'),str): current['output'] = current['output'][-6000:]
        facts = json.dumps(self.mailbox.recent()[:5],ensure_ascii=False)
        result = runner.run_role('remote_manager',f'Phase: {phase}\nCurrent mailbox facts (authoritative states): {facts}\nUser request:\n{message}\n'
            + ('Current remote receipt:\n'+json.dumps(current,ensure_ascii=False) if current else ''),
            self.directory/'logs'/f'{uuid.uuid4().hex}-{phase.lower()}.log')
        if not result.success:
            raise RuntimeError('Local AGY remote_manager failed; inspect its local role receipt')
        return result

    def send(self, message, request_id=None, model=DEFAULT_MODEL, timeout_seconds=21600, allowed_commands=None):
        request_id = request_id or uuid.uuid4().hex
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}',request_id):
            raise ValueError('Invalid request ID')
        request = {'request_id':request_id,'message':message,'model':model,'timeout_seconds':timeout_seconds}
        if allowed_commands: request['allowed_commands'] = list(allowed_commands)
        row = self.mailbox.put(request)
        if row['state'] != 'PENDING': return row['result']
        # Persist ambiguity BEFORE any network side effect. Never auto-resend.
        self.mailbox.update(request_id,{'request_id':request_id,'state':'UNKNOWN',
            'error':'Submission started; reconcile the remote receipt'})
        try: result = self.transport.send(request)
        except Exception:
            return self.mailbox.get(request_id)['result']
        return self.mailbox.update(request_id,result)

    def poll(self, request_id):
        self.mailbox.get(request_id)
        result = self.transport.poll(request_id)
        previous = self.mailbox.get(request_id).get('result') or {}
        if result['state'] == 'MISSING':
            result.update(state='UNKNOWN',error='Remote receipt unavailable or VM replaced; no automatic replay')
        elif previous.get('boot_id') and previous['boot_id'] != result.get('boot_id'):
            result.update(state='UNKNOWN',error='VM identity changed; inspect saved artifacts')
        return self.mailbox.update(request_id,result)

    def cancel(self, request_id):
        self.mailbox.get(request_id)
        return self.mailbox.update(request_id,self.transport.cancel(request_id))
