"""Read-only environment/config diagnostics; does not call models or allocate GPUs."""
import importlib.util
import os
import sys
from core.providers import EphemeralAgentRunner
from core.executables import find_executable


def inspect_environment(root, config):
    runner = EphemeralAgentRunner(root, config)
    report = {"python": sys.executable, "concurrency": config.get("orchestrator", {}).get("max_concurrent_tasks", 2),
              "roles": {}, "dependencies": {}, "cloud": {}, "ingress": {}}
    for role, cfg in config.get("roles", {}).items():
        name = cfg["provider"]
        provider = config["providers"].get(name, {})
        kind = provider.get("type")
        executable = provider.get("executable", "")
        report["roles"][role] = {"provider": name, "type": kind, "tier": cfg.get("tier"),
            "model": runner.resolve_model(role, provider) or "CLI/account default (set tier model to pin)",
            "effort": cfg.get("effort"), "executable": find_executable(executable) if executable else None}
        if kind in {"http_llm", "gemini_http"}:
            report["roles"][role]["credential_configured"] = bool(os.environ.get(provider.get("api_key_env", "")))
    for module in ["discord", "jupyter_client", "ipykernel", "playwright", "pypdf"]:
        report["dependencies"][module] = importlib.util.find_spec(module) is not None
    from core.portability import resolve_command
    try: runtime=resolve_command(config.get('cloud',{}).get('colab_command',['uvx']),root)[0]
    except FileNotFoundError:runtime=''
    report["cloud"] = {"kaggle_cli": find_executable("kaggle"), "uvx": find_executable("uvx"),
        "colab_runtime_executable": find_executable(runtime) if runtime else None,
        "colab_bridge_token_configured": bool(os.environ.get("COLAB_BRIDGE_TOKEN")),
        "kaggle_credentials_in_environment": bool(os.environ.get("KAGGLE_API_TOKEN") or (os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY"))),
        "note": "Login, quotas and GPU availability are not checked by this read-only command."}
    report["ingress"] = {"webhook_token_configured": bool(os.environ.get("WORKFLOW_WEBHOOK_TOKEN")),
        "discord_allowed_users_configured": bool(os.environ.get("DISCORD_ALLOWED_USER_IDS"))}
    from pathlib import Path
    report["agent_runtimes"] = {name: find_executable(name) for name in ("codex", "agy", "claude", "opencode")}
    report["provider_capabilities"] = {"codex_cli": "native hooks + final-answer file + JSON events",
        "agy_cli": "native hooks + text transcript", "claude_cli": "native hooks + text transcript",
        "opencode_cli": "JSON events + named provider sessions; controller observation (no native workflow hooks)",
        "http_llm": "controller-managed tool loop; no native CLI session"}
    skills = Path(root) / ".agents/skills"
    report["task_integrations"] = {"skill_sources": sorted(p.parent.name for p in skills.glob("*/SKILL.md")),
        "generated_artifact_skill": "workflow-artifacts", "native_protocols": ["codex", "claude", "agy"],
        "hook_receipts": ".workflow/hook-events.jsonl",
        "note": "Task bundles are generated and hash-checked. Runtime login/native hook execution require a live verification run."}
    return report
