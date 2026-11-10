"""Durable autonomous research workflow.

Use `doctor` for setup checks, `run` for a request, `enqueue` to queue work,
`daemon` or `discord-bot` for persistent workers, and `colab-bridge` for Google's
personal Colab browser session. See README.md and docs/ARCHITECTURE.md.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.orchestrator import HybridOrchestrator
from core.providers import EphemeralAgentRunner
from core.scheduler import TaskScheduler
from gateways.base import MessagingRouter
from gateways.discord_gateway import ConsoleGateway, DiscordGateway
from gateways.webhook_server import InboundWebhookServer
from gateways.zalo_messenger_gateway import MessengerGateway, ZaloGateway
from watchers.workspace_watcher import WorkspaceWatcherDaemon
from watchers.world_watcher import WorldWatcherDaemon

WORKSPACE_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = WORKSPACE_ROOT / "config" / "workflow_config.json"


def load_dotenv(dotenv_path: Path) -> None:
    """Load simple KEY=VALUE pairs from `.env` if present."""
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config() -> Dict[str, Any]:
    from core.configuration import validate_config
    source=CONFIG_PATH if CONFIG_PATH.is_file() else CONFIG_PATH.with_name('workflow_config.example.json')
    config = json.loads(source.read_text(encoding="utf-8"))
    return validate_config(config)


def save_config(config):
    from core.configuration import validate_config
    config = validate_config(config)  # persists canonical role names
    temporary = CONFIG_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    temporary.replace(CONFIG_PATH)


def build_system(
    config: Dict[str, Any],
    provider_override: Optional[str] = None,
):
    if provider_override == "dry_run_mock":
        config = json.loads(json.dumps(config))
        config["database_path"] = "storage/dry-run/events.db"
        config["logs_dir"] = "storage/dry-run/logs"
        config.setdefault("isolation", {})["directory"] = "storage/dry-run/workspaces"
        config["gateways"] = {}
    db_path = WORKSPACE_ROOT / config.get("database_path", "storage/events.db")
    logs_dir = WORKSPACE_ROOT / config.get("logs_dir", "storage/logs")

    db = WorkspaceDatabase(db_path)
    log_manager = ContextLogManager(logs_dir)
    runner = EphemeralAgentRunner(WORKSPACE_ROOT, config)

    # Messaging Router & Gateways
    router = MessagingRouter(db)
    router.register(ConsoleGateway())

    gw_cfg = config.get("gateways", {})
    if gw_cfg.get("discord", {}).get("enabled", False):
        router.register(DiscordGateway(gw_cfg["discord"]))
    if gw_cfg.get("zalo", {}).get("enabled", False):
        router.register(ZaloGateway(gw_cfg["zalo"]))
    if gw_cfg.get("messenger", {}).get("enabled", False):
        router.register(MessengerGateway(gw_cfg["messenger"]))

    orchestrator = HybridOrchestrator(
        workspace_root=WORKSPACE_ROOT,
        config=config,
        db=db,
        log_manager=log_manager,
        runner=runner,
        outbound_callback=router.dispatch_task_response,
        provider_override=provider_override,
    )
    orchestrator.delivery_tick = router.flush_outbox

    workspace_watcher = WorkspaceWatcherDaemon(
        workspace_root=WORKSPACE_ROOT,
        config=config,
        db=db,
        log_manager=log_manager,
        runner=runner,
        notify_callback=router.broadcast_notification,
        provider_override=provider_override,
        quota_router=orchestrator.quota,
    )

    def _spawn_from_world(prompt: str, actor: str) -> None:
        orchestrator.submit_task(
            user_prompt=prompt,
            title="World Watcher Triggered Task",
            source_channel="console",
            source_user=actor,
            event_key=actor if actor.startswith("world:") else "",
        )
    workspace_watcher.task_spawn_callback = _spawn_from_world

    world_watcher = WorldWatcherDaemon(
        workspace_root=WORKSPACE_ROOT,
        config=config,
        db=db,
        log_manager=log_manager,
        runner=runner,
        notify_callback=router.broadcast_notification,
        task_spawn_callback=_spawn_from_world,
        provider_override=provider_override,
        quota_router=orchestrator.quota,
    )

    return db, log_manager, orchestrator, workspace_watcher, world_watcher, router


def cmd_run(args: argparse.Namespace) -> None:
    config = load_config()
    override = "dry_run_mock" if args.dry_run else args.provider
    db, _, orchestrator, _, _, _ = build_system(config, provider_override=override)

    task = orchestrator.submit_task(
        user_prompt=args.prompt,
        source_channel=args.channel,
        source_user=args.user,
        input_files=args.input, source_path=args.source or "",
        plan_approval="required" if getattr(args, "approve_plan", False) else None,
    )
    print(f"[+] Created Task #{task.id}: '{task.title}'")
    print(f"[+] Context Log File: {task.context_log_path}")
    print(f"[+] Running ephemeral agent pipeline (provider_override={override or 'config default'})...")

    scheduler = TaskScheduler(orchestrator)
    scheduler.start()
    try:
        last_status=None
        while db.get_task(task.id).status not in {"COMPLETED", "FAILED", "ESCALATED"}:
            current=db.get_task(task.id)
            if current.status!=last_status:
                print(f'[+] Task #{task.id}: {current.status}',flush=True)
                if current.status.startswith('WAITING_'):
                    wait=next((w for w in orchestrator.control.waits() if w['task_id']==task.id),None)
                    if wait: print(f"    {wait['reason']} | dependency: {wait['dependency']} | inspect: python main.py control list",flush=True)
                last_status=current.status
            time.sleep(0.25)
        final_task = db.get_task(task.id)
    finally:
        scheduler.stop(cancel_running=sys.exc_info()[0] is KeyboardInterrupt)

    print(f"[+] Task #{final_task.id} finished with terminal status: {final_task.status}")
    if final_task.status != "COMPLETED":
        raise SystemExit(1)


def cmd_daemon(args: argparse.Namespace) -> None:
    config = load_config()
    override = "dry_run_mock" if args.dry_run else args.provider
    db, _, orchestrator, ws_watcher, world_watcher, _ = build_system(
        config, provider_override=override
    )

    wh_cfg = config.get("gateways", {}).get("webhook_server", {})
    webhook_server: Optional[InboundWebhookServer] = None
    webhook_token = os.environ.get(wh_cfg.get("token_env", "WORKFLOW_WEBHOOK_TOKEN"), "")
    if wh_cfg.get("enabled", True) and webhook_token:
        host = wh_cfg.get("host", "127.0.0.1")
        port = int(wh_cfg.get("port", 8080))

        def _enqueue(prompt: str, channel: str, user: str, event_key: str = "") -> int:
            t = orchestrator.submit_task(
                user_prompt=prompt,
                source_channel=channel,
                source_user=user,
                event_key=event_key,
            )
            return t.id

        def _inject_world(source: str, title: str, url: str) -> None:
            world_watcher.check_once(
                force_items=[{"source": source, "title": title, "url": url}]
            )

        webhook_server = InboundWebhookServer(
            host=host,
            port=port,
            db=db,
            enqueue_task_fn=_enqueue,
            inject_world_event_fn=_inject_world,
            auth_token=webhook_token,
        )
        webhook_server.start_background()
        print(f"[+] Inbound Messaging Webhook Server listening on http://{host}:{port}")
    elif wh_cfg.get("enabled", True):
        print("[i] Webhook ingress disabled until WORKFLOW_WEBHOOK_TOKEN is configured.")

    poll_interval = float(config.get("orchestrator", {}).get("poll_interval_seconds", 2.0))
    ws_interval = float(
        config.get("watchers", {}).get("workspace", {}).get("check_interval_seconds", 30)
    )
    world_interval = float(
        config.get("watchers", {}).get("world", {}).get("check_interval_seconds", 300)
    )

    last_ws_check = 0.0
    last_world_check = 0.0
    scheduler = TaskScheduler(orchestrator)
    scheduler.start()
    print("[+] Autonomous Workspace Daemon started. Press Ctrl+C to stop.")

    try:
        while True:
            now = time.monotonic()

            # 2. Internal Workspace Watcher tick
            if config.get("watchers", {}).get("workspace", {}).get("enabled", True):
                if now - last_ws_check >= ws_interval:
                    ws_watcher.check_once()
                    last_ws_check = now

            # 3. External World Watcher tick
            if config.get("watchers", {}).get("world", {}).get("enabled", True):
                if now - last_world_check >= world_interval:
                    world_watcher.check_once()
                    last_world_check = now

            time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("\n[!] Shutting down workspace daemon...")
        scheduler.stop(cancel_running=True)
        if webhook_server:
            webhook_server.stop()


def cmd_discord_bot(args: argparse.Namespace) -> None:
    config = load_config()
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        print("[!] Error: DISCORD_BOT_TOKEN is not set in .env or environment.")
        print("    Please set DISCORD_BOT_TOKEN in .env to use the interactive bot.")
        sys.exit(1)

    from gateways.discord_bot import DiscordBotRunner
    db, _, orchestrator, _, _, _ = build_system(config, provider_override=args.provider)

    scheduler = TaskScheduler(orchestrator)
    scheduler.start()

    def _enqueue(prompt: str, channel: str, user: str, event_key: str = "", input_files=None) -> Any:
        task = orchestrator.submit_task(
            user_prompt=prompt,
            source_channel=channel,
            source_user=user,
            event_key=event_key,
            input_files=input_files,
        )
        return task

    def _status() -> Dict[str, Any]:
        return {"tasks": db.get_all_tasks(limit=10)}

    bot_runner = DiscordBotRunner(token, _enqueue, _status, conversation=orchestrator.conversation)
    try:
        bot_runner.run()
    finally:
        scheduler.stop(cancel_running=True)


def cmd_status(args: argparse.Namespace) -> None:
    config = load_config()
    db_path = WORKSPACE_ROOT / config.get("database_path", "storage/events.db")
    db = WorkspaceDatabase(db_path)

    print("=== 1. Role -> Agent Provider Mapping (config/workflow_config.json) ===")
    for role, rcfg in config.get("roles", {}).items():
        prov = rcfg.get("provider")
        ptype = config.get("providers", {}).get(prov, {}).get("type", "unknown")
        print(f"  - {role:<18} -> provider: {prov:<22} (type: {ptype})")

    print("\n=== 2. Recent Tasks in SQLite ===")
    tasks = db.get_all_tasks(limit=10)
    if not tasks:
        print("  (No tasks recorded yet)")
    for t in tasks:
        from core.monitoring import status_line
        print(
            f"  [Task #{t.id:03d}] status={t.status:<13} retries={t.retry_count}/{t.max_retries} "
            f"channel={t.source_channel:<9} title='{t.title}' log={t.context_log_path}"+status_line(db,t.id)
        )

    print(f"\n=== 3. Recent Events in SQLite ({db_path.name}) ===")
    events = db.get_recent_events(limit=args.limit)
    if not events:
        print("  (No events recorded yet)")
    for ev in events:
        tid = f"Task#{ev['task_id']}" if ev["task_id"] is not None else "System"
        print(
            f"  [{ev['id']:04d}] {ev['timestamp']} | {ev['severity']:<5} | {ev['event_type']:<24} "
            f"| {ev['actor']:<20} | ({tid}) {ev['summary']}"
        )


def cmd_logs(args: argparse.Namespace) -> None:
    config = load_config()
    db = WorkspaceDatabase(WORKSPACE_ROOT / config.get("database_path", "storage/events.db"))
    task = db.get_task(args.task_id)
    if not task:
        print(f"Task #{args.task_id} not found.")
        sys.exit(1)
    log_path = Path(task.context_log_path)
    if not log_path.exists():
        print(f"Context log file not found at {log_path}")
        sys.exit(1)
    print(log_path.read_text(encoding="utf-8"))


def cmd_set_role_provider(args: argparse.Namespace) -> None:
    from core.role_names import canonical_role
    args.role = canonical_role(args.role)
    config = load_config()
    roles = config.get("roles", {})
    providers = config.get("providers", {})
    if args.role not in roles:
        print(f"Unknown role '{args.role}'. Valid roles: {', '.join(roles.keys())}")
        sys.exit(1)
    if args.provider not in providers:
        print(
            f"Unknown provider '{args.provider}'. Valid providers: {', '.join(providers.keys())}"
        )
        sys.exit(1)

    roles[args.role]["provider"] = args.provider
    roles[args.role]["model"] = ""
    save_config(config)
    print(f"[+] Updated role '{args.role}' to use provider '{args.provider}' in {CONFIG_PATH.name}.")


def main() -> None:
    # Ensure UTF-8 stdout on Windows consoles
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Antigravity Ephemeral Multi-Agent Workspace Orchestrator"
    )
    parser.add_argument("--no-env", action="store_true", help="Do not load .env files")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Submit a single task and run it to completion")
    p_run.add_argument("prompt", help="Task prompt to execute")
    p_run.add_argument("--channel", default="console", help="Source channel (console/discord/zalo/messenger)")
    p_run.add_argument("--user", default="local_user", help="Source user identifier")
    p_run.add_argument("--dry-run", action="store_true", help="Use fast mock provider to test workflow loop")
    p_run.add_argument("--provider", default=None, help="Override provider for all roles in this run")
    p_run.add_argument("--input", action="append", default=[], help="Explicit input file; repeat for more files (50 MB total)")
    p_run.add_argument("--source", help="Project directory to snapshot instead of the controller")
    p_run.add_argument("--approve-plan", action="store_true", help="Pause after planning until the owner approves the plan")
    p_run.set_defaults(func=cmd_run)

    p_daemon = sub.add_parser("daemon", help="Run the continuous orchestrator, watchers, and webhook server")
    p_daemon.add_argument("--dry-run", action="store_true", help="Use mock provider for all spawned agents")
    p_daemon.add_argument("--provider", default=None, help="Override provider for all roles")
    p_daemon.set_defaults(func=cmd_daemon)

    p_bot = sub.add_parser("discord-bot", help="Run the interactive Discord Bot listener")
    p_bot.add_argument("--provider", default=None, help="Override provider for all roles")
    p_bot.set_defaults(func=cmd_discord_bot)

    p_check_bot = sub.add_parser("check-bot", help="Check whether the Discord bot is active and listening")
    p_check_bot.set_defaults(func=lambda args: __import__("scripts.check_bot", fromlist=["check_bot_status"]).check_bot_status())

    p_status = sub.add_parser("status", help="Inspect role mappings, tasks, and SQLite events")
    p_status.add_argument("--limit", type=int, default=15, help="Number of recent events to display")
    p_status.set_defaults(func=cmd_status)

    p_logs = sub.add_parser("logs", help="Print the persistent context log file for a task")
    p_logs.add_argument("task_id", type=int, help="Task ID")
    p_logs.set_defaults(func=cmd_logs)

    def control(args):
        from core.control import ControlPlane
        cfg=load_config()
        db=WorkspaceDatabase(WORKSPACE_ROOT/cfg.get('database_path','storage/events.db'))
        cp=ControlPlane(db)
        if args.action=='list':
            with db._connect() as c:
                approvals=[dict(r) for r in c.execute('SELECT * FROM approvals ORDER BY created DESC LIMIT 30')]
            print(json.dumps({'waits':cp.waits(),'approvals':approvals},indent=2))
        elif args.action=='show':
            print(json.dumps(cp.approval(args.key),indent=2))
        elif args.action in {'approve','deny'}:
            if not args.actor:
                raise SystemExit('--actor must match the recorded task owner; local CLI is an administrator interface')
            accepted=cp.decide(args.key,args.actor,'APPROVED' if args.action=='approve' else 'DENIED')
            print('Decision recorded' if accepted else 'Rejected: identity, state or expiry mismatch')
            if not accepted: raise SystemExit(1)
        elif args.action=='cooldown':
            if args.key not in cfg.get('providers',{}): raise SystemExit('Unknown provider')
            cp.cool_down(args.key,args.seconds,'Explicit host cooldown')
            print('Provider cooldown recorded; new role calls will wait')
        elif args.action=='reconcile-colab':
            from core.resource_gate import ColabGate
            from core.colab_bridge import browser_action
            gate=ColabGate(WORKSPACE_ROOT/cfg.get('cloud',{}).get('resource_directory','storage/resource-control'),lambda:browser_action('runtime_status',{}))
            print(json.dumps(gate.reconcile(),indent=2))
    p_control=sub.add_parser('control',help='Inspect waits/exact approvals; host-only administrative decisions')
    p_control.add_argument('action',choices=['list','show','approve','deny','cooldown','reconcile-colab'])
    p_control.add_argument('key',nargs='?',default='')
    p_control.add_argument('--actor',default='')
    p_control.add_argument('--seconds',type=float,default=60)
    p_control.set_defaults(func=control)

    def dashboard(args):
        from core.dashboard import serve_dashboard
        cfg = load_config()
        db = WorkspaceDatabase(WORKSPACE_ROOT / cfg.get("database_path", "storage/events.db"))
        serve_dashboard(db, WORKSPACE_ROOT, port=args.port,host=args.host)
    p_dashboard = sub.add_parser("dashboard", help="Read-only local task dashboard (no workers)")
    p_dashboard.add_argument("--port", type=int, default=8787)
    p_dashboard.add_argument('--host',choices=['127.0.0.1','0.0.0.0'],default='127.0.0.1',help='Container bind; publish the port on localhost only')
    p_dashboard.set_defaults(func=dashboard)

    def inspect_task(args):
        from core.observability import task_detail
        cfg = load_config()
        db = WorkspaceDatabase(WORKSPACE_ROOT / cfg.get("database_path", "storage/events.db"))
        print(json.dumps(task_detail(db, args.task_id), indent=2, ensure_ascii=False))
    p_inspect = sub.add_parser("task", help="Inspect a task's stages, named runs and checks")
    p_inspect.add_argument("task_id", type=int)
    p_inspect.set_defaults(func=inspect_task)

    def verify(args):
        import subprocess
        command = [sys.executable, str(WORKSPACE_ROOT / "scripts/verify_workflow.py"),
                   "--provider", args.provider, "--timeout", str(args.timeout)]
        if args.no_env:
            command.append("--no-env")
        raise SystemExit(subprocess.call(command, cwd=WORKSPACE_ROOT))
    p_verify = sub.add_parser("verify", help="Real local smoke task in a separate DB; no Discord or GPU use")
    p_verify.add_argument("--provider", default="configured", choices=["configured", "codex_cli", "agy_headless_auto", "claude_cli", "opencode_cli"])
    p_verify.add_argument("--timeout", type=int, default=300)
    p_verify.set_defaults(func=verify)

    def board(args):
        from core.reports import write_board
        config = load_config()
        db = WorkspaceDatabase(WORKSPACE_ROOT / config["database_path"])
        destination = WORKSPACE_ROOT / "storage/TASKS.md"
        write_board(db, destination)
        print(destination)
    sub.add_parser("board", help="Write a readable task index to storage/TASKS.md").set_defaults(func=board)

    def archive(args):
        from core.maintenance import archive_task
        from core.reports import write_board
        config = load_config()
        db = WorkspaceDatabase(WORKSPACE_ROOT / config["database_path"])
        result = archive_task(db, WORKSPACE_ROOT, args.task_id, args.batch)
        write_board(db, WORKSPACE_ROOT / "storage/TASKS.md")
        print(result['archive_workspace'])
    p_archive = sub.add_parser('archive-task', help='Move a failed snapshot into storage/archive with hashes; keeps logs and database history')
    p_archive.add_argument('task_id', type=int)
    p_archive.add_argument('--batch', default=time.strftime('%Y%m%d'))
    p_archive.set_defaults(func=archive)

    p_set = sub.add_parser("set-role-provider", help="Legacy provider command; uses the same configuration validation as configure-role")
    p_set.add_argument("role", help="Role name (planner/operator/validator/communicator/workspace_watcher/world_watcher; legacy researcher accepted)")
    p_set.add_argument("provider", help="Provider name from config/workflow_config.json")
    p_set.set_defaults(func=cmd_set_role_provider)

    def configure_role(args):
        config = load_config()
        from core.role_names import canonical_role
        args.role = canonical_role(args.role)
        if args.role not in config["roles"] or args.provider not in config["providers"]:
            raise ValueError("Unknown role/provider; inspect config/workflow_config.json")
        kind = config["providers"][args.provider]["type"]
        if args.role in {"planner", "researcher", "operator", "validator"} and kind == "gemini_http":
            raise ValueError("This text-only provider cannot run an action role")
        config["roles"][args.role].update(provider=args.provider, model=args.model or "")
        if args.effort:
            config["roles"][args.role]["effort"] = args.effort
        save_config(config)
        print(f"Configured {args.role}; restart running daemons to apply.")

    p_role = sub.add_parser("configure-role", help="Set a role provider/model/effort")
    p_role.add_argument("role")
    p_role.add_argument("--provider", required=True)
    p_role.add_argument("--model", default="")
    p_role.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    p_role.set_defaults(func=configure_role)

    def use_cli(args):
        config = load_config()
        name = {"antigravity": "agy_headless_auto", "codex": "codex_cli", "claude": "claude_cli", "opencode": "opencode_cli"}[args.cli]
        for role in config["roles"].values():
            role.update(provider=name, model="")
        save_config(config)
        print(f"All roles use {name}; tier models/effort remain independently configurable. Restart daemons.")
    p_cli = sub.add_parser("use-cli", help="Choose Antigravity, Codex or Claude Code for all roles")
    p_cli.add_argument("cli", choices=["antigravity", "codex", "claude", "opencode"])
    p_cli.set_defaults(func=use_cli)

    def doctor(args):
        from core.diagnostics import inspect_environment
        print(json.dumps(inspect_environment(WORKSPACE_ROOT, load_config()), indent=2))
    sub.add_parser("doctor", help="Read-only setup diagnostics; never calls models or allocates GPUs").set_defaults(func=doctor)

    def colab_bridge(args):
        from core.colab_bridge import serve_colab_bridge
        serve_colab_bridge(load_config().get("cloud", {}), port=args.port)
    p_colab = sub.add_parser("colab-bridge", help="Keep official personal Colab browser session connected")
    p_colab.add_argument("--port", type=int, default=8765)
    p_colab.set_defaults(func=colab_bridge)

    def quota(args):
        from core.quota_cli import run
        cfg = load_config()
        run(args, cfg, WORKSPACE_ROOT, WorkspaceDatabase(WORKSPACE_ROOT / cfg.get("database_path", "storage/events.db")))
    p_quota = sub.add_parser("quota", help="Provider usage snapshots and opt-in quota routing (refresh forces one tiny Claude probe turn)")
    p_quota.add_argument("action", choices=["status", "refresh", "route"])
    p_quota.add_argument("role", nargs="?", default="", help="Role for `route` (what-if routing decision)")
    p_quota.add_argument("--task", type=int, default=None, help="Task ID for `route` (honours its provider override)")
    p_quota.add_argument("--requires", action="append", default=[], help="Account the work requires; may spend its reserve")
    p_quota.add_argument("--json", action="store_true")
    p_quota.set_defaults(func=quota)

    def enqueue(args):
        _, _, orchestrator, _, _, _ = build_system(load_config())
        task = orchestrator.submit_task(args.prompt, provider_override=args.provider or "",
                                       input_files=args.input, source_path=args.source or "",
                                       plan_approval="required" if args.approve_plan else None)
        print(f"Queued task #{task.id}. A daemon or Discord worker will pick it up.")
    p_enqueue = sub.add_parser("enqueue", help="Queue a request without starting workers")
    p_enqueue.add_argument("prompt")
    p_enqueue.add_argument("--provider", default=None)
    p_enqueue.add_argument("--input", action="append", default=[])
    p_enqueue.add_argument("--source")
    p_enqueue.add_argument("--approve-plan", action="store_true", help="Pause after planning until the owner approves the plan")
    p_enqueue.set_defaults(func=enqueue)

    args = parser.parse_args()
    if not args.no_env:
        load_dotenv(WORKSPACE_ROOT / ".env")
        load_dotenv(WORKSPACE_ROOT / "config" / ".env")
    args.func(args)


if __name__ == "__main__":
    main()
