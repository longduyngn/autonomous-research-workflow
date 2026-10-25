"""SQLite Event Store & Task State Database (WAL Mode).

Stores every workspace event, task state transition, ephemeral agent spawn/shutdown,
workspace health snapshot, and external world digest in a single SQLite database.
"""

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from core.models import TaskRecord, TaskStatus, utc_now_iso


class WorkspaceDatabase:
    """Thread-safe SQLite database with Write-Ahead Logging (WAL) for all events and tasks."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    task_id INTEGER,
                    severity TEXT NOT NULL DEFAULT 'INFO',
                    summary TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_events_task_id ON events(task_id);
                CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);

                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    user_prompt TEXT NOT NULL,
                    source_channel TEXT NOT NULL DEFAULT 'console',
                    source_user TEXT NOT NULL DEFAULT 'local_user',
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    current_role TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    max_retries INTEGER NOT NULL DEFAULT 3,
                    context_log_path TEXT NOT NULL DEFAULT '',
                    research_notes TEXT NOT NULL DEFAULT '',
                    operator_output TEXT NOT NULL DEFAULT '',
                    validation_feedback TEXT NOT NULL DEFAULT '',
                    final_response TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

                CREATE TABLE IF NOT EXISTS agent_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER,
                    role TEXT NOT NULL,
                    provider_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    spawned_at TEXT NOT NULL,
                    shutdown_at TEXT,
                    duration_seconds REAL DEFAULT 0.0,
                    exit_code INTEGER DEFAULT 0,
                    cli_log_path TEXT,
                    output_preview TEXT,
                    error_text TEXT
                );

                CREATE TABLE IF NOT EXISTS world_seen_items (
                    item_hash TEXT PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    discovered_at TEXT NOT NULL,
                    processed INTEGER NOT NULL DEFAULT 0
                );
                """
            )

    def log_event(
        self,
        event_type: str,
        actor: str,
        summary: str,
        task_id: Optional[int] = None,
        severity: str = "INFO",
        payload: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Record an immutable event in the `events` table."""
        now = utc_now_iso()
        payload_str = json.dumps(payload or {}, ensure_ascii=False)
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO events (timestamp, event_type, actor, task_id, severity, summary, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (now, event_type, actor, task_id, severity, summary, payload_str),
            )
            return int(cursor.lastrowid)

    def create_task(
        self,
        user_prompt: str,
        title: Optional[str] = None,
        source_channel: str = "console",
        source_user: str = "local_user",
        max_retries: int = 3,
        logs_dir: str | Path = "storage/logs",
    ) -> TaskRecord:
        """Insert a new task in PENDING state and log the TASK_CREATED event."""
        now = utc_now_iso()
        clean_title = (title or user_prompt.strip().splitlines()[0])[:80]
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO tasks (
                    title, user_prompt, source_channel, source_user,
                    status, current_role, retry_count, max_retries,
                    context_log_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_title,
                    user_prompt,
                    source_channel,
                    source_user,
                    TaskStatus.PENDING.value,
                    None,
                    0,
                    max_retries,
                    "",
                    now,
                    now,
                ),
            )
            task_id = int(cursor.lastrowid)
            log_path = str(Path(logs_dir) / f"task_{task_id:05d}.log")
            conn.execute(
                "UPDATE tasks SET context_log_path = ? WHERE id = ?",
                (log_path, task_id),
            )

        self.log_event(
            event_type="TASK_CREATED",
            actor=f"gateway:{source_channel}",
            task_id=task_id,
            summary=f"Task #{task_id} created from {source_channel}: {clean_title}",
            payload={"source_user": source_user, "prompt": user_prompt},
        )
        return self.get_task(task_id)  # type: ignore[return-value]

    def get_task(self, task_id: int) -> Optional[TaskRecord]:
        """Fetch a single task by ID."""
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return self._row_to_task(row) if row else None

    def get_Next_actionable_tasks(self, limit: int = 2) -> List[TaskRecord]:
        """Return tasks that are ready for orchestrator step execution."""
        active_statuses = (
            TaskStatus.PENDING.value,
            TaskStatus.RESEARCHING.value,
            TaskStatus.OPERATING.value,
            TaskStatus.VALIDATING.value,
            TaskStatus.COMMUNICATING.value,
        )
        placeholders = ",".join("?" for _ in active_statuses)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM tasks
                WHERE status IN ({placeholders})
                ORDER BY id ASC
                LIMIT ?
                """,
                (*active_statuses, limit),
            ).fetchall()
            return [self._row_to_task(r) for r in rows]

    def update_task(self, task_id: int, **fields: Any) -> Optional[TaskRecord]:
        """Update specific columns on a task record and refresh `updated_at`."""
        if not fields:
            return self.get_task(task_id)
        fields["updated_at"] = utc_now_iso()
        columns = ", ".join(f"{k} = ?" for k in fields.keys())
        values = list(fields.values()) + [task_id]
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE tasks SET {columns} WHERE id = ?", values)
        return self.get_task(task_id)

    def record_agent_spawn(
        self,
        role: str,
        provider_name: str,
        task_id: Optional[int] = None,
        cli_log_path: Optional[str] = None,
    ) -> int:
        """Record that an ephemeral agent process has been spawned."""
        now = utc_now_iso()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO agent_runs (task_id, role, provider_name, status, spawned_at, cli_log_path)
                VALUES (?, ?, ?, 'RUNNING', ?, ?)
                """,
                (task_id, role, provider_name, now, cli_log_path),
            )
            run_id = int(cursor.lastrowid)

        self.log_event(
            event_type="AGENT_SPAWNED",
            actor=f"role:{role}",
            task_id=task_id,
            summary=f"Spawned ephemeral `{role}` agent via `{provider_name}` (run_id={run_id})",
            payload={"run_id": run_id, "provider": provider_name, "cli_log_path": cli_log_path},
        )
        return run_id

    def record_agent_shutdown(
        self,
        run_id: int,
        role: str,
        task_id: Optional[int],
        success: bool,
        duration_seconds: float,
        exit_code: int,
        output_preview: str,
        error_text: Optional[str] = None,
    ) -> None:
        """Record that the ephemeral agent finished its task and shut down."""
        now = utc_now_iso()
        status = "COMPLETED" if success else "FAILED"
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE agent_runs
                SET status = ?, shutdown_at = ?, duration_seconds = ?,
                    exit_code = ?, output_preview = ?, error_text = ?
                WHERE id = ?
                """,
                (
                    status,
                    now,
                    duration_seconds,
                    exit_code,
                    output_preview[:1000],
                    error_text,
                    run_id,
                ),
            )

        self.log_event(
            event_type="AGENT_SHUTDOWN",
            actor=f"role:{role}",
            task_id=task_id,
            severity="INFO" if success else "WARN",
            summary=(
                f"Shut down ephemeral `{role}` agent (run_id={run_id}, "
                f"status={status}, duration={duration_seconds:.2f}s)"
            ),
            payload={
                "run_id": run_id,
                "status": status,
                "exit_code": exit_code,
                "duration_seconds": round(duration_seconds, 2),
                "error": error_text,
            },
        )

    def get_recent_events(self, limit: int = 25, task_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Return recent events from the database for status inspection or watchers."""
        with self._lock, self._connect() as conn:
            if task_id is not None:
                rows = conn.execute(
                    "SELECT * FROM events WHERE task_id = ? ORDER BY id DESC LIMIT ?",
                    (task_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM events ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_all_tasks(self, limit: int = 20) -> List[TaskRecord]:
        """Return recent tasks ordered by newest first."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._row_to_task(r) for r in rows]

    def is_world_item_seen(self, item_hash: str) -> bool:
        """Check whether an external world RSS/API item was already processed."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM world_seen_items WHERE item_hash = ?",
                (item_hash,),
            ).fetchone()
            return row is not None

    def mark_world_item_seen(self, item_hash: str, source_name: str, title: str, url: str) -> None:
        """Record an external world item so it is not re-processed repeatedly."""
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO world_seen_items (item_hash, source_name, title, url, discovered_at, processed)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (item_hash, source_name, title, url, utc_now_iso()),
            )

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            id=row["id"],
            title=row["title"],
            user_prompt=row["user_prompt"],
            source_channel=row["source_channel"],
            source_user=row["source_user"],
            status=row["status"],
            current_role=row["current_role"],
            retry_count=row["retry_count"],
            max_retries=row["max_retries"],
            context_log_path=row["context_log_path"],
            research_notes=row["research_notes"],
            operator_output=row["operator_output"],
            validation_feedback=row["validation_feedback"],
            final_response=row["final_response"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
