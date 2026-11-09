"""Publish already verified CPU pilot metrics, explicitly as a post-run report."""
import hashlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))


def main():
    from main import load_dotenv,load_config
    from core.role_memory import atomic_json
    load_dotenv(ROOT/'.env');load_dotenv(ROOT/'config/.env')
    d=ROOT/'storage/remote-claude/claude-pair-20261107-1'
    receipt=json.loads((d/'verification.json').read_text(encoding='utf-8'))
    if not receipt.get('passed'):raise SystemExit('Independent verification must pass first')
    saved=d/'wandb-publication.json'
    if saved.exists():
        previous=json.loads(saved.read_text())
        if previous.get('status')=='published':print(json.dumps(previous));return
    import wandb
    try:
        if not wandb.login(prompt=False,verify=True,timeout=10):raise RuntimeError('LoginUnavailable')
        run=wandb.init(project=load_config().get('monitoring',{}).get('project','antigravity-research'),
            id='cc'+hashlib.sha256(receipt['job_id'].encode()).hexdigest()[:24],resume='allow',mode='online',
            dir=str(d),config={'workflow_job_id':receipt['job_id'],'capture_mode':'post_run_verification',
                'scope':'CPU acceptance, deliberate fault injection','model':'claude-sonnet-5-5'},
            settings=wandb.Settings(console='off',disable_git=True,save_code=False,x_disable_meta=True,init_timeout=15))
        metrics={'workflow/completed':6,'workflow/total':6,'verification/passed':1,
            'verification/test_cases':receipt['test_cases'],'verification/baseline_expected_fail':1,
            'verification/repaired_pass':1,'workflow/wall_seconds':receipt['wall_seconds'],
            'agents/native_turns':receipt['native_turns'],
            'agents/messages_sent':sum(r['messages_sent'] for r in receipt['roles'].values()),
            **{'tokens/'+k:v for k,v in receipt.get('cumulative_model_usage',{}).items()}}
        run.log(metrics);run.summary.update(metrics)
        result={'status':'published','url':run.url,'capture_mode':'post_run_verification'}
        run.finish()
    except Exception as exc:
        result={'status':'unavailable','error_type':type(exc).__name__}
    atomic_json(saved,result);print(json.dumps(result))


if __name__=='__main__':main()
