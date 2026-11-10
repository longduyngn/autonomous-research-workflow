"""Explicit read-only Claude acceptance pilot; spends one short subscription run."""
import argparse
import json
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.providers import EphemeralAgentRunner


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',required=True)
    parser.add_argument('--out',default='storage/handoffs/quota-optimization-20261110/pilot-live')
    a=parser.parse_args()
    out=(ROOT/a.out).resolve()
    if not out.is_relative_to(ROOT):
        parser.error('out must be within the workspace')
    out.mkdir(parents=True,exist_ok=True)
    # Synthetic public fixture only. No campaign names, files, facts, prompts or
    # source excerpts are sent. Temp cwd excludes project CLAUDE.md/skills.
    digest=json.dumps({'state':'PAUSED','observed_at':'2026-01-01T00:00:00Z',
                       'note':'synthetic stale fixture; not a live observation'})
    (out/'status_digest.json').write_text(digest,encoding='utf-8')
    executable=Path.home()/'AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe'
    config={'database_path':str(ROOT/'storage/events.db'),'monitoring':{'enabled':False},
        'providers':{'claude_cli':{'type':'claude_cli','executable':str(executable) if executable.is_file() else 'claude',
            'models':{'worker':'claude-sonnet-5-5'}}},
        'roles':{'guardian':{'provider':'claude_cli','timeout_seconds':90}}}
    prompt=('Read-only workflow acceptance pilot. Do not inspect other files, use tools, execute code, '
        'send messages, access credentials or allocate compute. This is a synthetic fixture, '
        'not a live observation. In <=80 Vietnamese words state the status and timestamp caveat, '
        'without invented claims. Return only that status.\n'+digest)
    with tempfile.TemporaryDirectory(prefix='readonly-fixture-') as tmp:
        runner=EphemeralAgentRunner(tmp,config)
        result=runner.run_role('guardian',prompt,Path(tmp)/'pilot.log')
        # Preserve receipts locally after inference, without placing private
        # project paths into the model's prompt or working directory.
        import shutil
        for path in Path(tmp).glob('pilot.*'):
            shutil.copy2(path,out/path.name)
        result.structured_data['pilot_scope']='synthetic fixture; temp cwd; no campaign content sent'
        result.log_file=str(out/'pilot.log')
        result.structured_data['saved_stream']=str(out/'pilot.stdout.log')
        from core.quota import persist_claude_run
        # Idempotently attach the durable local receipt after temp cwd cleanup.
        persist_claude_run(ROOT/'storage/events.db',result,config=config)
    receipt={'success':result.success,'error':result.error,'output':result.output,
        'duration_seconds':result.duration_seconds,'evidence':result.structured_data}
    (out/'SUMMARY.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:receipt[k] for k in ('success','error','duration_seconds')}))
    return 0 if result.success else 1


if __name__=='__main__':sys.exit(main())
