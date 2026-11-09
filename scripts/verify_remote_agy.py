"""Offline regression and optional synthetic Linux worker checks. No model calls."""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))

LINUX_CHECK = r'''
import pathlib, uuid, time, json
from unittest.mock import patch,Mock
_base=pathlib.Path('/content/agy-worker/verification')/uuid.uuid4().hex
_base.mkdir(parents=True)
_fake=_base/'fake-agy'
_fake.write_text('#!/usr/bin/env python3\nimport json,pathlib\npathlib.Path("artifact.json").write_text(json.dumps({"value":42}))\nprint(json.dumps({"result":"synthetic CPU artifact saved", "conversationId":"synthetic-session"}))\n')
_fake.chmod(0o700)
_checks={}
_original=_agy_worker.find_agy
_agy_worker.find_agy=lambda: str(_fake)
try:
    for _rid in ('first','second'):
        _request={'request_id':_rid,'message':'Synthetic CPU worker test','model':'synthetic-model','timeout_seconds':10}
        _agy_worker.atomic(_agy_worker.job_path(_base,_rid),{'request_id':_rid,'request':_request,
            'fingerprint':_agy_worker.fingerprint(_request),'state':'QUEUED','created_at':time.time()})
        _agy_worker.run(_base,_rid)
    _first=_agy_worker.status(_base,'first'); _second=_agy_worker.status(_base,'second')
    _checks['completed']=_first['state']==_second['state']=='COMPLETED'
    _checks['session_reused']=_first['session_id']==_second['session_id']=='synthetic-session' and _second['resumed']
    _checks['artifact_readback']=json.loads((_base/'artifact.json').read_text())=={'value':42}
    _checks['portable_history']=len((_base/'history.jsonl').read_text().splitlines())==2
    _request={'request_id':'dedup','message':'synthetic duplicate','model':'synthetic-model'}
    with patch.object(_agy_worker.subprocess,'Popen',return_value=Mock(pid=1234567)) as _spawn:
        _agy_worker.submit(_base,_request); _agy_worker.submit(_base,_request)
        _checks['duplicate_no_second_process']=_spawn.call_count==1
    _fake.write_text('#!/usr/bin/env python3\nimport pathlib,time\npathlib.Path("timeout-artifact.txt").write_text("preserved")\ntime.sleep(20)\n')
    _request={'request_id':'timeout','message':'Synthetic timeout','model':'synthetic-model','timeout_seconds':2}
    _agy_worker.atomic(_agy_worker.job_path(_base,'timeout'),{'request_id':'timeout','request':_request,
        'fingerprint':_agy_worker.fingerprint(_request),'state':'QUEUED','created_at':time.time()})
    _agy_worker.run(_base,'timeout')
    _checks['timeout_unknown_artifacts_preserved']=_agy_worker.status(_base,'timeout')['state']=='UNKNOWN' and (_base/'timeout-artifact.txt').read_text()=='preserved'
    import threading
    _request={'request_id':'cancel','message':'Synthetic cancellation','model':'synthetic-model','timeout_seconds':15}
    _agy_worker.atomic(_agy_worker.job_path(_base,'cancel'),{'request_id':'cancel','request':_request,
        'fingerprint':_agy_worker.fingerprint(_request),'state':'QUEUED','created_at':time.time(),'boot_id':_agy_worker.boot_id()})
    _timer=threading.Timer(1,lambda:_agy_worker.cancel(_base,'cancel'))
    _timer.start()
    _agy_worker.run(_base,'cancel'); _timer.join()
    _checks['owner_cancellation']=_agy_worker.status(_base,'cancel')['state']=='CANCELLED'
finally:
    _agy_worker.find_agy=_original
_result={'synthetic':True,'real_agy_calls':0,'checks':_checks,'passed':all(_checks.values()),'directory':str(_base),'boot_id':_agy_worker.boot_id()}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--linux',action='store_true')
    a = p.parse_args()
    directory = ROOT/'storage/remote-agy/verification'
    directory.mkdir(parents=True,exist_ok=True)
    if a.linux:
        from core.remote_agent import ColabAgentTransport
        from main import load_config
        t=ColabAgentTransport(ROOT,ROOT/'storage/remote-agy/colab-personal',load_config())
        result=t.rpc(LINUX_CHECK)
        (directory/'linux-worker.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result))
        raise SystemExit(0 if result['passed'] else 1)
    env=dict(os.environ)
    # On Windows, system32/bash is a WSL launcher. Use installed Git Bash for
    # the existing POSIX-hook quoting test, without changing the user's PATH.
    git_bash=Path('C:/Program Files/Git/bin/bash.exe')
    if os.name=='nt' and git_bash.exists():
        env['PATH']=str(git_bash.parent)+os.pathsep+env.get('PATH','')
    started=time.monotonic()
    log=directory/'tests.log'
    with log.open('w',encoding='utf-8') as output:
        run=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'],cwd=ROOT,
                           env=env,stdout=output,stderr=subprocess.STDOUT)
    result={'exit_code':run.returncode,'elapsed_seconds':round(time.monotonic()-started,3),
            'log':str(log),'tail':log.read_text(encoding='utf-8',errors='replace').splitlines()[-6:]}
    (directory/'tests.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))
    raise SystemExit(run.returncode)


if __name__=='__main__': main()
