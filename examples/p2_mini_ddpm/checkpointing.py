"""Checkpoint/resume protocol for the mini-DDPM example (torch-optional, runs inside Colab/Kaggle/local).

* State is written atomically to a local file, then published to the shared store:
  - ``WORKFLOW_CHECKPOINT_URI=rclone:gdrive:<path>`` -> ``rclone copyto`` (Kaggle/local; rclone configured by the user)
  - ``WORKFLOW_CHECKPOINT_URI=file:<dir>`` or a mounted Drive path (Colab ``/content/drive/MyDrive/...``) -> copy
  Files go to ``checkpoints/<id>/`` and the manifest is written last (same layout as core.artifact_store).
* ``WORKFLOW_PROGRESS: {...}`` lines let the controller's model-free poller observe progress.
"""
import hashlib
import json
import os
import pickle
import shutil
import subprocess
import time
from pathlib import Path


def progress(job_id, completed, total, state='RUNNING', **metrics):
    line = {'job_id': job_id, 'state': state, 'completed': completed, 'total': total, 'heartbeat_at': time.time()}
    if metrics:
        line['metrics'] = {k: float(v) for k, v in metrics.items()}
    print('WORKFLOW_PROGRESS: ' + json.dumps(line), flush=True)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def save_local(state, path, saver=None):
    """Atomic local write; ``saver(state, file)`` defaults to pickle (torch.save in train.py)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'wb') as handle:
        (saver or (lambda s, f: pickle.dump(s, f)))(state, handle)
    os.replace(tmp, path)
    return path


class SharedStore:
    """Minimal publisher matching core.artifact_store's layout, usable without the controller package."""

    def __init__(self, uri):
        if not uri:
            raise ValueError('WORKFLOW_CHECKPOINT_URI is required for remote runs')
        self.uri = uri
        if uri.startswith('rclone:'):
            self.kind, self.base = 'rclone', uri[len('rclone:'):].rstrip('/')
        else:
            self.kind, self.base = 'dir', Path(uri[len('file:'):] if uri.startswith('file:') else uri)

    def _put(self, local, rel):
        if self.kind == 'rclone':
            subprocess.run(['rclone', 'copyto', str(local), f'{self.base}/{rel}'], check=True)
            return f'rclone:{self.base}/{rel}'
        target = Path(self.base) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, target)
        return f'file:{target}'

    def _get(self, rel, local):
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        if self.kind == 'rclone':
            subprocess.run(['rclone', 'copyto', f'{self.base}/{rel}', str(local)], check=True)
        else:
            shutil.copyfile(Path(self.base) / rel, local)

    def _list_manifests(self):
        if self.kind == 'rclone':
            done = subprocess.run(['rclone', 'lsjson', f'{self.base}/manifests'], capture_output=True, text=True)
            return sorted(row['Path'] for row in json.loads(done.stdout or '[]')) if done.returncode == 0 else []
        directory = Path(self.base) / 'manifests'
        return sorted(p.name for p in directory.glob('*.json')) if directory.exists() else []

    def publish(self, checkpoint_id, files, meta):
        entries = []
        for local in files:
            local = Path(local)
            rel = f'checkpoints/{checkpoint_id}/{local.name}'
            uri = self._put(local, rel)
            entries.append({'uri': uri, 'rel': rel, 'bytes': local.stat().st_size, 'sha256': _sha256(local),
                            'kind': meta.get('kinds', {}).get(local.name, 'checkpoint'),
                            'local_download': local.name in meta.get('light', ())})
        manifest = {'manifest_version': 1, 'checkpoint_id': checkpoint_id, 'files': entries,
                    'publication_state': 'PUBLISHED', 'created_at': time.time(),
                    **{k: v for k, v in meta.items() if k not in ('kinds', 'light')}}
        tmp = Path(files[0]).with_name(f'{checkpoint_id}.manifest.json')
        tmp.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        self._put(tmp, f'manifests/{checkpoint_id}.json')  # written last
        return manifest

    def latest(self):
        """Newest published manifest (by global_step) or None."""
        best = None
        for name in self._list_manifests():
            local = Path(os.environ.get('TMPDIR', '/tmp')) / f'wf-manifest-{name}'
            try:
                self._get(f'manifests/{name}', local)
                manifest = json.loads(local.read_text(encoding='utf-8'))
            except Exception:
                continue
            if manifest.get('publication_state') == 'PUBLISHED' and (
                    best is None or manifest.get('global_step', 0) > best.get('global_step', 0)):
                best = manifest
        return best

    def restore(self, manifest, name, destination):
        item = next(f for f in manifest['files'] if Path(f['rel']).name == name)
        self._get(item['rel'], destination)
        if _sha256(destination) != item['sha256']:
            raise RuntimeError('Checkpoint hash mismatch after restore')
        return destination
