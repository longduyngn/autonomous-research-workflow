"""Explicit CPU pilot for two Claude Code roles on the managed Colab VM."""
import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
sys.stdout.reconfigure(encoding='utf-8',errors='replace')


def main():
    from main import load_config,load_dotenv
    from core.remote_claude import ClaudeCloud
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['setup','auth','start','run','status','collect'])
    p.add_argument('--job-id',default='claude-auth-20261107-1')
    p.add_argument('--kind',default='smoke',choices=['smoke','pair'])
    p.add_argument('--full',action='store_true',help='Print the full sanitized native receipt')
    p.add_argument('--no-env',action='store_true',help='Skip dotenv loading (status/collect need no local Claude login)')
    a=p.parse_args()
    if not a.no_env:
        load_dotenv(ROOT/'.env'); load_dotenv(ROOT/'config/.env')
    cloud=ClaudeCloud(ROOT,load_config())
    if a.action=='setup': result=cloud.deploy()
    elif a.action=='auth': result=cloud.inject_token()
    elif a.action=='start': result=cloud.start(a.job_id,a.kind)
    elif a.action=='run':
        cloud.deploy();cloud.inject_token();result=cloud.start(a.job_id,a.kind)
    elif a.action=='collect':result=cloud.collect(a.job_id)
    else: result=cloud.status(a.job_id)
    if a.action=='status':
        from core.role_memory import atomic_json
        atomic_json(ROOT/'storage/remote-claude'/a.job_id/'status.json',result)
        if not a.full:
            result={k:result[k] for k in ('job_id','state','stage','completed','total','roles','error',
                'started_at','finished_at','auth','nonce_match','quota_snapshots','quota_route') if k in result}
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
