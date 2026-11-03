"""Reversible archival of failed task snapshots, preserving audit evidence."""
import hashlib
import json
from datetime import datetime
from pathlib import Path
from core.models import utc_now_iso


def archive_task(db, controller, task_id, archive_name):
    root = Path(controller).resolve()
    task = db.get_task(task_id)
    if not task or task.status not in {'ESCALATED', 'FAILED'} or task.lease_owner:
        raise ValueError('Only unowned, failed terminal tasks can be archived')
    source = Path(task.workspace_path).resolve()
    expected = root / 'storage/workspaces' / f'task_{task.id:05d}'
    destination = (root / 'storage/archive' / archive_name / source.name).resolve()
    if source != expected or not source.is_dir() or source.is_symlink():
        raise ValueError('Archive source must be the registered task snapshot under storage/workspaces')
    if not destination.is_relative_to(root / 'storage/archive') or destination.exists():
        raise ValueError('Archive destination must be new and inside storage/archive')
    if task.remote_job_id:
        raise ValueError('Inspect remote job before archiving')
    inherited_jobs = []
    for path in (source / '.workflow/cloud_jobs').glob('*/job.json'):
        job = json.loads(path.read_text(encoding='utf-8'))
        if job.get('state') not in {'complete', 'completed', 'error', 'cancelled', 'failed'}:
            original = root / '.workflow/cloud_jobs' / path.parent.name / 'job.json'
            prior = json.loads(original.read_text(encoding='utf-8')) if original.exists() else {}
            if (job.get('created_at', float('inf')) < datetime.fromisoformat(task.created_at).timestamp()
                    and job.get('fingerprint') and all(job.get(k) == prior.get(k) for k in ('id', 'slug', 'fingerprint'))):
                # Old snapshots copied controller runtime state before it was
                # excluded. Keep the copy, but leave its actual owner untouched.
                inherited_jobs.append({'copy': str(path.relative_to(source)), 'owner_record': str(original),
                    'reason': 'Matching controller job predates this task; this is inherited state, not a submission by this task'})
            else:
                raise ValueError('Unresolved cloud job must be inspected before archiving')
    # Verify every path before moving; this never recursively deletes a snapshot.
    files = {}
    for path in source.rglob('*'):
        if path.is_symlink():
            raise ValueError('Archive contains a symbolic link; inspect it first')
        if path.is_file():
            files[path.relative_to(source).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {'task_id': task.id, 'archived_at_utc': utc_now_iso(), 'original_workspace': str(source),
                'archive_workspace': str(destination), 'status': task.status,
                'feedback': task.validation_feedback, 'context_log': task.context_log_path,
                'inherited_cloud_jobs': inherited_jobs,
                'operation_state': 'prepared',
                'files_sha256': files, 'restore': 'Move the snapshot back to original_workspace and update the database path; retry through a new task instead of executing archived hooks.'}
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = destination.parent / f'task_{task.id:05d}.manifest.json'
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding='utf-8'))
        if previous.get('operation_state') != 'deferred':
            raise ValueError('Archive manifest already exists')
        manifest['previous_attempt'] = previous
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    try:
        source.rename(destination)
    except OSError as exc:
        manifest['operation_state'] = 'deferred'
        manifest['error'] = str(exc)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        db.log_event('TASK_ARCHIVE_DEFERRED', 'maintenance', 'Snapshot move deferred; original preserved',
                     task_id=task.id, severity='WARN', payload={'manifest': str(manifest_path), 'error': str(exc)})
        raise
    manifest['operation_state'] = 'moved'
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    db.update_task(task.id, workspace_path=str(destination))
    db.log_event('TASK_ARCHIVED', 'maintenance', f'Preserved failed snapshot in {destination}',
                 task_id=task.id, payload={'manifest': str(manifest_path), 'files': len(files)})
    return manifest
