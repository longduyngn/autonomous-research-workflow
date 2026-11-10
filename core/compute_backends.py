"""Colab and Kaggle job adapters over ``CloudTools`` (P2.5-04/05).

Both follow ``core.compute_jobs.JobAdapter``. Heavy outputs live on the shared store (Drive);
``fetch_outputs`` brings home only the files a manifest marks ``local_download``.
"""
import json
import re
from pathlib import Path

LIGHT_PATTERN = r'(?i)^[^/]*\.(?:json|jsonl|csv|txt|log|md|png|jpg|jpeg|svg)$'


def _bootstrap_copy(workspace, entry, request_key, env):
    """Copy a v4 notebook with a first cell exporting controller identity (checkpoint URI, request key)."""
    source = Path(workspace) / entry
    notebook = json.loads(source.read_text(encoding='utf-8'))
    lines = ['import os\n'] + [f"os.environ[{k!r}] = {str(v)!r}\n" for k, v in sorted(env.items()) if v]
    notebook.setdefault('cells', []).insert(0, {'cell_type': 'code', 'id': 'wfboot01', 'metadata': {},
                                                'source': lines, 'outputs': [], 'execution_count': None})
    target = Path(workspace) / '.workflow' / 'compute' / request_key / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(notebook, indent=1), encoding='utf-8')
    return target.relative_to(Path(workspace)).as_posix()


def fetch_from_store(store, manifest, destination, max_bytes):
    """Download light files listed in ``manifest`` (local_download) from the shared store."""
    fetched, skipped = [], []
    for item in manifest.get('files', []):
        if not item.get('local_download'):
            skipped.append(item.get('uri'))
            continue
        if item.get('bytes', 0) > max_bytes:
            skipped.append(item.get('uri'))
            continue
        target = Path(destination) / Path(item['rel']).name
        store.get(item['rel'], target)
        fetched.append({'uri': item['uri'], 'path': str(target), 'bytes': item.get('bytes')})
    return {'fetched': fetched, 'left_remote': skipped}


class KaggleJobAdapter:
    STATES = {'complete': 'COMPLETED', 'error': 'FAILED', 'cancelled': 'CANCELLED', 'canceled': 'CANCELLED',
              'running': 'RUNNING', 'queued': 'QUEUED'}

    def __init__(self, workspace, config, tools=None, store=None):
        from core.cloud import CloudTools
        self.workspace, self.config = Path(workspace), config
        self.tools = tools or CloudTools(workspace, config)
        self.store = store

    def submit(self, spec, request_key, job_id):
        entry = _bootstrap_copy(self.workspace, spec.entry, request_key,
                                {'WORKFLOW_CHECKPOINT_URI': spec.checkpoint_uri, 'WORKFLOW_JOB_REQUEST_KEY': request_key,
                                 'WORKFLOW_PARENT_JOB_ID': spec.parent_job_id})
        args = {'notebook_path': entry, 'hardware': spec.hardware,
                **{k: v for k, v in spec.config.items() if k in ('kaggle_kernel_slug', 'input_files', 'dataset_sources',
                                                                 'competition_sources', 'total', 'stall_seconds')}}
        if spec.budget_seconds:
            args['timeout_seconds'] = int(spec.budget_seconds)  # only a user-stated budget (D6)
        job = self.tools.kaggle_execute_notebook(args)
        if job.get('state') == 'submission_unknown':
            raise RuntimeError('Kaggle push outcome unknown: ' + str(job.get('error', ''))[:200])
        return {'external_id': job['id'], 'details': {'slug': job.get('slug'), 'version': job.get('version'),
                                                      'hardware': job.get('hardware')}}

    def status(self, handle):
        job = self.tools.kaggle_job_status({'job_id': handle.external_id})
        state = self.STATES.get(job.get('state'), 'UNKNOWN')
        return {'state': state, 'version_mismatch': job.get('version_mismatch')}

    def logs_tail(self, handle, cursor, limit):
        # Kaggle exposes the run log only with the outputs after completion.
        return {'cursor': cursor, 'lines': [], 'available': False}

    def fetch_outputs(self, handle, manifest, policy):
        if self.store is not None and manifest.get('files'):
            destination = Path(policy.get('destination') or self.workspace / 'results' / handle.job_id)
            return fetch_from_store(self.store, manifest, destination, policy.get('max_bytes', 50 * 1024 * 1024))
        names = [Path(f['rel']).name for f in manifest.get('files', []) if f.get('local_download')]
        result = self.tools.kaggle_export_data({'job_id': handle.external_id, **({'files': names} if names else {})})
        return {'fetched': result.get('files', []), 'status': result.get('status')}

    def cancel(self, handle, reason):
        receipt = self.tools.kaggle_job_cancel({'job_id': handle.external_id, 'reason': reason})
        if receipt.get('status') == 'cancel_requested':
            # Verify against the CLI: only a cancelled/errored state of our version counts as stopped.
            observed = self.status(handle)
            if observed['state'] in ('CANCELLED', 'FAILED') and not observed.get('version_mismatch'):
                return {'state': 'STOPPED', 'verified': True, 'receipt': receipt.get('status')}
            return {'state': 'CANCEL_REQUESTED', 'verified': False, 'receipt': receipt.get('status')}
        return {'state': 'UNKNOWN', 'verified': False, 'receipt': receipt.get('status')}


class ColabJobAdapter:
    """Wraps the personal-Colab bridge: launch a registered cell once, observe via progress markers."""

    def __init__(self, workspace, config, tools=None, store=None):
        from core.cloud import CloudTools
        self.workspace, self.config = Path(workspace), config
        self.tools = tools or CloudTools(workspace, config)
        self.store = store

    def submit(self, spec, request_key, job_id):
        if not spec.config.get('register_external_id'):
            raise ValueError('Colab jobs need the prepared notebook cell id (config.register_external_id)')
        result = self.tools.colab_job_start({'job_id': job_id})
        if result.get('status') == 'not_resubmitted':
            raise RuntimeError('Colab launch already attempted; reconcile instead of clicking Run again')
        return {'external_id': spec.config['register_external_id'],
                'details': {'browser_result': str(result.get('browser_result', ''))[:200]}}

    def _cell_text(self, handle):
        result = self.tools._colab('/call', {'name': 'get_cells', 'arguments': {'includeOutputs': True}}, timeout=20)
        for cell in (result.get('structuredContent') or {}).get('cells', []):
            if cell.get('id') == handle.external_id:
                return ''.join(''.join(o.get('text', [])) if isinstance(o.get('text', []), list) else str(o.get('text', ''))
                               for o in cell.get('outputs', []))
        raise LookupError('Registered Colab cell not found')

    def status(self, handle):
        text = self._cell_text(handle)
        for line in reversed(text.splitlines()):
            if line.startswith('WORKFLOW_PROGRESS: '):
                data = json.loads(line[len('WORKFLOW_PROGRESS: '):])
                if data.get('job_id') == handle.job_id:
                    state = {'COMPLETED': 'COMPLETED', 'FAILED': 'FAILED'}.get(data.get('state'), 'RUNNING')
                    return {'state': state, 'completed': data.get('completed', 0), 'stage': data.get('stage', '')}
        return {'state': 'UNKNOWN'}

    def logs_tail(self, handle, cursor, limit):
        lines = self._cell_text(handle).splitlines()
        return {'cursor': len(lines), 'lines': lines[max(cursor, len(lines) - limit):]}

    def fetch_outputs(self, handle, manifest, policy):
        if self.store is None:
            raise RuntimeError('Colab outputs are read from the shared store; configure storage')
        destination = Path(policy.get('destination') or self.workspace / 'results' / handle.job_id)
        return fetch_from_store(self.store, manifest, destination, policy.get('max_bytes', 50 * 1024 * 1024))

    def cancel(self, handle, reason):
        result = self.tools.colab_release({})
        released = isinstance(result, dict) and (result.get('released') is True or result.get('status') == 'released')
        return {'state': 'STOPPED' if released else 'CANCEL_REQUESTED', 'verified': bool(released),
                'receipt': {k: result.get(k) for k in ('status', 'released')} if isinstance(result, dict) else {}}
