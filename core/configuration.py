"""Validate controller settings without reading credential files."""
import math

# Soft per-role turn budgets: prompt guidance plus an over-budget signal. They never
# stop a run; wall-clock timeouts and progress supervision remain the hard limits.
ROLE_TURN_BUDGET = {'communicator': 5, 'validator': 15, 'researcher': 25,
                    'operator': 35, 'guardian': 20}
ROLE_MAX_TURNS = ROLE_TURN_BUDGET  # legacy name


def role_turn_budget(config, role):
    # `role_max_turns` is the legacy key; `role_turn_budget` wins when both exist.
    merged = {**config.get('role_max_turns', {}), **config.get('role_turn_budget', {})}
    value = merged.get(role, ROLE_TURN_BUDGET.get(role, 15))
    if type(value) is not int or not 1 <= value <= 1000:
        raise ValueError('role_turn_budget must contain integers between 1 and 1000')
    return value


role_max_turns = role_turn_budget  # legacy name


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("Workflow config must be a JSON object")
    limits = {}
    for key in ('role_max_turns', 'role_turn_budget'):
        if not isinstance(config.get(key, {}), dict):
            raise ValueError(key + ' must be an object')
        limits.update(config.get(key, {}))
    for role in set(ROLE_TURN_BUDGET) | set(limits):
        role_turn_budget(config, role)
    monitor=config.get('monitoring',{})
    for name,default,low,high in [('agent_review_seconds',900,1,86400),('max_agent_seconds',21600,1,604800),
                                  ('max_diagnostics_per_job',2,0,10)]:
        value=monitor.get(name,default)
        if type(value) is not int or not low<=value<=high:
            raise ValueError('Invalid monitoring.'+name)
    if type(monitor.get('enabled',True)) is not bool:
        raise ValueError('monitoring.enabled must be boolean')
    orchestrator = config.get("orchestrator", {})
    capacity = orchestrator.get("max_concurrent_tasks", 2)
    attempts = orchestrator.get("max_attempts", 3)
    if type(capacity) is not int or not 1 <= capacity <= 3:
        raise ValueError("max_concurrent_tasks must be an integer between 1 and 3")
    if type(attempts) is not int or not 1 <= attempts <= 100:
        raise ValueError("max_attempts must be an integer between 1 and 100")
    repairs = orchestrator.get('max_plan_repairs', 2)
    if type(repairs) is not int or not 0 <= repairs <= 10:
        raise ValueError('max_plan_repairs must be an integer between 0 and 10')
    timeout = orchestrator.get("task_timeout_seconds", 86400)
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("task_timeout_seconds must be positive and finite")
    providers = config.get("providers", {})
    from core.opencode_config import api_profile
    for name, provider in providers.items():
        if isinstance(provider, dict) and provider.get("api_profile"):
            if provider.get("type") != "opencode_cli":
                raise ValueError(f"Provider {name}: api_profile requires type opencode_cli")
            try:
                api_profile(provider)
            except ValueError as exc:
                raise ValueError(f"Provider {name}: {exc}") from None
    for role, settings in config.get("roles", {}).items():
        if settings.get("provider") not in providers:
            raise ValueError(f"Unknown provider for {role}: {settings.get('provider')}")
        if settings.get("tier", "worker") not in {"research", "worker", "fast"}:
            raise ValueError(f"Unknown model tier for {role}")
        if int(settings.get("timeout_seconds", 600)) < 1:
            raise ValueError(f"Role timeout must be positive: {role}")
        provider = providers[settings["provider"]]
        if role in {"researcher", "operator", "validator"}:
            if provider.get("type") == "gemini_http":
                raise ValueError(f"{role} requires a tool-capable provider")
            if provider.get("type") == "http_llm" and not provider.get("supports_tools"):
                raise ValueError(f"{role} requires supports_tools=true")
    validate_quota_routing(config)
    return config


def validate_quota_routing(config):
    """Opt-in quota router settings; absent or disabled blocks stay fully backward compatible."""
    block = config.get('quota_routing')
    if block is None:
        return
    if not isinstance(block, dict):
        raise ValueError('quota_routing must be an object')
    providers, roles = config.get('providers', {}), config.get('roles', {})
    for name in ('enabled', 'sticky_routes', 'light_validator'):
        if type(block.get(name, False)) is not bool:
            raise ValueError(f'quota_routing.{name} must be boolean')
    for name, low, high in (('poll_seconds', 10, 86400), ('stale_seconds', 1, 604800), ('default_cooldown_seconds', 1, 604800)):
        value = block.get(name, low)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'quota_routing.{name} must be an integer between {low} and {high}')
    reserve = block.get('reserve_percent', 10)
    if not isinstance(reserve, (int, float)) or isinstance(reserve, bool) or not 0 <= reserve < 100:
        raise ValueError('quota_routing.reserve_percent must be in [0, 100)')
    for account, spec in block.get('accounts', {}).items():
        if not isinstance(spec, dict) or type(spec.get('unlimited', False)) is not bool:
            raise ValueError(f'quota_routing.accounts.{account} must be an object with boolean unlimited')
        budget = spec.get('budget_tokens_5h')
        if budget is not None and (type(budget) is not int or budget <= 0):
            raise ValueError(f'quota_routing.accounts.{account}.budget_tokens_5h must be a positive integer')

    def entries(label, items):
        if not isinstance(items, list):
            raise ValueError(f'quota_routing.{label} must be a list of provider/model entries')
        for item in items:
            if not isinstance(item, dict) or item.get('provider') not in providers or not isinstance(item.get('model', ''), str):
                raise ValueError(f'quota_routing.{label} entries need a configured provider and a string model')
    classes = block.get('classes', {})
    for name, items in classes.items():
        entries('classes.' + name, items)
    for name, items in block.get('reserve', {}).items():
        if name not in classes:
            raise ValueError(f'quota_routing.reserve names unknown class {name}')
        entries('reserve.' + name, items)
    for account, items in block.get('workarounds', {}).items():
        entries('workarounds.' + account, items)
    for role, name in block.get('role_classes', {}).items():
        if role not in roles or name not in classes:
            raise ValueError(f'quota_routing.role_classes maps unknown role/class: {role} -> {name}')
    for role, accounts in block.get('requires', {}).items():
        if role not in roles or not isinstance(accounts, list) or not all(isinstance(a, str) for a in accounts):
            raise ValueError(f'quota_routing.requires.{role} must be a list of account names for a configured role')
    if block.get('enabled') and not block.get('role_classes'):
        raise ValueError('quota_routing.enabled needs role_classes; no role would be routed')
