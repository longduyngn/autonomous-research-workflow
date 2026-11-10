"""Off-machine checkpoint/artifact store and manifest v1 (P2.5-02/03).

Layout under a run root (e.g. ``gdrive:workflow-runs/<task-id>/<logical-run-id>``)::

    checkpoints/<checkpoint-id>/<files>    immutable once written
    artifacts/...
    manifests/<checkpoint-id>.json         written LAST, after every file is verified

A checkpoint is resumable only when its manifest says PUBLISHED and the files listed in it exist
with the recorded size/hash. Heavy files never need to come to the local machine.
Credentials (rclone token, HF token) are configured by the user outside this code.
"""
import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path, PurePosixPath

MANIFEST_VERSION = 1
_SEGMENT = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.@=+-]{0,127}$')


def sha256_file(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(chunk), b''):
            digest.update(block)
    return digest.hexdigest()


def safe_relative(path):
    """Reject absolute paths, drive letters, '..' and odd segments (no path escape on the remote)."""
    parts = PurePosixPath(str(path).replace('\\', '/')).parts
    if not parts or any(p in ('', '.', '..') or not _SEGMENT.match(p) for p in parts):
        raise ValueError(f'Unsafe artifact path: {path!r}')
    return '/'.join(parts)


class LocalDirStore:
    """Store rooted at a local directory (tests, local runs, or a Drive-for-desktop folder)."""

    scheme = 'file'

    def __init__(self, root):
        self.root = Path(root)

    def _target(self, rel):
        return self.root / safe_relative(rel)

    def uri(self, rel):
        return f'file:{safe_relative(rel)}'

    def put(self, local_path, rel):
        target = self._target(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_path, target)
        return self.stat(rel)

    def put_text(self, text, rel):
        target = self._target(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + '.tmp')
        tmp.write_text(text, encoding='utf-8')
        tmp.replace(target)

    def get(self, rel, local_path):
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._target(rel), local_path)

    def read_text(self, rel):
        return self._target(rel).read_text(encoding='utf-8')

    def exists(self, rel):
        return self._target(rel).exists()

    def stat(self, rel):
        target = self._target(rel)
        if not target.is_file():
            return None
        return {'uri': self.uri(rel), 'remote_id': safe_relative(rel), 'bytes': target.stat().st_size,
                'sha256': sha256_file(target)}

    def list(self, prefix):
        base = self._target(prefix)
        if not base.exists():
            return []
        return sorted(p.relative_to(self.root).as_posix() for p in base.rglob('*') if p.is_file())

    def delete(self, rel):
        target = self._target(rel)
        if target.is_file():
            target.unlink()


class RcloneStore:
    """rclone remote (Google Drive ``gdrive:``). Verification uses size + the remote MD5 against the local file."""

    scheme = 'rclone'

    def __init__(self, remote='gdrive', base='', root_folder_id='', run=None, executable='rclone'):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', remote):
            raise ValueError('Invalid rclone remote name')
        self.remote, self.base, self.root_folder_id = remote, safe_relative(base) if base else '', root_folder_id
        self.executable = executable
        self.run = run or self._run

    def _run(self, argv, timeout=3600):
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        if done.returncode:
            # stderr may contain paths but never the token (rclone does not print it).
            raise RuntimeError(f'rclone failed ({done.returncode}): {done.stderr.strip()[-500:]}')
        return done.stdout

    def _flags(self):
        return ['--drive-root-folder-id', self.root_folder_id] if self.root_folder_id else []

    def _path(self, rel):
        rel = safe_relative(rel)
        return f'{self.remote}:' + (f'{self.base}/{rel}' if self.base else rel)

    def uri(self, rel):
        return 'rclone:' + self._path(rel)

    def put(self, local_path, rel):
        self.run([self.executable, 'copyto', str(local_path), self._path(rel), *self._flags()])
        info = self.stat(rel)
        local_md5 = hashlib.md5(Path(local_path).read_bytes()).hexdigest()
        if not info or info['bytes'] != Path(local_path).stat().st_size or (info.get('md5') and info['md5'] != local_md5):
            raise RuntimeError(f'Upload verification failed for {rel}')
        info['sha256'] = sha256_file(local_path)
        return info

    def put_text(self, text, rel):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'content'
            path.write_text(text, encoding='utf-8')
            self.run([self.executable, 'copyto', str(path), self._path(rel), *self._flags()])

    def get(self, rel, local_path):
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        self.run([self.executable, 'copyto', self._path(rel), str(local_path), *self._flags()])

    def read_text(self, rel):
        return self.run([self.executable, 'cat', self._path(rel), *self._flags()], timeout=300)

    def stat(self, rel):
        out = self.run([self.executable, 'lsjson', '--hash', '--files-only', self._path(rel), *self._flags()], timeout=300)
        rows = json.loads(out or '[]')
        if not rows:
            return None
        row = rows[0]
        return {'uri': self.uri(rel), 'remote_id': row.get('ID') or safe_relative(rel), 'bytes': row.get('Size'),
                'md5': (row.get('Hashes') or {}).get('md5') or (row.get('Hashes') or {}).get('MD5')}

    def exists(self, rel):
        try:
            return self.stat(rel) is not None
        except RuntimeError:
            return False

    def list(self, prefix):
        try:
            out = self.run([self.executable, 'lsjson', '-R', '--files-only', self._path(prefix), *self._flags()], timeout=300)
        except RuntimeError:
            return []
        return sorted(f"{safe_relative(prefix)}/{row['Path']}" for row in json.loads(out or '[]'))

    def delete(self, rel):
        self.run([self.executable, 'deletefile', self._path(rel), *self._flags()], timeout=300)


# ------------------------------------------------------------------ manifest

def build_manifest(*, task_id, logical_run_id, job_id, checkpoint_id, backend, files, parent_job_id='',
                   runtime_identity='', hardware_requested='', hardware_observed='', code_hash='', data_hash='',
                   config_hash='', environment_hash='', global_step=0, epoch=0, data_cursor=None,
                   checkpoint_schema='', clock=time.time):
    return {'manifest_version': MANIFEST_VERSION, 'task_id': task_id, 'logical_run_id': logical_run_id,
            'job_id': job_id, 'parent_job_id': parent_job_id, 'checkpoint_id': checkpoint_id, 'backend': backend,
            'runtime_identity': runtime_identity, 'hardware_requested': hardware_requested,
            'hardware_observed': hardware_observed, 'code_hash': code_hash, 'data_hash': data_hash,
            'config_hash': config_hash, 'environment_hash': environment_hash, 'global_step': global_step,
            'epoch': epoch, 'data_cursor': data_cursor, 'checkpoint_schema': checkpoint_schema, 'files': files,
            'publication_state': 'PENDING', 'verification_source': '', 'verified_at': None, 'created_at': clock()}


def validate_manifest(manifest):
    required = ('manifest_version', 'task_id', 'logical_run_id', 'checkpoint_id', 'files', 'publication_state')
    missing = [key for key in required if key not in manifest]
    if missing:
        raise ValueError('Manifest misses ' + ', '.join(missing))
    if manifest['manifest_version'] != MANIFEST_VERSION:
        raise ValueError('Unsupported manifest version')
    for item in manifest['files']:
        if not {'uri', 'bytes', 'sha256', 'kind'} <= set(item):
            raise ValueError('Manifest file entries need uri, bytes, sha256 and kind')
    return manifest


def compatibility(manifest, **expected):
    """List of mismatching identity fields (code/data/config/environment) for a resume."""
    return [key for key, value in expected.items() if value and manifest.get(key) and manifest[key] != value]


def publish_checkpoint(store, run_root, checkpoint_id, local_files, manifest_fields, kinds=None, light=()):
    """Upload files to an immutable checkpoint directory, verify each, then publish the manifest last."""
    run_root = safe_relative(run_root)
    checkpoint_id = safe_relative(checkpoint_id)
    manifest_rel = f'{run_root}/manifests/{checkpoint_id}.json'
    if store.exists(manifest_rel):
        raise ValueError('Checkpoint id already published; checkpoints are immutable')
    entries = []
    for local in local_files:
        local = Path(local)
        rel = f'{run_root}/checkpoints/{checkpoint_id}/{safe_relative(local.name)}'
        info = store.put(local, rel)
        expected = sha256_file(local)
        if info.get('sha256') and info['sha256'] != expected:
            raise RuntimeError(f'Hash mismatch after upload: {local.name}')
        entries.append({'uri': info['uri'], 'rel': rel, 'remote_id': info.get('remote_id', ''), 'bytes': local.stat().st_size,
                        'sha256': expected, 'kind': (kinds or {}).get(local.name, 'checkpoint'),
                        'local_download': local.name in set(light)})
    manifest = build_manifest(checkpoint_id=checkpoint_id, files=entries, **manifest_fields)
    manifest.update(publication_state='PUBLISHED', verification_source=store.scheme, verified_at=time.time())
    validate_manifest(manifest)
    store.put_text(json.dumps(manifest, indent=2), manifest_rel)
    return manifest


def published_manifests(store, run_root):
    run_root = safe_relative(run_root)
    found = []
    for rel in store.list(f'{run_root}/manifests'):
        if not rel.endswith('.json'):
            continue
        try:
            manifest = validate_manifest(json.loads(store.read_text(rel)))
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
        if manifest['publication_state'] == 'PUBLISHED':
            found.append(manifest)
    return sorted(found, key=lambda m: (m.get('global_step', 0), m.get('created_at', 0)))


def latest_resumable(store, run_root, **expected):
    """Newest PUBLISHED manifest whose files still exist and whose identity matches."""
    for manifest in reversed(published_manifests(store, run_root)):
        if compatibility(manifest, **expected):
            continue
        files_ok = True
        for item in manifest['files']:
            info = store.stat(item['rel']) if item.get('rel') else None
            if not info or info.get('bytes') != item['bytes']:
                files_ok = False
                break
        if files_ok:
            return manifest
    return None


def prune(store, run_root, keep=3, protect=()):
    """Delete checkpoint files of this run older than the newest ``keep`` published ones (never protected/final)."""
    manifests = published_manifests(store, run_root)
    removable = manifests[:-keep] if keep > 0 else manifests
    removed = []
    for manifest in removable:
        if manifest['checkpoint_id'] in protect or manifest.get('final'):
            continue
        prefix = f"{safe_relative(run_root)}/checkpoints/{manifest['checkpoint_id']}"
        for rel in store.list(prefix):
            store.delete(rel)
            removed.append(rel)
    return removed
