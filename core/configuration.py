"""Validate controller settings without reading credential files."""
import math


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("Workflow config must be a JSON object")
    orchestrator = config.get("orchestrator", {})
    capacity = orchestrator.get("max_concurrent_tasks", 2)
    attempts = orchestrator.get("max_attempts", 3)
    if type(capacity) is not int or not 1 <= capacity <= 3:
        raise ValueError("max_concurrent_tasks must be an integer between 1 and 3")
    if type(attempts) is not int or not 1 <= attempts <= 100:
        raise ValueError("max_attempts must be an integer between 1 and 100")
    timeout = orchestrator.get("task_timeout_seconds", 86400)
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("task_timeout_seconds must be positive and finite")
    providers = config.get("providers", {})
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
    return config
