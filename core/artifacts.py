"""Workspace-contained artifact receipts, compatible with the original JSONL schema."""
import hashlib
import json
from pathlib import Path


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def register(root, name, path, metadata=None):
    from core.tools import AgentTools
    from core.role_memory import RoleMemory
    root = Path(root).resolve()
    target = AgentTools(root, 'operator').path(path)
    if not target.is_file():
        raise FileNotFoundError(target)
    record = {'path': str(target.relative_to(root)), 'bytes': target.stat().st_size,
              'sha256': file_hash(target)}
    if name:
        record['name'] = name
    if metadata is not None:
        if not isinstance(metadata, dict):
            raise ValueError('metadata must be an object')
        record['metadata'] = metadata
    receipt = AgentTools(root, 'operator').path('.workflow/artifact-receipts.jsonl')
    receipt.parent.mkdir(parents=True, exist_ok=True)
    with RoleMemory(AgentTools(root, 'operator').path('.workflow/artifact-lock'), 'operator').locked():
        with receipt.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
    return record


def verify(root):
    from core.tools import AgentTools
    paths = AgentTools(root, 'validator')
    receipt = paths.path('.workflow/artifact-receipts.jsonl')
    results = []
    if not receipt.exists():
        return {'results': [], 'ok': True}
    with receipt.open(encoding='utf-8') as stream:
        for number, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
                target = paths.path(row['path'])
                status = ('missing' if not target.is_file() else 'ok' if
                          target.stat().st_size == row['bytes'] and file_hash(target) == row['sha256']
                          else 'hash-mismatch')
                results.append({'path': row['path'], 'status': status})
            except (ValueError, KeyError, TypeError, OSError) as exc:
                results.append({'line': number, 'status': 'invalid', 'error': str(exc)[:200]})
    return {'results': results, 'ok': all(r['status'] == 'ok' for r in results)}
