"""P2.5-03 live check of the shared store: publish a synthetic checkpoint, restore it, compare hashes.

  python scripts/verify_p2_storage.py --store drive --case publish-restore

Uses config ``storage`` (rclone remote ``gdrive``, base ``workflow-runs``, root folder ID). Requires the
user to have installed rclone and run ``rclone config`` once. The receipt keeps metadata and hashes only.
"""
import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.artifact_store import LocalDirStore, RcloneStore, latest_resumable, publish_checkpoint, sha256_file  # noqa: E402

RECEIPTS = ROOT / 'storage' / 'p2' / 'acceptance' / 'storage'


def store_from_config(kind):
    from main import load_config
    cfg = load_config().get('storage', {})
    if kind == 'drive':
        if not shutil.which('rclone'):
            raise SystemExit('DEFERRED: rclone is not installed. Run `winget install Rclone.Rclone` and `rclone config` '
                             '(remote name gdrive, scope drive) first.')
        return RcloneStore(cfg.get('remote', 'gdrive'), base=cfg.get('base', 'workflow-runs'),
                           root_folder_id=cfg.get('root_folder_id', ''))
    return LocalDirStore(ROOT / 'storage' / 'p2' / 'local-store')


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--store', choices=['drive', 'local'], default='drive')
    parser.add_argument('--case', choices=['publish-restore'], default='publish-restore')
    args = parser.parse_args()
    store = store_from_config(args.store)
    run_root = f"_probe/{time.strftime('%Y%m%d-%H%M%S')}"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        payload = tmp / 'checkpoint.bin'
        payload.write_bytes(b'synthetic-checkpoint-' + str(time.time()).encode() * 1000)
        manifest = publish_checkpoint(store, run_root, 'ck-probe', [payload],
                                      {'task_id': 0, 'logical_run_id': 'probe', 'job_id': 'probe', 'backend': 'local'})
        found = latest_resumable(store, run_root)
        restored = tmp / 'restored.bin'
        store.get(manifest['files'][0]['rel'], restored)
        passed = bool(found) and sha256_file(restored) == sha256_file(payload)
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    receipt = RECEIPTS / f"{time.strftime('%Y%m%d-%H%M%S')}-{args.store}.json"
    receipt.write_text(json.dumps({'store': args.store, 'run_root': run_root, 'manifest_uri_count': len(manifest['files']),
                                   'sha256': manifest['files'][0]['sha256'], 'bytes': manifest['files'][0]['bytes'],
                                   'resumable_found': bool(found), 'passed': passed}, indent=2), encoding='utf-8')
    print(f"{args.store} publish-restore: {'PASS' if passed else 'FAIL'} receipt={receipt}")
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
