"""Portable Colab/Kaggle progress writer. Local receipts precede optional W&B."""
import json
import math
import os
import re
import time
import threading
from pathlib import Path


def numeric_metrics(metrics):
    if not isinstance(metrics, dict) or len(metrics) > 100:
        raise ValueError('Invalid metrics')
    for key, value in metrics.items():
        if (not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_/.-]{0,79}', key)
                or re.search('api.?key|password|secret|credential', key, re.I)
                or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValueError('Use safe metric names and finite numeric values')
    return metrics


class ExperimentProgress:
    def __init__(self, job_id, directory, *, total=0, project='antigravity-research',
                 entity=None, mode='offline', run_id=None, sdk=None, heartbeat_seconds=60,
                 budget_seconds=None, checkpoint_callback=None):
        if not re.fullmatch('[a-zA-Z0-9_-]{1,64}', job_id):
            raise ValueError('Invalid job ID')
        if mode not in {'online', 'offline', 'disabled'}:
            raise ValueError('Invalid tracking mode')
        budget_seconds=float(os.environ.get('WORKFLOW_JOB_BUDGET_SECONDS','43200')) if budget_seconds is None else budget_seconds
        if (not isinstance(heartbeat_seconds,(int,float)) or not math.isfinite(heartbeat_seconds)
                or heartbeat_seconds<0 or (heartbeat_seconds and heartbeat_seconds<1)
                or not isinstance(budget_seconds,(int,float)) or not math.isfinite(budget_seconds) or budget_seconds<0):
            raise ValueError('Invalid heartbeat or job budget')
        self.job_id, self.run = job_id, None
        self._lock=threading.RLock()
        self._stop=threading.Event()
        self.budget_seconds=budget_seconds
        self.checkpoint_callback=checkpoint_callback
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory/(job_id+'.json')
        existed = self.path.exists()
        self.data = json.loads(self.path.read_text()) if existed else {
            'job_id': job_id, 'completed': 0, 'total': total, 'stage': 'starting',
            'state': 'RUNNING', 'created_at': time.time(), 'sequence': 0}
        if self.data['state'] != 'RUNNING' or (existed and self.data['total'] != total):
            raise ValueError('New scope or finished job requires a new identity')
        self.data.update(tracking_mode=mode, tracking_status=mode)
        if mode != 'disabled':
            try:
                if sdk is None:
                    import wandb as sdk
                self.run = sdk.init(project=project, entity=entity, id=run_id or job_id,
                    resume='allow' if mode == 'online' else None, mode=mode,
                    dir=str(self.directory), config={'workflow_job_id': job_id},
                    settings=sdk.Settings(console='off', disable_git=True, save_code=False,
                                          x_disable_meta=True, init_timeout=15))
                path=self.run.path
                self.data.update(wandb_path=(path if isinstance(path,str) else '/'.join(path)) if mode=='online' else None,
                                 wandb_url=self.run.url if mode == 'online' else None)
            except Exception as exc:
                self.data['tracking_status'] = 'unavailable:'+type(exc).__name__
        self._save()
        if heartbeat_seconds:
            def beat():
                while not self._stop.wait(heartbeat_seconds):
                    try: self._heartbeat_once()
                    except Exception: pass  # Sampling is optional, not job correctness.
            threading.Thread(target=beat,daemon=True,name='experiment-heartbeat').start()

    def _save(self):
        self.data['heartbeat_at'] = time.time()
        self.data['sequence'] += 1
        tmp = self.path.with_suffix('.tmp')
        with tmp.open('w', encoding='utf-8') as f:
            json.dump(self.data, f, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def update(self, completed=None, *, stage=None, total=None, metrics=None):
        with self._lock:
            if self.data['state']!='RUNNING': raise ValueError('Finished job requires a new identity')
            if self.budget_seconds and time.time()-self.data['created_at']>=self.budget_seconds:
                self.data['stop_requested']='budget_exhausted'
                if self.checkpoint_callback:
                    self.checkpoint_callback()  # Called at a main-loop boundary, never from heartbeat.
                self.finish(exit_code=124)
                raise TimeoutError('Job budget exhausted at a safe progress boundary')
            return self._update(completed,stage=stage,total=total,metrics=metrics)

    def _heartbeat_once(self):
        import subprocess
        metrics=dict(self.data.get('metrics',{}))
        try:
            text=subprocess.check_output(['nvidia-smi','--query-gpu=utilization.gpu,memory.used',
                                          '--format=csv,noheader,nounits'],text=True,timeout=2,
                                         creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            util,memory=text.splitlines()[0].split(',')
            metrics.update(gpu_util_pct=float(util),gpu_memory_mb=float(memory))
        except (OSError,ValueError,subprocess.SubprocessError): pass
        with self._lock:
            if self.data['state']=='RUNNING':
                return self._update(metrics=metrics)

    def _update(self, completed=None, *, stage=None, total=None, metrics=None):
        done = self.data['completed'] if completed is None else completed
        limit = self.data['total'] if total is None else total
        if (not all(isinstance(x,(int,float)) and math.isfinite(x) for x in (done,limit))
                or done < self.data['completed'] or limit < 0 or (limit and done > limit)
                or (self.data['total'] and limit != self.data['total'])):
            raise ValueError('Progress must be monotonic and scope fixed')
        if self.data['state'] != 'RUNNING':
            raise ValueError('Finished job requires a new identity')
        if stage is not None and not re.fullmatch(r'[a-zA-Z0-9 _./-]{1,80}', stage):
            raise ValueError('Invalid stage')
        self.data.update(completed=done, total=limit, stage=stage or self.data['stage'],
                         metrics=numeric_metrics(metrics or {}))
        self._save()
        payload = {'workflow/'+k:self.data[k] for k in
                   ('completed','total','heartbeat_at','stage','job_id','sequence')}
        payload.update(self.data['metrics'])
        if self.run:
            try:
                self.run.log(payload)
                self.run.summary.update(payload)
            except Exception as exc:
                self.data['tracking_status'] = 'unavailable:'+type(exc).__name__
        print('WORKFLOW_PROGRESS: '+json.dumps(self.data, allow_nan=False), flush=True)
        return dict(self.data)

    def finish(self, exit_code=0):
        with self._lock:
            return self._finish(exit_code)

    def _finish(self, exit_code=0):
        if self.data['state']!='RUNNING': return dict(self.data)
        self._stop.set()
        self.data.update(state='COMPLETED' if exit_code == 0 else 'FAILED', exit_code=exit_code)
        self._save()
        # The execution result is visible before a slow telemetry flush.
        print('WORKFLOW_PROGRESS: '+json.dumps(self.data, allow_nan=False), flush=True)
        if self.run:
            try:
                self.run.summary['workflow/state'] = self.data['state']
                self.run.finish(exit_code=exit_code)
            except Exception as exc:
                self.data['tracking_status'] = 'unavailable:'+type(exc).__name__
        return dict(self.data)
