"""Read back the bounded live CPU probe. Makes no model/authentication calls."""
import base64
import hashlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))


def main():
    from main import load_config
    from core.remote_agent import RemoteManager
    from core.role_memory import atomic_json
    directory=ROOT/'storage/remote-agy/colab-personal'
    manager=RemoteManager(ROOT,directory,load_config())
    # Preserve the original observation before correcting the exit-zero soft denial.
    remote=manager.transport.rpc('''import pathlib,base64,time,json
_root=pathlib.Path('/content/agy-worker')
_rid='live-cpu-link-20261107-2'
_path=_agy_worker.job_path(_root,_rid)
_row=json.loads(_path.read_text())
_native,_state,_text,_error=_agy_worker.parse_native(_path.with_suffix('.stdout').read_text(),_row.get('exit_code',0))
if _state!=_row['state']:
    _backup=_path.with_name(_rid+'.original-receipt.json')
    if not _backup.exists(): _agy_worker.atomic(_backup,_row)
    _row.update(original_worker_state=_row['state'],state=_state,output=_text,error=_error,
        denied_actions=_native.get('denied_actions',[]),reconciled_at=time.time(),
        reconciliation='Exit-zero soft denial is not completed execution; original receipt preserved')
    _agy_worker.atomic(_path,_row)
_files={}
for _name in ('cpu_link_verification.py','cpu_link_verification.json'):
    _files[_name]=base64.b64encode((_root/'artifacts'/_name).read_bytes()).decode()
_result={'files':_files,'boot_id':_agy_worker.boot_id()}
''')
    evidence=ROOT/'storage/remote-agy/verification/live'
    evidence.mkdir(parents=True,exist_ok=True)
    files={}
    for name,data in remote['files'].items():
        content=base64.b64decode(data)
        (evidence/name).write_bytes(content)
        files[name]={'path':str(evidence/name),'sha256':hashlib.sha256(content).hexdigest()}
    value=json.loads((evidence/'cpu_link_verification.json').read_text(encoding='utf-8'))
    expected=sum(i*i for i in range(1,10001))
    assert value['n']==10000 and value['sum_squares']==expected
    assert value['gpu_present'] is False
    assert value['recalled_label']=='LINK-7C91-BETA'
    receipts=[manager.poll('live-cpu-link-20261107-'+str(i)) for i in (1,2,3)]
    assert [r['state'] for r in receipts]==['COMPLETED','WAITING_APPROVAL','COMPLETED']
    assert len({r['session_id'] for r in receipts})==1
    assert receipts[1]['resumed'] and receipts[2]['resumed']
    assert 'LINK-7C91-BETA' in receipts[0]['output']
    assert not receipts[2].get('denied_actions')
    review_path=directory/'reviews/live-cpu-link-20261107-2.json'
    if review_path.exists():
        review=json.loads(review_path.read_text(encoding='utf-8'))
        review['host_reconciliation']='Original worker COMPLETED flag was incorrect: native shell action denied; current receipt WAITING_APPROVAL. CPU artifact was subsequently executed under request 3.'
        atomic_json(review_path,review)
    result={'real_agy':True,'passed':True,'synthetic':False,'boot_id':remote['boot_id'],
        'cpu_only':True,'independent_result':value,'artifacts':files,'receipts':receipts,
        'scope':'Three bounded live exchanges, native continuity, soft-denial correction and independently recomputed CPU artifact; not a benchmark reproduction'}
    atomic_json(evidence/'receipt.json',result)
    print(json.dumps({'passed':True,'real_agy':True,'session_id':receipts[0]['session_id'],
        'sum_squares':expected,'gpu_present':False,'states':[r['state'] for r in receipts],
        'evidence':str(evidence/'receipt.json')}))


if __name__=='__main__':main()
