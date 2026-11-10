"""Bring only light results home (P2.5-07, D7).

``collect_results`` copies the manifest's ``local_download`` files (metrics, logs, samples, reports) into
``storage/results/<task-id>/<logical-run-id>/`` and writes ``artifact_index.json`` recording where every
artifact is and how it was checked:

* ``remote_verified`` - heavy file stays on the shared store; size re-checked there.
* ``local_fetched``   - light file copied home and its sha256 matched the manifest.
* ``unvalidated``     - listed but missing or mismatching; never counted as evidence.
"""
import json
from pathlib import Path

from core.artifact_store import sha256_file, validate_manifest


def collect_results(store, manifest, results_root, max_bytes=50 * 1024 * 1024):
    validate_manifest(manifest)
    target = Path(results_root) / str(manifest['task_id']) / manifest['logical_run_id']
    target.mkdir(parents=True, exist_ok=True)
    index = []
    for item in manifest['files']:
        entry = {'uri': item['uri'], 'kind': item.get('kind'), 'bytes': item.get('bytes'), 'sha256': item.get('sha256')}
        if item.get('local_download') and (item.get('bytes') or 0) <= max_bytes:
            local = target / Path(item['rel']).name
            try:
                store.get(item['rel'], local)
                ok = sha256_file(local) == item['sha256']
            except Exception:
                ok = False
            entry.update(state='local_fetched' if ok else 'unvalidated', path=str(local) if ok else None)
            if not ok and local.exists():
                local.unlink()
        else:
            info = None
            try:
                info = store.stat(item['rel'])
            except Exception:
                info = None
            entry['state'] = 'remote_verified' if info and info.get('bytes') == item.get('bytes') else 'unvalidated'
        index.append(entry)
    (target / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    (target / 'artifact_index.json').write_text(json.dumps({'checkpoint_id': manifest['checkpoint_id'],
                                                            'artifacts': index}, indent=2), encoding='utf-8')
    return {'directory': str(target), 'artifacts': index,
            'complete': all(a['state'] != 'unvalidated' for a in index)}
