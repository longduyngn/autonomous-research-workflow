"""Prepare/delegate bounded Ralph subtasks; review and integration stay with coordinator."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
PACK=ROOT/'docs/ralph-next'
STATE=ROOT/'storage/ralph-next'


def append(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a',encoding='utf8') as stream:
        stream.write(json.dumps({'at_unix':time.time(),**value},ensure_ascii=False)+'\n')


def defer(task,reason,evidence=''):
    value={'task_id':task['id'],'status':'deferred','reason':reason,
        'expected_action':task['title'],'allowed_paths':task['allowed_paths'],
        'required_actor':'host/account owner when outside delegated routine scope',
        'evidence':evidence,'replay_safe':False,
        'resume':'Reconcile evidence and permissions, then prepare a new attempt for '+task['id']}
    append(STATE/'approval_queue.jsonl',value)
    append(STATE/'ledger.jsonl',value)
    print(json.dumps(value,indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['list','prepare','run','defer','record'])
    parser.add_argument('task',nargs='?')
    parser.add_argument('--reason',default='')
    parser.add_argument('--evidence',type=Path)
    args=parser.parse_args()
    catalog=json.loads((PACK/'tasks.json').read_text(encoding='utf8'))
    tasks={t['id']:t for t in catalog['tasks']}
    if args.command=='list':
        for t in tasks.values():print(f"{t['id']} [{t['profile']}] {t['title']} | deps={','.join(t['depends_on']) or '-'}")
        return
    if args.task not in tasks:parser.error('Select a valid task ID')
    task=tasks[args.task]
    if args.command=='defer':
        if not args.reason:parser.error('--reason is required')
        return defer(task,args.reason,str(args.evidence or ''))
    if args.command=='record':
        if not args.evidence or not args.evidence.is_file():parser.error('--evidence must reference a reviewed JSON record')
        evidence=json.loads(args.evidence.read_text(encoding='utf8'))
        if evidence.get('task_id')!=task['id'] or evidence.get('review_passed') is not True or not evidence.get('checks'):
            parser.error('Review record requires matching task_id, review_passed=true and nonempty checks')
        if not isinstance(evidence['checks'],list) or any(not isinstance(c,dict) or c.get('passed') is not True or c.get('exit_code',0)!=0 for c in evidence['checks']):
            parser.error('Every recorded acceptance check must explicitly pass')
        status=evidence.get('status','source_verified')
        if status not in {'source_verified','live_verified','reused_verified'}:parser.error('Invalid reviewed status')
        if task['live_gate'] and status!='live_verified':parser.error('A live-gated task needs live evidence; source checks alone do not close it')
        append(STATE/'ledger.jsonl',{'task_id':task['id'],'status':status,'evidence':str(args.evidence.resolve()),
            'evidence_sha256':hashlib.sha256(args.evidence.read_bytes()).hexdigest()})
        print('Coordinator review recorded; evidence truth remains coordinator responsibility')
        return
    if args.command=='run':
        latest={}
        ledger=STATE/'ledger.jsonl'
        if ledger.exists():
            for line in ledger.read_text(encoding='utf8').splitlines():
                row=json.loads(line);latest[row['task_id']]=row
        missing=[d for d in task['depends_on'] if latest.get(d,{}).get('status') not in {'source_verified','live_verified','reused_verified'}]
        if missing:return defer(task,'Unverified dependencies: '+','.join(missing))
        if latest.get(task['id'],{}).get('status') in {'running','pending_review'}:
            raise SystemExit('Prior attempt requires review/reconciliation; refusing automatic replay')
        if task['live_gate']:return defer(task,'Live/human/resource gate: prepare evidence and use an explicitly authorized managed workflow; no automatic live launch')
        if task['profile']=='coordinator':
            print('Perform this task in the current coordinator chat; do not recursively spawn another coordinator.')
            return
    profiles=json.loads((PACK/'profiles.json').read_text(encoding='utf8'))
    profile=profiles[task['profile']]
    from main import load_config
    from core.workspaces import WorkspaceManager
    from core.providers import EphemeralAgentRunner
    job=STATE/'jobs'/(task['id']+'-'+uuid.uuid4().hex[:12])
    cfg=load_config()
    cfg['isolation']={'enabled':True,'directory':str(job/'workspaces')}
    workspace=WorkspaceManager(ROOT,cfg).prepare(SimpleNamespace(id=1,workspace_path='',source_path='',inputs_json='[]'))
    prompt=('Authorized delegated repository maintenance in this isolated snapshot. Read assigned files first. '
        'Work on ONE bounded task only. Do not read credentials/env, change permissions/hooks, send Discord '
        'messages, start controller services, buy/allocate compute, or edit the original controller. '
        'Do not weaken native permissions. If approval/auth/model/tool access blocks work, stop that action, '
        'save exact evidence and safe resume notes, and report BLOCKED; never claim implementation success. '
        'Keep all edits within allowed_paths. Run meaningful scoped checks. Write .workflow/WORKER_HANDOFF.md '
        'with changed files, actual commands/results, outstanding approvals, next action and evidence paths. '
        'Do not commit or merge automatically.\n\n'+json.dumps(task,indent=2)+'\n\nRead docs/ralph-next/START_HERE.md for the parent contract.')
    (job/'request.json').write_text(json.dumps(task,indent=2),encoding='utf8')
    (job/'prompt.md').write_text(prompt,encoding='utf8')
    if args.command=='prepare':
        print(json.dumps({'task_id':task['id'],'workspace':str(workspace),'prompt':str(job/'prompt.md'),'model_called':False},indent=2))
        return
    model=profile.get('model') or os.environ.get(profile.get('model_env',''),'')
    bindings=STATE/'bindings.json'
    if bindings.exists():model=json.loads(bindings.read_text()).get(task['profile'],{}).get('model',model)
    if not model:return defer(task,'Requested exact model ID unresolved; no silent fallback',str(job))
    provider=profile['provider']
    if provider not in cfg['providers']:return defer(task,'Requested provider profile missing',str(job))
    cfg['roles']['operator'].update(provider=provider,model=model,effort=profile['effort'],timeout_seconds=task['timeout_seconds'])
    if cfg['providers'][provider].get('type')=='agy_cli':
        cfg['providers'][provider].update(mode='auto',sandbox=True,skip_permissions=False)
    append(STATE/'ledger.jsonl',{'task_id':task['id'],'status':'running','job':str(job),'model':model,'effort':profile['effort']})
    runner=EphemeralAgentRunner(ROOT,cfg).for_workspace(workspace)
    try:
        result=runner.run_role('operator',prompt,job/'worker.log',provider_override=provider)
        receipt={'task_id':task['id'],'model':model,'workspace':str(workspace),'cli_success':result.success,
                 'output':result.output,'error':result.error,'metadata':result.structured_data,'status':'pending_review'}
        (job/'result.json').write_text(json.dumps(receipt,indent=2),encoding='utf8')
        if not result.success:return defer(task,result.error or 'Worker failed',str(job/'result.json'))
        subprocess.run(['git','diff','--binary'],cwd=workspace,stdout=(job/'changes.diff').open('wb'),check=True)
        append(STATE/'ledger.jsonl',{'task_id':task['id'],'status':'pending_review','job':str(job),'evidence':str(job/'result.json')})
        print(json.dumps({'status':'pending_review','job':str(job),'warning':'CLI success is not task acceptance. Review untracked new files as well as diff.'}))
    except Exception as exc:
        defer(task,type(exc).__name__+': '+str(exc),str(job))


if __name__=='__main__':main()
