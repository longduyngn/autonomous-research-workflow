"""Per-task context log manager and structured file logger.

Every task gets its own dedicated markdown/log file (`storage/logs/task_<id>.log`)
so ephemeral agents can read the exact accumulated context when spawned and append
their findings before shutting down.
"""

from pathlib import Path
from typing import Optional
from core.models import AgentRunResult, TaskRecord, utc_now_iso


class ContextLogManager:
    """Manages per-task context log files and agent run transcripts."""

    def __init__(self, logs_dir: Path) -> None:
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.agent_runs_dir = self.logs_dir / "agent_runs"
        self.agent_runs_dir.mkdir(parents=True, exist_ok=True)

    def get_task_log_path(self, task_id: int) -> Path:
        """Return the path for a task's persistent context log file."""
        return self.logs_dir / f"task_{task_id:05d}.log"

    def initialize_task_log(self, task: TaskRecord) -> Path:
        """Create the initial context log file for a newly created task."""
        path = Path(task.context_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            f"# Task #{task.id:05d}: {task.title}\n"
            f"- **Created At**: {task.created_at}\n"
            f"- **Source Channel**: {task.source_channel} (User: {task.source_user})\n"
            f"- **Max Validation Retries**: {task.max_retries}\n\n"
            f"## Original User Request\n"
            f"{task.user_prompt}\n\n"
            f"---\n"
            f"## Execution Timeline & Context History\n"
        )
        path.write_text(header, encoding="utf-8")
        return path

    def append_agent_step(
        self,
        task_log_path: str | Path,
        result: AgentRunResult,
        attempt: int = 1,
        extra_note: Optional[str] = None,
    ) -> None:
        """Append an ephemeral agent's execution output to the task's context log."""
        path = Path(task_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        status_tag = "SUCCESS" if result.success else "ERROR"
        entry = (
            f"\n### [{utc_now_iso()}] Role: `{result.role.upper()}` "
            f"(Provider: `{result.provider_name}`, Attempt: {attempt}, "
            f"Status: {status_tag}, Duration: {result.duration_seconds:.2f}s)\n"
        )
        if extra_note:
            entry += f"> **Orchestrator Note**: {extra_note}\n\n"
        if result.error:
            entry += f"**Error**:\n```text\n{result.error}\n```\n\n"
        entry += f"**Agent Output**:\n{result.output.strip()}\n\n---\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(entry)

    def read_context(self, task_log_path: str | Path, max_chars: int = 32000) -> str:
        """Read the accumulated context log for injection into the next ephemeral agent."""
        path = Path(task_log_path)
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8")
        if len(text) <= max_chars:
            return text
        # Preserve the header (first 4000 chars) + most recent tail context
        head = text[:4000]
        tail = text[-(max_chars - 4100) :]
        return f"{head}\n\n...[TRUNCATED MIDDLE CONTEXT]...\n\n{tail}"

    def create_agent_cli_log_path(self, task_id: int, role: str) -> Path:
        """Return a dedicated CLI log path for a single ephemeral agent invocation."""
        ts = utc_now_iso().replace(":", "-").replace(".", "-")
        return self.agent_runs_dir / f"task_{task_id:05d}_{role}_{ts}.cli.log"
