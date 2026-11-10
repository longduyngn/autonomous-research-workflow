"""Short structured role handoff (P2.6-03). Clear prose fields, no compression dialect.

A handoff records what was observed, not verified claims. The controller keeps it bounded so the
next provider reads it in one pass; plan, checks and job identity are always re-sent in the prompt.
"""
import json
from pathlib import Path

FIELDS = ('task_id', 'role', 'iteration_id', 'plan_revision', 'plan_hash', 'feedback_revision',
          'completed_actions', 'evidence_paths', 'acceptance_results', 'pending_actions',
          'unresolved_effects', 'active_job_ids', 'checkpoint_manifest_uri', 'next_safe_action')
LIST_FIELDS = ('completed_actions', 'evidence_paths', 'acceptance_results', 'pending_actions',
               'unresolved_effects', 'active_job_ids')
MAX_BYTES = 8192
MAX_ITEMS = 20


def build_handoff(**values):
    unknown = set(values) - set(FIELDS) - {'extra'}
    if unknown:
        raise ValueError('Unknown handoff fields: ' + ', '.join(sorted(unknown)))
    handoff = {field: values.get(field, [] if field in LIST_FIELDS else None) for field in FIELDS}
    for field in LIST_FIELDS:
        items = handoff[field] or []
        if not isinstance(items, list):
            raise ValueError(f'{field} must be a list')
        handoff[field] = [str(item)[:400] for item in items[-MAX_ITEMS:]]
    if not handoff['task_id'] or not handoff['role'] or not handoff['next_safe_action']:
        raise ValueError('A handoff needs task_id, role and next_safe_action')
    if values.get('extra'):
        handoff['extra'] = values['extra']
    text = json.dumps(handoff, ensure_ascii=False)
    while len(text.encode('utf-8')) > MAX_BYTES and handoff.get('extra'):
        handoff.pop('extra')
        text = json.dumps(handoff, ensure_ascii=False)
    if len(text.encode('utf-8')) > MAX_BYTES:
        for field in LIST_FIELDS:
            handoff[field] = handoff[field][-5:]
        text = json.dumps(handoff, ensure_ascii=False)
    return handoff


def write_handoff(path, **values):
    from core.role_memory import atomic_json
    handoff = build_handoff(**values)
    atomic_json(Path(path), handoff)
    return handoff
