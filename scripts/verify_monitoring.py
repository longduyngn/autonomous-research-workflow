"""A synthetic tracking smoke test in an isolated DB; never starts GPU jobs."""
import argparse
import json
import sys
import time
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--online',action='store_true')
    args=p.parse_args()
    from core.database import WorkspaceDatabase
    from core.monitoring import ProgressStore,wandb_observation
    from core.experiment_progress import ExperimentProgress
    from main import load_config
    directory=ROOT/'storage/verification-local/monitoring'/uuid.uuid4().hex
    directory.mkdir(parents=True)
    db=WorkspaceDatabase(directory/'events.db')
    task=db.create_task('SYNTHETIC W&B integration probe; no research computation',logs_dir=directory/'logs')
    store=ProgressStore(db)
    job,_=store.register(task.id,'synthetic-smoke','local',total=3)
    tracking=load_config().get('monitoring',{})
    writer=ExperimentProgress(job['id'],directory/'progress',total=3,mode='online' if args.online else 'offline',
                              project=tracking.get('project','antigravity-research'),entity=tracking.get('entity'))
    if writer.run:
        writer.run.config.update({'synthetic_integration_probe':True,'no_gpu_compute':True})
    for n in (1,2,3):
        writer.update(n,stage='synthetic-check',metrics={'synthetic_metric':n/3})
        store.observe(job['id'],dict(writer.data))
    writer.finish()
    if args.online:
        if not writer.data.get('wandb_path') or not writer.data.get('wandb_url'):
            raise RuntimeError('Online W&B run was not initialized; no live success claimed')
        store.attach_wandb(job['id'],writer.data['wandb_path'])
        for _ in range(8):
            try:
                observed=wandb_observation(writer.data['wandb_path'])
                if observed['completed']==3 and observed['state']=='COMPLETED':break
            except Exception: observed={}
            time.sleep(2)
        else: raise RuntimeError('W&B API did not verify finished progress')
        store.observe(job['id'],observed,'wandb')
    else:
        store.observe(job['id'],dict(writer.data),'local')
    receipt={'mode':'online' if args.online else 'offline','synthetic':True,'gpu_started':False,
             'job':store.get(job['id']),'directory':str(directory)}
    path=ROOT/'storage/verification-local'/('wandb-online-receipt.json' if args.online else 'wandb-offline-receipt.json')
    path.write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps({'receipt':str(path),'wandb_url':writer.data.get('wandb_url'),'mode':receipt['mode']}))


if __name__=='__main__':main()
