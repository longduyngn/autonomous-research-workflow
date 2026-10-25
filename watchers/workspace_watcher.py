"""Internal Workspace Watcher Daemon.

Monitors workspace health without burning LLM tokens on every tick:
1. Detects unexpected file system changes outside of active Operator runs.
2. Detects tasks stuck in an active state longer than `stuck_task_threshold_seconds`.
3. When an anomaly is found, logs an event in SQLite and spawns the ephemeral
   `workspace_watcher` agent to diagnose and recommend an action.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.models import RoleName, TaskStatus
from core.providers import EphemeralAgentRunner


class WorkspaceWatcherDaemon:
    """Lightweight internal state monitor that spawns `workspace_watcher` on anomalies."""

    def __init__(
        self,
        workspace_root: Path,
        config: Dict[str, Any],
        db: WorkspaceDatabase,
        log_manager: ContextLogManager,
        runner: EphemeralAgentRunner,
        notify_callback: Optional[Callable[[str], None]] = None,
        provider_override: Optional[str] = None,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.config = config
        self.watcher_cfg = config.get("watchers", {}).get("workspace", {})
        self.db = db
        self.log_manager = log_manager
        self.runner = runner
        self.notify_callback = notify_callback
        self.provider_override = provider_override
        self._file_snapshot: Dict[str, float] = self._take_file_snapshot()

    def _take_file_snapshot(self) -> Dict[str, float]:
        ignored = set(
            self.watcher_cfg.get(
                "ignored_dirs",
                [".git", "__pycache__", "storage", ".venv", "node_modules"],
            )
        )
        snapshot: Dict[str, float] = {}
        for path in self.workspace_root.rglob("*"):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(self.workspace_root).parts
            if any(part in ignored for part in rel_parts):
                continue
            try:
                snapshot[str(path.relative_to(self.workspace_root))] = path.stat().st_mtime
            except OSError:
                continue
        return snapshot

    def check_once(self) -> Optional[str]:
        """Run a single lightweight inspection tick. Spawns LLM agent only on anomaly."""
        anomalies = []

        # 1. Check for stuck tasks in SQLite
        threshold = int(self.watcher_cfg.get("stuck_task_threshold_seconds", 900))
        now = datetime.now(timezone.utc)
        for task in self.db.get_Next_actionable_tasks(limit=20):
            if task.status == TaskStatus.PENDING.value:
                continue
            try:
                updated_dt = datetime.fromisoformat(task.updated_at)
                elapsed = (now - updated_dt).total_seconds()
                if elapsed > threshold:
                    anomalies.append(
                        f"Task #{task.id} ('{task.title}') has been in status {task.status} "
                        f"for { int(elapsed) }s (threshold: {threshold}s)."
                    )
            except ValueError:
                continue

        # 2. Check for workspace file changes
        new_snapshot = self._take_file_snapshot()
        added = set(new_snapshot.keys()) - set(self._file_snapshot.keys())
        removed = set(self._file_snapshot.keys()) - set(new_snapshot.keys())
        modified = {
            k
            for k in new_snapshot.keys() & self._file_snapshot.keys()
            if abs(new_snapshot[k] - self._file_snapshot[k]) > 0.001
        }
        self._file_snapshot = new_snapshot

        if added or removed or modified:
            self.db.log_event(
                event_type="WORKSPACE_FILES_CHANGED",
                actor="watcher:workspace",
                summary=f"Files changed (+{len(added)} ~{len(modified)} -{len(removed)})",
                payload={
                    "added": sorted(added)[:20],
                    "modified": sorted(modified)[:20],
                    "removed": sorted(removed)[:20],
                },
            )

        if not anomalies:
            return None

        # Anomaly detected -> Spawn ephemeral `workspace_watcher` agent
        anomaly_report = "\n".join(f"- {a}" for a in anomalies)
        self.db.log_event(
            event_type="WORKSPACE_ANOMALY_DETECTED",
            actor="watcher:workspace",
            severity="WARN",
            summary=f"Detected {len(anomalies)} workspace anomaly(s); spawning workspace_watcher agent.",
            payload={"anomalies": anomalies},
        )

        role = RoleName.WORKSPACE_WATCHER.value
        role_cfg = self.config.get("roles", {}).get(role, {})
        provider_name = self.provider_override or role_cfg.get("provider", "agy_headless_safe")
        cli_log = self.log_manager.create_agent_cli_log_path(0, role)

        run_id = self.db.record_agent_spawn(
            role=role,
            provider_name=provider_name,
            task_id=None,
            cli_log_path=str(cli_log),
        )
        res = self.runner.run_role(
            role=role,
            task_prompt=f"### Workspace Anomalies Detected\n{anomaly_report}\n",
            cli_log_path=cli_log,
            provider_override=self.provider_override,
        )
        self.db.record_agent_shutdown(
            run_id=run_id,
            role=role,
            task_id=None,
            success=res.success,
            duration_seconds=res.duration_seconds,
            exit_code=res.exit_code,
            output_preview=res.output,
            error_text=res.error,
        )

        if res.success and "ACTION: ALERT_USER" in res.output.upper() and self.notify_callback:
            self.notify_callback(f"🛡️ **Workspace Watcher Alert**\n{res.output}")

        return res.output
