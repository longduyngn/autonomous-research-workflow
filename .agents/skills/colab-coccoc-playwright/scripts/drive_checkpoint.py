"""Check Colab Drive readiness and optionally mount/probe an explicit destination.

With --auto-consent (or config colab_drive_consent.enabled) a watcher approves the standard Drive mount
consent in the user's Cốc Cốc profile while the mount runs (see drive_consent.py); it never types passwords
or handles verification. It does not inspect credentials or browse Drive contents.
Each invocation owns a fresh control cell; it never replaces a scientific cell.
"""
import argparse
import base64
import json
import sys
import uuid
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))


def validate_destination(value):
    p = PurePosixPath(value)
    base = PurePosixPath('/content/drive/MyDrive')
    if '..' in p.parts or '\\' in value or p == base or not p.is_relative_to(base):
        raise ValueError('Destination must be a dedicated directory below /content/drive/MyDrive')
    return str(p)


def build_body(destination=None, mount=False, probe=False, expected_boot=None):
    if probe and not destination:
        raise ValueError('--probe requires an explicit --destination')
    if destination:
        destination = validate_destination(destination)
    helper = base64.b64encode((ROOT/'core/checkpoints.py').read_bytes()).decode('ascii') if probe else ''
    return f'''import base64,datetime,hashlib,json,os,pathlib,uuid
_boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
_expected={expected_boot!r}
if _expected and _boot!=_expected:
    raise RuntimeError('Runtime boot identity changed; reconcile before mounting')
def _mounted():
    return os.path.ismount('/content/drive') and pathlib.Path('/content/drive/MyDrive').is_dir()
_r={{'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
     'boot_id':_boot,'drive_mounted':_mounted(),'mount_attempted':False}}
if not _r['drive_mounted'] and {mount!r}:
    _r['mount_attempted']=True
    try:
        from google.colab import drive
        drive.mount('/content/drive',timeout_ms=90000)
    except Exception as _e:
        _r['mount_error_type']=type(_e).__name__
    _r['drive_mounted']=_mounted()
_r['state']='READY' if _r['drive_mounted'] else 'AUTH_REQUIRED'
if not _r['drive_mounted']:
    _r['next_action']='Complete the standard Colab Drive consent (or enable auto-consent), then retry once.'
elif {probe!r}:
    _dest=pathlib.Path({destination!r})
    if not _dest.resolve().is_relative_to(pathlib.Path('/content/drive/MyDrive').resolve()):
        raise ValueError('Destination escapes MyDrive')
    _ns={{'__name__':'colab_checkpoint_probe'}}
    exec(compile(base64.b64decode({helper!r}),'<checkpoint-helper>','exec'),_ns)
    _probe_id=uuid.uuid4().hex
    _work=pathlib.Path('/content/drive-readiness-probes')/_probe_id
    _source=_work/'source';_source.mkdir(parents=True)
    _raw=json.dumps({{'kind':'synthetic-storage-probe','step':0,'nonce':_probe_id}},sort_keys=True).encode()
    (_source/'state.json').write_bytes(_raw)
    _digest=hashlib.sha256(_raw).hexdigest()
    _store_path=_dest/'storage-probes'/_probe_id
    _store=_ns['CheckpointStore'](_store_path)
    _expected_meta={{'code_sha256':hashlib.sha256(base64.b64decode({helper!r})).hexdigest(),
                    'data_sha256':_digest,'config_sha256':hashlib.sha256(b'synthetic-drive-probe-v1').hexdigest()}}
    _manifest=_store.publish(_source,['state.json'],{{**_expected_meta,'step':0,'kind':'synthetic-storage-probe'}})
    _store.verify(_manifest['id'],_expected_meta)
    _store.restore(_manifest['id'],_work/'restored',_expected_meta)
    _restored=(_work/'restored/state.json').read_bytes()
    assert _restored==_raw
    _r.update(destination=str(_dest),probe_store=str(_store_path),checkpoint_id=_manifest['id'],
        probe_sha256=_digest,restored_byte_equal=True,publish_restore_verified=True,
        verification_scope='Synthetic publish/hash/restore through current Drive mount; no VM-loss test or real experiment checkpoint yet.')
_result=_r
'''


def start_consent_watch(timeout_sec,account=''):
    import threading
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    from drive_consent import run_consent_watch
    from colab_playwright import CDP_URL,ensure_coccoc_cdp
    ensure_coccoc_cdp()
    stop=threading.Event();box={}
    def _run():
        try:
            box['result']=run_consent_watch(CDP_URL,timeout_sec,account,stop)
        except Exception as exc:
            box['result']={'state':'WATCH_ERROR','error_type':type(exc).__name__}
    thread=threading.Thread(target=_run,daemon=True);thread.start()
    return stop,thread,box


def stop_consent_watch(watcher):
    stop,thread,box=watcher
    stop.set();thread.join(timeout=15)
    return box.get('result',{'state':'WATCH_STILL_RUNNING'})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mount',action='store_true',help='Use normal Colab mount flow; user handles any consent')
    p.add_argument('--destination',help='Explicit dedicated directory below /content/drive/MyDrive')
    p.add_argument('--probe',action='store_true',help='Publish and restore a small synthetic checkpoint')
    p.add_argument('--expected-boot-id')
    p.add_argument('--receipt',default='storage/tools/colab_drive_status.json')
    p.add_argument('--auto-consent',action='store_true',help='Approve the Drive mount consent in Cốc Cốc while mounting')
    p.add_argument('--consent-timeout',type=int,default=150)
    a=p.parse_args()
    body=build_body(a.destination,a.mount,a.probe,a.expected_boot_id)
    receipt=(ROOT/a.receipt).resolve()
    if not receipt.is_relative_to(ROOT/'storage'):
        p.error('--receipt must be inside controller storage/')
    from main import load_config,load_dotenv
    from core.cloud import CloudTools
    from core.remote_agent import ColabAgentTransport
    from core.role_memory import atomic_json
    load_dotenv(ROOT/'.env');cfg=load_config()
    transport=ROOT/'storage/tools/drive-readiness-transport'/uuid.uuid4().hex
    tr=ColabAgentTransport(ROOT,transport,cfg,cloud=CloudTools(ROOT,cfg))
    policy=cfg.get('colab_drive_consent') or {}
    watcher=None
    if a.mount and (a.auto_consent or policy.get('enabled')):
        watcher=start_consent_watch(a.consent_timeout,policy.get('account',''))
    try:
        result=tr.rpc(body)
    finally:
        if watcher:
            result_consent=stop_consent_watch(watcher)
    if watcher:
        result['auto_consent']=result_consent
    atomic_json(receipt,result)
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result.get('state')=='READY' else 2


if __name__=='__main__':
    raise SystemExit(main())
