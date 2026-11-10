"""Canonical role and status names, with read compatibility for legacy names (P2.2).

``researcher`` was renamed ``planner`` (status ``RESEARCHING`` -> ``PLANNING``). New records use the
canonical names; stored history keeps whatever it was written with and is mapped on read.
"""
import copy
import warnings

PLANNER = 'planner'
LEGACY_ROLES = {'researcher': PLANNER}
LEGACY_STATUSES = {'RESEARCHING': 'PLANNING'}
LEGACY_PROMPT_FILES = {'roles/researcher.md': 'roles/planner.md'}
ACTION_ROLES = frozenset({PLANNER, 'operator', 'validator'})


def canonical_role(name):
    return LEGACY_ROLES.get(name, name)


def canonical_status(status):
    return LEGACY_STATUSES.get(status, status)


def role_aliases(role):
    """Canonical name first, then legacy names that mean the same role (for reading history)."""
    role = canonical_role(role)
    return [role] + [old for old, new in LEGACY_ROLES.items() if new == role]


def status_aliases(status):
    status = canonical_status(status)
    return [status] + [old for old, new in LEGACY_STATUSES.items() if new == status]


def _merge_key(mapping, where, deprecated):
    """Rename legacy keys of ``mapping`` in place; identical duplicates merge, conflicts fail."""
    if not isinstance(mapping, dict):
        return
    for old, new in LEGACY_ROLES.items():
        if old not in mapping:
            continue
        value = mapping.pop(old)
        if new in mapping and mapping[new] != value:
            raise ValueError(f'{where}: both {old!r} (legacy) and {new!r} are set with different values; '
                             f'remove {old!r}')
        mapping.setdefault(new, value)
        deprecated.append(f'{where}.{old}')


def normalize_config(config):
    """Return a copy of ``config`` with legacy role names mapped to canonical names."""
    if not isinstance(config, dict):
        return config
    config = copy.deepcopy(config)
    deprecated = []
    for key in ('roles', 'role_turn_budget', 'role_max_turns'):
        _merge_key(config.get(key), key, deprecated)
    routing = config.get('quota_routing')
    if isinstance(routing, dict):
        for key in ('role_classes', 'requires'):
            _merge_key(routing.get(key), f'quota_routing.{key}', deprecated)
    planner = (config.get('roles') or {}).get(PLANNER)
    if isinstance(planner, dict) and planner.get('prompt_file') in LEGACY_PROMPT_FILES:
        planner['prompt_file'] = LEGACY_PROMPT_FILES[planner['prompt_file']]
        deprecated.append('roles.planner.prompt_file')
    if deprecated:
        warnings.warn('Deprecated role name "researcher" in config (' + ', '.join(deprecated) +
                      '); it is now "planner".', DeprecationWarning, stacklevel=2)
    return config
