"""SQLite Event Store & Task State Database (WAL Mode).

Stores every workspace event, task state transition, ephemeral agent spawn/shutdown,
workspace health snapshot, and external world digest in a single SQLite database.
"""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            with conn:
                yield conn
        finally:
            conn.close()

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
                CREATE TABLE IF NOT EXISTS message_outbox (
                    task_id INTEGER PRIMARY KEY,
                    channel TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    message TEXT NOT NULL,
                    delivered INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0,
                    lease_until REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS task_waits(task_id INTEGER PRIMARY KEY,kind TEXT NOT NULL,
                    resume_status TEXT NOT NULL,reason TEXT NOT NULL,dependency TEXT NOT NULL,
                    since REAL NOT NULL,due REAL NOT NULL);
                """
            )
            existing = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
            additions = {
                "outcome": "TEXT NOT NULL DEFAULT ''",
                "plan_json": "TEXT NOT NULL DEFAULT '{}'",
                "step_index": "INTEGER NOT NULL DEFAULT 0",
                "workspace_path": "TEXT NOT NULL DEFAULT ''",
                "lease_owner": "TEXT NOT NULL DEFAULT ''",
                "lease_expires": "REAL NOT NULL DEFAULT 0",
                "heartbeat_at": "REAL NOT NULL DEFAULT 0",
                "started_at": "REAL NOT NULL DEFAULT 0",
                "remote_job_id": "TEXT NOT NULL DEFAULT ''",
                "event_key": "TEXT NOT NULL DEFAULT ''",
                "parent_task_id": "INTEGER NOT NULL DEFAULT 0",
                "provider_override": "TEXT NOT NULL DEFAULT ''",
                "replan_count": "INTEGER NOT NULL DEFAULT 0",
                "feedback_revision": "INTEGER NOT NULL DEFAULT 0",
                "waiting_seconds": "REAL NOT NULL DEFAULT 0",
                "wait_deadline": "REAL NOT NULL DEFAULT 0",
                "plan_revision": "INTEGER NOT NULL DEFAULT 0",
                "review_reason": "TEXT NOT NULL DEFAULT ''",
                "event_review_count": "INTEGER NOT NULL DEFAULT 0",
                "inputs_json": "TEXT NOT NULL DEFAULT '[]'",
                "source_path": "TEXT NOT NULL DEFAULT ''",
                "plan_approval": "TEXT NOT NULL DEFAULT 'none'",
            }
            for name, definition in additions.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")
            run_columns = {r[1] for r in conn.execute("PRAGMA table_info(agent_runs)")}
            outbox_columns = {r[1] for r in conn.execute('PRAGMA table_info(message_outbox)')}
            if 'revision' not in outbox_columns:
                conn.execute('ALTER TABLE message_outbox ADD COLUMN revision INTEGER NOT NULL DEFAULT 1')
            for name, definition in {
                "step_index": "INTEGER NOT NULL DEFAULT -1",
                "attempt": "INTEGER NOT NULL DEFAULT 1",
                "run_label": "TEXT NOT NULL DEFAULT ''",
                "session_id": "TEXT NOT NULL DEFAULT ''",
                "usage_json": "TEXT NOT NULL DEFAULT '{}'",
                "num_turns": "INTEGER NOT NULL DEFAULT 0",
                "total_cost_usd": "REAL NOT NULL DEFAULT 0",
            }.items():
                if name not in run_columns:
                    conn.execute(f"ALTER TABLE agent_runs ADD COLUMN {name} {definition}")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_event_key ON tasks(event_key) WHERE event_key != ''")
            if "lease_owner" not in existing:
                # Legacy active records have no proof that their previous process stopped.
                conn.execute("""UPDATE tasks SET status='ESCALATED', outcome='ESCALATED', current_role=NULL,
                    validation_feedback='Legacy in-progress task: inspect artifacts before a follow-up request.'
                    WHERE status IN ('PLANNING','RESEARCHING','OPERATING','VALIDATING','COMMUNICATING')""")

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
        event_key: str = "",
        ready: bool = True,
    ) -> TaskRecord:
        """Insert a new task in PENDING state and log the TASK_CREATED event."""
        if not user_prompt.strip():
            raise ValueError("Task prompt must not be empty.")
        now = utc_now_iso()
        clean_title = (title or user_prompt.strip().splitlines()[0])[:80]
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if event_key:
                existing = conn.execute("SELECT * FROM tasks WHERE event_key=?", (event_key,)).fetchone()
                if existing:
                    task = self._row_to_task(existing)
                    task._newly_created = False
                    return task
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
                    TaskStatus.PENDING.value if ready else "INITIALIZING",
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
                "UPDATE tasks SET context_log_path = ?, event_key=? WHERE id = ?",
                (log_path, event_key, task_id),
            )

        self.log_event(
            event_type="TASK_CREATED",
            actor=f"gateway:{source_channel}",
            task_id=task_id,
            summary=f"Task #{task_id} created from {source_channel}: {clean_title}",
            payload={"source_user": source_user, "prompt": user_prompt},
        )
        task = self.get_task(task_id)
        task._newly_created = True
        return task

    def get_task(self, task_id: int) -> Optional[TaskRecord]:
        """Fetch a single task by ID."""
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return self._row_to_task(row) if row else None

    def get_Next_actionable_tasks(self, limit: int = 2) -> List[TaskRecord]:
        """Return tasks that are ready for orchestrator step execution."""
        active_statuses = (
            TaskStatus.PENDING.value,
            TaskStatus.PLANNING.value,
            TaskStatus.RESEARCHING.value,
            TaskStatus.OPERATING.value,
            TaskStatus.VALIDATING.value,
            TaskStatus.COMMUNICATING.value,
            TaskStatus.WAITING_REMOTE.value,
        )
        placeholders = ",".join("?" for _ in active_statuses)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM tasks
                WHERE status IN ({placeholders}) AND NOT EXISTS (SELECT 1 FROM task_waits w WHERE w.task_id=tasks.id)
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
        unknown = set(fields) - set(TaskRecord.__dataclass_fields__)
        if unknown or "id" in fields:
            raise ValueError(f"Invalid task fields: {sorted(unknown or {'id'})}")
        fields["updated_at"] = utc_now_iso()
        columns = ", ".join(f"{k} = ?" for k in fields.keys())
        values = list(fields.values()) + [task_id]
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE tasks SET {columns} WHERE id = ?", values)
        return self.get_task(task_id)

    def claim_task(self, owner: str, capacity: int = 2, lease_seconds: float = 90,
                   task_id: Optional[int] = None) -> Optional[TaskRecord]:
        """Claim under a database write lock; the cap applies across processes."""
        if not 1 <= capacity <= 3:
            raise ValueError("Concurrency must be between 1 and 3")
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Never automatically replay an interrupted shell/GPU submission.
            # A committed wait proves the role boundary was reached before a crash.
            conn.execute("UPDATE tasks SET lease_owner='',lease_expires=0 WHERE lease_expires<? AND status LIKE 'WAITING_%' AND EXISTS (SELECT 1 FROM task_waits w WHERE w.task_id=tasks.id AND tasks.status='WAITING_'||w.kind)",(now,))
            expired = conn.execute("SELECT id FROM tasks WHERE lease_owner != '' AND lease_expires < ?", (now,)).fetchall()
            for row in expired:
                conn.execute("UPDATE agent_runs SET status='INTERRUPTED', shutdown_at=? WHERE task_id=? AND status='RUNNING'",
                             (utc_now_iso(), row["id"]))
                old = conn.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone()
                monitored=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='experiment_jobs'").fetchone()
                tracked=(conn.execute("SELECT id FROM experiment_jobs WHERE task_id=? AND id=? AND state NOT IN ('ARTIFACTS_READY','RECONCILED')",
                         (row['id'],old['remote_job_id'])).fetchone() if monitored and old['remote_job_id'] else None)
                if tracked and old['status'] not in {'COMPLETED','FAILED','ESCALATED'}:
                    # A durable job proves what to observe, not that a command
                    # may be replayed. Fence the old worker and restore polling.
                    conn.execute("UPDATE tasks SET status='WAITING_REMOTE',current_role=NULL,lease_owner='',lease_expires=0,outcome='',waiting_seconds=waiting_seconds+? WHERE id=?",
                                 (max(0,now-old['heartbeat_at']),row['id']))
                    conn.execute('INSERT OR REPLACE INTO task_waits VALUES(?,?,?,?,?,?,?)',
                                 (row['id'],'REMOTE','WAITING_REMOTE','Controller restarted; reconcile the existing registered job',tracked['id'],now,now+1))
                    continue
                from core.recovery import queue_expired_worker
                if queue_expired_worker(conn,old,now):
                    continue
                if old["status"] not in {"COMPLETED", "FAILED", "ESCALATED"}:
                    message = f"Task #{row['id']}: ESCALATED. Worker lease expired; inspect workspace and remote jobs before resuming. Workspace: {old['workspace_path']}"
                    conn.execute("UPDATE tasks SET final_response=? WHERE id=?", (message, row["id"]))
                    conn.execute("INSERT OR IGNORE INTO message_outbox(task_id,channel,recipient,message) VALUES(?,?,?,?)",
                                 (row["id"], old["source_channel"], old["source_user"], message))
            conn.execute("""UPDATE tasks SET status='ESCALATED', outcome='ESCALATED', current_role=NULL,
                validation_feedback='Worker lease expired. Inspect artifacts and remote jobs before resuming.',
                lease_owner='', lease_expires=0, updated_at=?
                WHERE lease_owner != '' AND lease_expires < ? AND status NOT IN ('COMPLETED','FAILED','ESCALATED')""",
                (utc_now_iso(), now))
            conn.execute("UPDATE tasks SET lease_owner='', lease_expires=0 WHERE lease_expires < ?", (now,))
            cutoff = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
            # A crash during input/log preparation is not proof of a ready task.
            for row in conn.execute("SELECT * FROM tasks WHERE status='INITIALIZING' AND updated_at<?", (cutoff,)).fetchall():
                message = f"Task #{row['id']}: ESCALATED. Input preparation was interrupted; inspect inputs before retrying."
                conn.execute("UPDATE tasks SET status='ESCALATED',outcome='ESCALATED',validation_feedback=?,final_response=?,updated_at=? WHERE id=?",
                             (message, message, utc_now_iso(), row['id']))
                conn.execute("INSERT OR IGNORE INTO message_outbox(task_id,channel,recipient,message) VALUES(?,?,?,?)",
                             (row['id'], row['source_channel'], row['source_user'], message))
            count = conn.execute("SELECT COUNT(*) FROM tasks WHERE lease_owner != '' AND lease_expires >= ?", (now,)).fetchone()[0]
            if count >= capacity:
                return None
            params = []
            where = "status IN ('PENDING','PLANNING','RESEARCHING','OPERATING','VALIDATING','COMMUNICATING','WAITING_REMOTE') AND lease_owner=''"
            where += ' AND NOT EXISTS (SELECT 1 FROM task_waits w WHERE w.task_id=tasks.id)'
            if task_id is not None:
                where += " AND id=?"
                params.append(task_id)
            row = conn.execute(f"SELECT * FROM tasks WHERE {where} ORDER BY id LIMIT 1", params).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE tasks SET lease_owner=?, lease_expires=?, heartbeat_at=?, started_at=CASE WHEN started_at=0 THEN ? ELSE started_at END WHERE id=?",
                         (owner, now + lease_seconds, now, now, row["id"]))
            return self._row_to_task(conn.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone())

    def renew_lease(self, task_id, owner, lease_seconds=90):
        now = time.time()
        with self._lock, self._connect() as conn:
            return conn.execute("UPDATE tasks SET lease_expires=?, heartbeat_at=? WHERE id=? AND lease_owner=? AND lease_expires>=?",
                (now + lease_seconds, now, task_id, owner, now)).rowcount == 1

    def release_lease(self, task_id, owner):
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE tasks SET lease_owner='', lease_expires=0 WHERE id=? AND lease_owner=?", (task_id, owner))

    def finalize_task(self, task_id, outcome, message, feedback=None, expected_feedback_revision=None):
        if outcome not in {"COMPLETED", "FAILED", "ESCALATED"}:
            raise ValueError("Invalid terminal outcome")
        with self._lock, self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if expected_feedback_revision is not None:
                pending = conn.execute('SELECT 1 FROM task_feedback WHERE task_id=? AND applied=0 LIMIT 1', (task_id,)).fetchone()
                revision = conn.execute('SELECT feedback_revision FROM tasks WHERE id=?', (task_id,)).fetchone()[0]
                if pending or revision != expected_feedback_revision:
                    return self._row_to_task(conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())
            conn.execute("UPDATE tasks SET status=?, outcome=?, final_response=?, current_role=NULL, updated_at=? WHERE id=?",
                         (outcome, outcome, message, utc_now_iso(), task_id))
            if feedback is not None:
                conn.execute("UPDATE tasks SET validation_feedback=? WHERE id=?", (feedback, task_id))
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            conn.execute("""INSERT INTO message_outbox(task_id,channel,recipient,message) VALUES(?,?,?,?)
                ON CONFLICT(task_id) DO UPDATE SET channel=excluded.channel,recipient=excluded.recipient,
                message=excluded.message,delivered=0,attempts=0,next_attempt=0,lease_until=0,
                revision=message_outbox.revision+1
                WHERE message_outbox.message!=excluded.message OR message_outbox.channel!=excluded.channel
                  OR message_outbox.recipient!=excluded.recipient""",
                         (task_id, task["source_channel"], task["source_user"], message))
        return self.get_task(task_id)

    def claim_outbound(self):
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM message_outbox WHERE delivered=0 AND next_attempt<=? AND lease_until<? ORDER BY task_id LIMIT 1", (now, now)).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE message_outbox SET lease_until=? WHERE task_id=?", (now + 120, row["task_id"]))
            return dict(row)

    def finish_outbound(self, task_id, delivered, revision=None):
        with self._lock, self._connect() as conn:
            if revision is None:
                revision=conn.execute('SELECT revision FROM message_outbox WHERE task_id=?',(task_id,)).fetchone()
                revision=revision[0] if revision else 0
            conn.execute("UPDATE message_outbox SET delivered=?, attempts=attempts+1, next_attempt=?, lease_until=0 WHERE task_id=? AND revision=?",
                         (int(delivered), time.time() + 30, task_id,revision))

    def task_timeline(self, task_id):
        task = self.get_task(task_id)
        if not task:
            raise ValueError("Task not found")
        with self._connect() as conn:
            runs = [dict(row) for row in conn.execute(
                "SELECT id,role,provider_name,status,spawned_at,shutdown_at,step_index,attempt,run_label,session_id FROM agent_runs WHERE task_id=? ORDER BY id", (task_id,))]
        result = {"task_id": task.id, "received_at_utc": task.created_at,
                  "event_key": task.event_key, "parent_task_id": task.parent_task_id, "roles": runs}
        if task.event_key.startswith("discord:") and task.event_key[8:].isdigit():
            result["discord_message_sent_at_utc"] = datetime.fromtimestamp(
                ((int(task.event_key[8:]) >> 22) + 1420070400000) / 1000, timezone.utc).isoformat()
        return result

    def record_agent_spawn(
        self,
        role: str,
        provider_name: str,
        task_id: Optional[int] = None,
        cli_log_path: Optional[str] = None,
        step_index: int = -1,
        attempt: int = 1,
        run_label: str = "",
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
            conn.execute("UPDATE agent_runs SET step_index=?,attempt=?,run_label=? WHERE id=?",
                         (step_index, attempt, run_label, run_id))

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
        session_id: str = "",
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
            conn.execute("UPDATE agent_runs SET session_id=? WHERE id=?", (session_id, run_id))

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
        return TaskRecord(**{k: row[k] for k in row.keys() if k in TaskRecord.__dataclass_fields__})
