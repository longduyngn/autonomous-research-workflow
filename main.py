"""Main Entrypoint for the Antigravity Ephemeral Multi-Agent Workspace.

Usage examples:
  # 1. Test the full 4-stage ephemeral loop (Researcher -> Operator -> Validator -> Communicator) in dry-run mode:
  python main.py run "Create a hello_world.py script" --dry-run

  # 2. Run a real task using headless Antigravity CLI (`agy --print`, no API key required):
  python main.py run "Create a Python utility that calculates Fibonacci numbers and validate it"

  # 3. Start the continuous autonomous workspace daemon (Orchestrator + 2 Watchers + Messaging Webhook Server):
  python main.py daemon

  # 4. Inspect SQLite event store, task queue, and role-to-provider mapping:
  python main.py status

  # 5. View a task's persistent context log file:
  python main.py logs 1

  # 6. Dynamically change which agent provider backs a specific role:
  python main.py set-role-provider researcher gemini_rest_api
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
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def build_system(
    config: Dict[str, Any],
    provider_override: Optional[str] = None,
):
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

    workspace_watcher = WorkspaceWatcherDaemon(
        workspace_root=WORKSPACE_ROOT,
        config=config,
        db=db,
        log_manager=log_manager,
        runner=runner,
        notify_callback=router.broadcast_notification,
        provider_override=provider_override,
    )

    def _spawn_from_world(prompt: str, actor: str) -> None:
        orchestrator.submit_task(
            user_prompt=prompt,
            title="World Watcher Triggered Task",
            source_channel="console",
            source_user=actor,
        )

    world_watcher = WorldWatcherDaemon(
        workspace_root=WORKSPACE_ROOT,
        config=config,
        db=db,
        log_manager=log_manager,
        runner=runner,
        notify_callback=router.broadcast_notification,
        task_spawn_callback=_spawn_from_world,
        provider_override=provider_override,
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
    )
    print(f"[+] Created Task #{task.id}: '{task.title}'")
    print(f"[+] Context Log File: {task.context_log_path}")
    print(f"[+] Running ephemeral agent pipeline (provider_override={override or 'config default'})...")

    final_task = orchestrator.run_task_to_completion(task.id)
    print(f"[+] Task #{final_task.id} finished with terminal status: {final_task.status}")


def cmd_daemon(args: argparse.Namespace) -> None:
    config = load_config()
    override = "dry_run_mock" if args.dry_run else args.provider
    db, _, orchestrator, ws_watcher, world_watcher, _ = build_system(
        config, provider_override=override
    )

    wh_cfg = config.get("gateways", {}).get("webhook_server", {})
    webhook_server: Optional[InboundWebhookServer] = None
    if wh_cfg.get("enabled", True):
        host = wh_cfg.get("host", "127.0.0.1")
        port = int(wh_cfg.get("port", 8080))

        def _enqueue(prompt: str, channel: str, user: str) -> int:
            t = orchestrator.submit_task(
                user_prompt=prompt,
                source_channel=channel,
                source_user=user,
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
        )
        webhook_server.start_background()
        print(f"[+] Inbound Messaging Webhook Server listening on http://{host}:{port}")

    poll_interval = float(config.get("orchestrator", {}).get("poll_interval_seconds", 2.0))
    ws_interval = float(
        config.get("watchers", {}).get("workspace", {}).get("check_interval_seconds", 30)
    )
    world_interval = float(
        config.get("watchers", {}).get("world", {}).get("check_interval_seconds", 300)
    )

    last_ws_check = 0.0
    last_world_check = 0.0
    print("[+] Autonomous Workspace Daemon started. Press Ctrl+C to stop.")

    try:
        while True:
            now = time.monotonic()

            # 1. Step pending/active tasks in the state machine
            active_tasks = db.get_Next_actionable_tasks(limit=2)
            for task in active_tasks:
                print(
                    f"[*] Advancing Task #{task.id} (status={task.status}, role={task.current_role})..."
                )
                orchestrator.process_next_step(task)

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

    def _enqueue(prompt: str, channel: str, user: str) -> Any:
        task = orchestrator.submit_task(
            user_prompt=prompt,
            source_channel=channel,
            source_user=user,
        )
        # Run in background or immediately
        import threading
        threading.Thread(target=orchestrator.run_task_to_completion, args=(task.id,), daemon=True).start()
        return task

    def _status() -> Dict[str, Any]:
        return {"tasks": db.get_all_tasks(limit=10)}

    bot_runner = DiscordBotRunner(token, _enqueue, _status)
    bot_runner.run()


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
        print(
            f"  [Task #{t.id:03d}] status={t.status:<13} retries={t.retry_count}/{t.max_retries} "
            f"channel={t.source_channel:<9} title='{t.title}' log={t.context_log_path}"
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
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"[+] Updated role '{args.role}' to use provider '{args.provider}' in {CONFIG_PATH.name}.")


def main() -> None:
    # Ensure UTF-8 stdout on Windows consoles
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    load_dotenv(WORKSPACE_ROOT / ".env")
    load_dotenv(WORKSPACE_ROOT / "config" / ".env")

    parser = argparse.ArgumentParser(
        description="Antigravity Ephemeral Multi-Agent Workspace Orchestrator"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Submit a single task and run it to completion")
    p_run.add_argument("prompt", help="Task prompt to execute")
    p_run.add_argument("--channel", default="console", help="Source channel (console/discord/zalo/messenger)")
    p_run.add_argument("--user", default="local_user", help="Source user identifier")
    p_run.add_argument("--dry-run", action="store_true", help="Use fast mock provider to test workflow loop")
    p_run.add_argument("--provider", default=None, help="Override provider for all roles in this run")
    p_run.set_defaults(func=cmd_run)

    p_daemon = sub.add_parser("daemon", help="Run the continuous orchestrator, watchers, and webhook server")
    p_daemon.add_argument("--dry-run", action="store_true", help="Use mock provider for all spawned agents")
    p_daemon.add_argument("--provider", default=None, help="Override provider for all roles")
    p_daemon.set_defaults(func=cmd_daemon)

    p_bot = sub.add_parser("discord-bot", help="Run the interactive Discord Bot listener")
    p_bot.add_argument("--provider", default=None, help="Override provider for all roles")
    p_bot.set_defaults(func=cmd_discord_bot)

    p_status = sub.add_parser("status", help="Inspect role mappings, tasks, and SQLite events")
    p_status.add_argument("--limit", type=int, default=15, help="Number of recent events to display")
    p_status.set_defaults(func=cmd_status)

    p_logs = sub.add_parser("logs", help="Print the persistent context log file for a task")
    p_logs.add_argument("task_id", type=int, help="Task ID")
    p_logs.set_defaults(func=cmd_logs)

    p_set = sub.add_parser("set-role-provider", help="Change which provider executes a given role")
    p_set.add_argument("role", help="Role name (researcher/operator/validator/communicator/workspace_watcher/world_watcher)")
    p_set.add_argument("provider", help="Provider name from config/workflow_config.json")
    p_set.set_defaults(func=cmd_set_role_provider)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
