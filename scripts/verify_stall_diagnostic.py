"""Explicit live read-only diagnostic probe. No real experiment is touched."""
import json
import sys
import time
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))


def main():
    from main import load_config,load_dotenv
    from core.database import WorkspaceDatabase
    from core.logger import ContextLogManager
    from core.orchestrator import HybridOrchestrator
    load_dotenv(ROOT/'.env')
    cfg=load_config()
    directory=ROOT/'storage/verification-local/diagnostic'/uuid.uuid4().hex
    directory.mkdir(parents=True)
    db=WorkspaceDatabase(directory/'events.db')
    orch=HybridOrchestrator(ROOT,cfg,db,ContextLogManager(directory/'logs'),None)
    task=db.create_task('SYNTHETIC read-only stall diagnosis; no real compute',logs_dir=directory/'logs')
    task=db.update_task(task.id,status='OPERATING',workspace_path=str(directory))
    verdict=orch.supervisor._consult(task,{'synthetic_probe':True,'kind':'job','job':{
        'state':'RUNNING','completed':20,'total':100,'progress_age_seconds':1200,
        'heartbeat_age_seconds':5,'telemetry_status':'local','budget_remaining_seconds':20000},
        'note':'No process/GPU observation exists. Do not infer a confirmed dead job solely from counter stagnation.'})
    path=ROOT/'storage/verification-local/stall-diagnostic-live-receipt.json'
    path.write_text(json.dumps({'synthetic':True,'readonly':True,'gpu_started':False,'verdict':verdict,
                               'directory':str(directory)},indent=2),encoding='utf-8')
    print(json.dumps({'receipt':str(path),'verdict':verdict}))
    if verdict.get('reason','').startswith(('Diagnostic unavailable','Diagnostic failed')):
        raise SystemExit(1)


if __name__=='__main__':main()
