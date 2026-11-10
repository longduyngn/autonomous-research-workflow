"""API profiles for the OpenCode agent loop (P2.1-02).

A provider of type ``opencode_cli`` may carry an ``api_profile``. One adapter serves
every profile; profiles differ only in vendor, endpoint, credential variable,
quota account and model choice. Generated OpenCode configuration references the
credential by environment-variable name (``{env:NAME}``) and never contains its value.
OpenCode runs with an isolated XDG home so global/user/Claude Code configuration
cannot change the route the controller selected.
"""
import json
from pathlib import Path

VENDORS = {
    'openrouter': {'base_url': 'https://openrouter.ai/api/v1', 'api_key_env': 'OPENROUTER_API_KEY'},
    'openai': {'base_url': 'https://api.openai.com/v1', 'api_key_env': 'OPENAI_API_KEY'},
    'deepseek': {'base_url': 'https://api.deepseek.com/v1', 'api_key_env': 'DEEPSEEK_API_KEY'},
    'gemini': {'base_url': 'https://generativelanguage.googleapis.com/v1beta/openai', 'api_key_env': 'GEMINI_API_KEY'},
    'anthropic': {'base_url': 'https://api.anthropic.com/v1', 'api_key_env': 'ANTHROPIC_API_KEY',
                  'npm': '@ai-sdk/anthropic'},
    'local': {'base_url': '', 'api_key_env': '', 'require_api_key': False},
}
ISOLATION_FLAGS = {
    'OPENCODE_DISABLE_AUTOUPDATE': 'true', 'OPENCODE_DISABLE_MODELS_FETCH': 'true',
    'OPENCODE_DISABLE_CLAUDE_CODE': 'true', 'OPENCODE_DISABLE_PROJECT_CONFIG': 'true',
    'OPENCODE_DISABLE_DEFAULT_PLUGINS': 'true', 'OPENCODE_DISABLE_EXTERNAL_SKILLS': 'true',
    'OPENCODE_DISABLE_LSP_DOWNLOAD': 'true',
}
# Inherited variables that could redirect OpenCode to another config or account.
STRIP_PREFIXES = ('OPENCODE_',)


def api_profile(provider):
    """Normalized profile dict for an opencode_cli provider, or None (plain OpenCode)."""
    raw = provider.get('api_profile')
    if not raw:
        return None
    vendor = raw.get('vendor', '')
    if vendor not in VENDORS:
        raise ValueError(f'Unknown API vendor {vendor!r}; choose one of {sorted(VENDORS)}')
    base = VENDORS[vendor]
    profile = {
        'vendor': vendor,
        'provider_id': raw.get('provider_id') or vendor,
        'base_url': raw.get('base_url') or base['base_url'],
        'api_key_env': raw.get('api_key_env', base['api_key_env']),
        'require_api_key': raw.get('require_api_key', base.get('require_api_key', True)),
        'npm': raw.get('npm') or base.get('npm', '@ai-sdk/openai-compatible'),
        'account': raw.get('account') or f'api:{vendor}',
        'free_only': bool(raw.get('free_only', False)),
        'auto_free': bool(raw.get('auto_free', False)),
        'min_context': int(raw.get('min_context', 32768)),
    }
    if not profile['base_url']:
        raise ValueError(f'API profile {vendor} needs base_url')
    if profile['auto_free'] and vendor != 'openrouter':
        raise ValueError('auto_free model discovery is only implemented for OpenRouter')
    if profile['require_api_key'] and not profile['api_key_env']:
        raise ValueError(f'API profile {vendor} needs api_key_env')
    return profile


def check_credential(profile, env):
    """Raise when the credential variable is unset; the value itself is never inspected further."""
    if profile['require_api_key'] and not (env.get(profile['api_key_env']) or '').strip():
        raise ValueError(f"Missing {profile['api_key_env']} for API profile {profile['vendor']}")


def qualified_model(profile, model):
    """OpenCode model reference ``<provider_id>/<model>``; vendor model IDs may contain '/'."""
    prefix = profile['provider_id'] + '/'
    return model if model.startswith(prefix) and model.count('/') > 1 else prefix + model


def bare_model(profile, model):
    prefix = profile['provider_id'] + '/'
    return model[len(prefix):] if model.startswith(prefix) and model.count('/') > 1 else model


def runtime_config(profile, model, permission=None):
    """OpenCode config content for one invocation: exactly one provider and one model."""
    bare = bare_model(profile, model)
    options = {'baseURL': profile['base_url']}
    if profile['api_key_env']:
        options['apiKey'] = '{env:' + profile['api_key_env'] + '}'
    config = {
        '$schema': 'https://opencode.ai/config.json',
        'autoupdate': False,
        'share': 'disabled',
        'provider': {profile['provider_id']: {
            'npm': profile['npm'], 'name': profile['vendor'], 'options': options,
            'models': {bare: {'name': bare, 'tool_call': True}}}},
        'model': qualified_model(profile, bare),
        'small_model': qualified_model(profile, bare),
    }
    if permission is not None:
        config['permission'] = permission
    return config


def isolated_env(base_env, home, config):
    """Child environment with an isolated XDG home and the generated config content."""
    home = Path(home)
    env = {k: v for k, v in base_env.items() if not k.startswith(STRIP_PREFIXES)}
    for name in ('XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME'):
        path = home / name.split('_')[1].lower()
        path.mkdir(parents=True, exist_ok=True)
        env[name] = str(path)
    env.update(ISOLATION_FLAGS)
    env['OPENCODE_CONFIG_CONTENT'] = json.dumps(config)
    return env
