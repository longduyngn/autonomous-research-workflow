"""Standard provider result (P2.4-01): one schema for every CLI/API/mock/extension adapter.

The legacy ``AgentRunResult.success`` stays the compatibility field; the standard view is derived
from it and from ``structured_data`` and is stored in every run receipt. Values a provider did not
report stay ``None`` (never invented as 0).
"""
SCHEMA_VERSION = 1
STATUSES = ('COMPLETED', 'INCOMPLETE', 'WAITING_PROVIDER', 'WAITING_INPUT', 'FAILED', 'CANCELLED', 'UNKNOWN')
ERROR_CLASSES = ('RATE_LIMIT', 'QUOTA_EXHAUSTED', 'AUTH_REQUIRED', 'MODEL_UNAVAILABLE', 'PERMISSION_DENIED',
                 'CONTEXT_LIMIT', 'TRANSPORT', 'PROTOCOL', 'TOOL_FAILED')


def _int(value):
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def normalize_usage(usage, cost=None):
    """Map Claude/Codex/OpenCode/HTTP usage shapes onto one dict; unknown fields stay None."""
    if not isinstance(usage, dict) or not usage:
        return {'input_tokens': None, 'output_tokens': None, 'cache_read_tokens': None, 'cache_write_tokens': None,
                'cost_usd': _float(cost), 'source': 'unavailable'}
    read = usage.get('cache_read_tokens', usage.get('cache_read_input_tokens', usage.get('cached_input_tokens')))
    write = usage.get('cache_write_tokens', usage.get('cache_creation_input_tokens'))
    return {'input_tokens': _int(usage.get('input_tokens', usage.get('prompt_tokens'))),
            'output_tokens': _int(usage.get('output_tokens', usage.get('completion_tokens'))),
            'cache_read_tokens': _int(read), 'cache_write_tokens': _int(write),
            'cost_usd': _float(cost), 'source': 'provider'}


def _float(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def classify_error(result):
    data = result.structured_data or {}
    if result.success:
        return None
    if data.get('error_class') in ERROR_CLASSES:
        return data['error_class']
    quota = data.get('quota') or data.get('pinned_quota')
    if quota:
        return 'QUOTA_EXHAUSTED' if quota.get('exhausted', True) else 'RATE_LIMIT'
    if data.get('retry_safe') and data.get('retry_after'):
        return 'RATE_LIMIT'
    text = (result.error or '').lower()
    for markers, name in ((('authentication', 'login', 'unauthorized', 'api key'), 'AUTH_REQUIRED'),
                          (('denied', 'permission'), 'PERMISSION_DENIED'),
                          (('model not found', 'unknown model', 'no endpoints', 'model is not available'), 'MODEL_UNAVAILABLE'),
                          (('context length', 'context window', 'too many tokens'), 'CONTEXT_LIMIT'),
                          (('timed out', 'timeout', 'connection', 'econnrefused', 'http 5'), 'TRANSPORT'),
                          (('tool',), 'TOOL_FAILED')):
        if any(marker in text for marker in markers):
            return name
    return 'PROTOCOL'


def classify_status(result, error_class):
    data = result.structured_data or {}
    if result.success:
        return 'COMPLETED'
    if 'cancel' in (result.error or '').lower():
        return 'CANCELLED'
    if data.get('budget_exhausted') or data.get('reconcile_required'):
        return 'INCOMPLETE'
    if error_class in {'QUOTA_EXHAUSTED', 'RATE_LIMIT'} and not data.get('pinned_exhausted'):
        return 'WAITING_PROVIDER'
    if error_class == 'AUTH_REQUIRED':
        return 'WAITING_INPUT'
    return 'FAILED'


def standard_result(result, output_path=None):
    """Schema v1 dict for ``result`` (an AgentRunResult)."""
    data = result.structured_data or {}
    error_class = classify_error(result)
    quota = data.get('quota') or data.get('pinned_quota') or {}
    return {
        'schema_version': SCHEMA_VERSION,
        'run_id': data.get('run_id') or None,
        'role': result.role,
        'provider': result.provider_name,
        'model': data.get('model') or None,
        'quota_account': data.get('quota_account') or None,
        'status': classify_status(result, error_class),
        'output_path': output_path,
        'handoff_path': data.get('handoff') or None,
        'quota_hit': bool(quota) or error_class in {'QUOTA_EXHAUSTED', 'RATE_LIMIT'},
        'reset_at': quota.get('resets_at') if quota else None,
        'retry_after': data.get('retry_after'),
        'usage': normalize_usage(data.get('usage'), data.get('total_cost_usd')),
        'error_class': error_class,
        'session_id': data.get('session_id') or None,
        'integration_mode': data.get('integration_mode') or None,
    }
