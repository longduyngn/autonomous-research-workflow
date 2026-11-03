"""Bounded live-agent proof using an independent DB and console-only delivery."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from main import load_config, load_dotenv
from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.orchestrator import HybridOrchestrator
from core.providers import EphemeralAgentRunner
from gateways.base import MessagingRouter
from gateways.discord_gateway import ConsoleGateway


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="codex_cli", choices=["codex_cli", "agy_headless_auto", "claude_cli", "opencode_cli", "configured"])
    parser.add_argument("--no-env", action="store_true", help="Do not read .env files")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument('--cloud-t4', action='store_true', help='Explicit live Colab T4 smoke check (1 second); default remains local-only')
    parser.add_argument("--probe-mcp-workspace", help="Only inspect a local notebook through native MCP in an existing prepared verification workspace")
    args = parser.parse_args()
    if not args.no_env:
        load_dotenv(ROOT / ".env")
        load_dotenv(ROOT / "config/.env")
    config = load_config()
    if args.probe_mcp_workspace:
        config["roles"]["operator"]["timeout_seconds"] = args.timeout
        workspace = Path(args.probe_mcp_workspace).resolve()
        allowed = ROOT / "storage"
        if not (workspace.is_relative_to(allowed) and workspace.relative_to(allowed).parts[0].startswith("verification-")):
            parser.error("MCP probe must use a dedicated verification workspace")
        notebook = {"nbformat": 4, "nbformat_minor": 5, "metadata": {"workflow_probe": "native-mcp"},
                    "cells": [{"cell_type": "markdown", "metadata": {}, "source": ["MCP connectivity proof"]}]}
        (workspace / ".workflow/mcp-probe.ipynb").write_text(json.dumps(notebook), encoding="utf-8")
        runner = EphemeralAgentRunner(ROOT, config).for_workspace(workspace)
        result = runner.run_role("operator", "Local connectivity test only. Use the configured native notebook MCP tool "
            "notebook_inspect on .workflow/mcp-probe.ipynb. Do not use shell or cloud execution as a substitute. "
            "Report the actual metadata.workflow_probe value and cell source, prefixed MCP_PROBE_PASS when read successfully.",
            cli_log_path=workspace / ".workflow/native-mcp-probe.log", provider_override=args.provider)
        observed = result.structured_data.get("native_tools", []) + result.structured_data.get("native_mcp_tools", [])
        passed = result.success and any("notebook_inspect" in name for name in observed)
        report = {"passed": passed, "output": result.output, "error": result.error, **result.structured_data}
        (workspace / ".workflow/native-mcp-summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        return 0 if passed else 1
    directory = ROOT / ("storage/verification-cloud" if args.cloud_t4 else "storage/verification-local") / args.provider
    config["database_path"] = str(directory / "events.db")
    config["logs_dir"] = str(directory / "logs")
    config["isolation"] = {"enabled": True, "directory": str(directory / "workspaces")}
    config["orchestrator"].update(max_attempts=1, task_timeout_seconds=args.timeout * 4)
    for role in ("researcher", "operator", "validator", "communicator"):
        config["roles"][role]["timeout_seconds"] = args.timeout
    db = WorkspaceDatabase(config["database_path"])
    logs = ContextLogManager(Path(config["logs_dir"]))
    router = MessagingRouter(db)
    router.register(ConsoleGateway())
    orch = HybridOrchestrator(ROOT, config, db, logs, EphemeralAgentRunner(ROOT, config),
        outbound_callback=router.dispatch_task_response, provider_override=None if args.provider == "configured" else args.provider)
    prompt = (
        "LOCAL end-to-end integration proof, explicitly authorized to create files in this task workspace. "
        "Do not use cloud services, start/stop browsers or GPU jobs, send messages, or spawn helper agents. "
        "Use the workflow-artifacts skill: read its SKILL.md; Operator and Validator should run its verify.py "
        "on the actual results.json using the workflow Python in AGENTS.md. "
        "Create sum_numbers.py which computes sum(range(100)) and writes results.json with exactly "
        "{\"sum\":4950,\"count\":100}. Create check_results.py that runs the script using sys.executable, "
        "loads results.json, asserts those exact values and prints PASS. Run it and preserve results.json. "
        "Researcher: use a direct plan with an executable check [\"{python}\",\"check_results.py\"]. "
        "Validator: independently execute check_results.py, inspect the JSON, run the artifact skill, "
        "and give the strict verdict with command evidence. Communicator: report outcome and artifact path concisely."
    )
    if args.cloud_t4:
        helper = str(ROOT / 'scripts/colab_timed_probe.py')
        check_argv = ['{python}', helper, '--workspace', '.', '--seconds', '1', '--check']
        prompt = ('Explicitly authorized final Antigravity cloud integration proof. Use only the assigned workspace, '
            'one short Colab T4 allocation and existing signed-in browser. No helper agents or messaging commands. '
            'Read the colab-coccoc-playwright and workflow-artifacts skills. Researcher: use a direct plan with backend=colab whose '
            'executable check argv is ' + json.dumps(check_argv) + '. Operator: run the supplied controller helper '
            + helper + ' --workspace . --seconds 1 with the workflow Python. It prints hello/current time, measures '
            'nvidia-smi T4, saves real notebook output and verifies an empty Colab session manager after disconnect. '
            'Do not edit measured evidence or weaken checks. Validator: independently run the controller helper '
            'with --workspace . --seconds 1 --check; inspect actual output, run ID, GPU and active_session_count=0. '
            'Operator and Validator register artifacts/colab_timed_result.json and notebooks/colab_t4_timeline.ipynb '
            'with workflow-artifacts verify.py. If only shutdown observation needs refresh, --verify-shutdown '
            'collects a fresh observation without allocating again. New human approval must be deferred, not requested. '
            'Communicator: report the actual outcome concisely. Do not rerun the 300-second experiment.')
    task = orch.submit_task(prompt, title="Live hooks and skills cloud verification" if args.cloud_t4 else "Live hooks and skills local verification")
    print(json.dumps({"task_id": task.id, "provider": args.provider, "database": config["database_path"]}), flush=True)
    try:
        final = orch.run_task_to_completion(task.id)
    except Exception as exc:
        final = db.get_task(task.id)
        print(f"Worker failed: {exc}", file=sys.stderr)
    workspace = Path(final.workspace_path) if final.workspace_path else None
    result = None
    output_name = 'artifacts/colab_timed_result.json' if args.cloud_t4 else 'results.json'
    if workspace and (workspace / output_name).exists():
        result = json.loads((workspace / output_name).read_text(encoding="utf-8-sig"))
    events = []
    if workspace and (workspace / ".workflow/hook-events.jsonl").exists():
        events = [json.loads(line) for line in (workspace / ".workflow/hook-events.jsonl").read_text(encoding="utf-8").splitlines()]
    receipts = []
    if workspace and (workspace / ".workflow/artifact-receipts.jsonl").exists():
        receipts = [json.loads(line) for line in (workspace / ".workflow/artifact-receipts.jsonl").read_text(encoding="utf-8").splitlines()]
    started = {e["role"] for e in events if e["event"] == "SessionStart"}
    roles = {"researcher", "operator", "validator", "communicator"}
    actual_valid = result == {"sum":4950, "count":100}
    if args.cloud_t4:
        actual_valid = bool(result and result.get('status') == 'completed'
            and 'T4' in result.get('measurement', {}).get('gpu_name', '')
            and result.get('measurement', {}).get('elapsed_seconds', 0) >= 1
            and result.get('shutdown_state', {}).get('runtime_stopped') is True
            and result.get('shutdown_state', {}).get('active_session_count') == 0)
    timeline = db.task_timeline(final.id)
    required_native_roles = {r['role'] for r in timeline['roles']
        if config['providers'].get(r['provider_name'], {}).get('type') in {'codex_cli','claude_cli','agy_cli'}}
    passed = (final.status == "COMPLETED" and actual_valid
              and required_native_roles <= started and len(receipts) >= 2)
    summary = {"passed": passed, "task_id": task.id, "provider": args.provider, "status": final.status,
        "workspace": final.workspace_path, "actual_result": ({'measurement': result.get('measurement'), 'shutdown_state': result.get('shutdown_state')} if args.cloud_t4 and result else result), "native_hook_roles": sorted(started),
        "required_native_hook_roles": sorted(required_native_roles), "hook_event_count": len(events),
        "artifact_receipts": receipts, "feedback": final.validation_feedback}
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"task_{task.id:05d}_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
