"""Host controller for encrypted subscription auth and cloud-local Claude roles."""
import base64
import json
import os
import hashlib
from pathlib import Path

from core.remote_agent import ColabAgentTransport


def seal_secret(secret,challenge):
    from cryptography.hazmat.primitives import serialization,hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key=AESGCM.generate_key(bit_length=256); nonce=os.urandom(12)
    public=serialization.load_pem_public_key(challenge['public_key'].encode())
    return {'transfer_id':challenge['transfer_id'],
        'wrapped_key':base64.b64encode(public.encrypt(key,padding.OAEP(
            mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))).decode(),
        'nonce':base64.b64encode(nonce).decode(),
        'ciphertext':base64.b64encode(AESGCM(key).encrypt(nonce,secret.encode(),challenge['transfer_id'].encode())).decode()}


class ClaudeCloud:
    def __init__(self,controller,config):
        self.controller=Path(controller).resolve()
        self.config=config
        # Same managed Colab owner as the existing CPU agent probe. It remains
        # one personal VM; the two provider workspaces/credentials are separate.
        self.directory=self.controller/'storage/remote-agy/colab-personal'
        self.transport=ColabAgentTransport(controller,self.directory,config)

    def deploy(self):
        source=base64.b64encode((self.controller/'scripts/remote_claude_worker.py').read_bytes()).decode()
        return self.transport.rpc('import pathlib,base64,importlib.util\n'
            "_cw_root=pathlib.Path('/content/claude-workflow'); _cw_root.mkdir(exist_ok=True)\n"
            f"(_cw_root/'remote_claude_worker.py').write_bytes(base64.b64decode({source!r}))\n"
            "_saved_token=getattr(globals().get('_cw'),'_oauth_token',None)\n"
            "_spec=importlib.util.spec_from_file_location('claude_cloud_worker',_cw_root/'remote_claude_worker.py')\n"
            '_cw=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_cw)\n'
            '_cw._oauth_token=_saved_token; del _saved_token\n'
            '_result=_cw.setup()')

    def inject_token(self):
        token=os.environ.get('CLAUDE_CODE_OAUTH_TOKEN')
        if not token: raise RuntimeError('CLAUDE_CODE_OAUTH_TOKEN is missing')
        challenge=self.transport.rpc('_result=_cw.prepare_secret()')
        envelope=seal_secret(token,challenge)
        return self.transport.rpc(f'_result=_cw.receive_secret({envelope!r})')

    def start(self,job_id,kind='smoke',manifest=None):
        roles=('operator','validator') if kind=='pair' else ('operator',)
        manifest={**(manifest or {}),'requires':{role:['remote_claude'] for role in roles}}
        config=getattr(self,'config',{})
        if config.get('quota_routing',{}).get('enabled'):
            from core.quota import QuotaRouter, QuotaStore, record_route
            from core.database import WorkspaceDatabase
            from core.control import ControlPlane
            database=Path(config.get('database_path','storage/events.db'))
            if not database.is_absolute():database=self.controller/database
            db=WorkspaceDatabase(database);control=ControlPlane(db)
            # This path can only run on the remote login, including its reserve.
            entry={'provider':'remote_claude_operator','account':'remote_claude','model':''}
            block={**config['quota_routing'],'classes':{'remote':[entry]},
                   'role_classes':{role:'remote' for role in roles},'workarounds':{},'reserve':{}}
            def cooldown(key):
                with db._connect() as connection:
                    row=connection.execute('SELECT until_time FROM provider_cooldowns WHERE account=?',(key,)).fetchone()
                return row[0] if row else 0
            router=QuotaRouter({**config,'quota_routing':block},QuotaStore(db),cooldown_until=cooldown)
            for role in roles:
                decision=router.select(role,requires=['remote_claude'])
                record_route(db,config,role,decision)
                if decision.action=='wait':
                    return {'job_id':job_id,'state':'WAITING_PROVIDER','quota_route':decision.to_dict()}
        return self.transport.rpc(f'_result=_cw.launch({job_id!r},{kind!r},{manifest!r})')

    def status(self,job_id):
        result=self.transport.rpc(f'_result=_cw.status({job_id!r})')
        self.capture_quota(result)
        return result

    def capture_quota(self,result):
        """Import normalized worker usage, preserving remote observation times."""
        from core.quota import Snapshot, QuotaStore
        from core.database import WorkspaceDatabase
        config=getattr(self,'config',{})
        database=Path(config.get('database_path','storage/events.db'))
        if not database.is_absolute():database=self.controller/database
        snapshots=[]
        for item in result.get('quota_snapshots',[]):
            if item.get('account')=='remote_claude' and item.get('window') in {'five_hour','seven_day'}:
                snapshots.append(Snapshot(**{k:v for k,v in item.items() if k in Snapshot.__dataclass_fields__}))
        if snapshots:QuotaStore(WorkspaceDatabase(database)).put(snapshots)

    def collect(self,job_id):
        from core.role_memory import atomic_json
        from pathlib import PurePosixPath
        result=self.transport.rpc(f'_result=_cw.collect({job_id!r})')
        directory=self.controller/'storage/remote-claude'/job_id
        directory.mkdir(parents=True,exist_ok=True)
        manifest={}
        for name,item in result['files'].items():
            parts=PurePosixPath(name)
            if parts.is_absolute() or '..' in parts.parts or '\\' in name:
                raise ValueError('Invalid evidence path')
            path=(directory/name).resolve()
            if not path.is_relative_to(directory.resolve()):raise ValueError('Evidence path escaped workspace')
            data=base64.b64decode(item['data'])
            digest=hashlib.sha256(data).hexdigest()
            if digest!=item['sha256']:raise ValueError('Evidence transfer hash mismatch')
            if os.environ.get('CLAUDE_CODE_OAUTH_TOKEN','\0').encode() in data:
                raise ValueError('Credential data found in evidence export')
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            manifest[name]={'sha256':digest,'bytes':len(data)}
        atomic_json(directory/'download-manifest.json',{'boot_id':result['boot_id'],'files':manifest})
        return {'state':'DOWNLOADED','directory':str(directory),'files':len(manifest)}


class RemoteClaudeOperatorProvider:
    """Extension slot for routing an operator to the Colab Claude login (quota workaround).

    Fails closed. The cloud worker (scripts/remote_claude_worker.py) only runs the
    fixed `smoke`/`pair` pilot scenarios; it cannot receive a task workspace or prompt
    or return file changes. Launching a pilot and reporting it as the operator step
    would mark unexecuted work done, so this adapter refuses before touching Colab
    until the worker gains a general task kind with workspace transfer and
    collection. Keep it out of `quota_routing.workarounds` until then.
    """

    UNSUPPORTED = ('remote_claude_operator is not available: the Colab Claude worker only runs the '
                   'smoke/pair pilot scenarios and cannot execute or return a task workspace yet')

    def __init__(self, controller_root, config, cloud=None):
        self.controller = Path(controller_root).resolve()
        self.config = config
        self.cloud = cloud

    def run(self, *, role, prompt, workspace, timeout, model, log_path, cancel_event):
        from core.models import AgentRunResult
        return AgentRunResult(role=role, provider_name='remote_claude_operator', success=False, output='',
                              exit_code=1, error=self.UNSUPPORTED,
                              structured_data={'quota_account': 'remote_claude', 'unsupported': True})
