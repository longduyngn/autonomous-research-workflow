"""Data models and state enums for the multi-agent workflow."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


class TaskStatus(str, Enum):
    """Lifecycle states of a task in the hybrid state machine."""

    PENDING = "PENDING"
    INITIALIZING = "INITIALIZING"
    PLANNING = "PLANNING"
    RESEARCHING = "RESEARCHING"  # legacy stored status; read as PLANNING
    OPERATING = "OPERATING"
    VALIDATING = "VALIDATING"
    COMMUNICATING = "COMMUNICATING"
    WAITING_REMOTE = "WAITING_REMOTE"
    WAITING_RESOURCE = "WAITING_RESOURCE"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    WAITING_PROVIDER = "WAITING_PROVIDER"
    WAITING_INPUT = "WAITING_INPUT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"


class RoleName(str, Enum):
    """Standard agent roles in the workspace."""

    PLANNER = "planner"
    RESEARCHER = "planner"  # legacy alias of PLANNER
    OPERATOR = "operator"
    VALIDATOR = "validator"
    COMMUNICATOR = "communicator"
    WORKSPACE_WATCHER = "workspace_watcher"
    WORLD_WATCHER = "world_watcher"
    REMOTE_MANAGER = "remote_manager"


@dataclass
class AgentRunResult:
    """Result returned by an ephemeral agent execution after it shuts down."""

    role: str
    provider_name: str
    success: bool
    output: str
    exit_code: int = 0
    duration_seconds: float = 0.0
    error: Optional[str] = None
    structured_data: Dict[str, Any] = field(default_factory=dict)
    log_file: Optional[str] = None


@dataclass
class TaskRecord:
    """Represents a task tracked in SQLite and context log files."""

    id: int
    title: str
    user_prompt: str
    source_channel: str
    source_user: str
    status: str
    current_role: Optional[str]
    retry_count: int
    max_retries: int
    context_log_path: str
    research_notes: str = ""
    operator_output: str = ""
    validation_feedback: str = ""
    final_response: str = ""
    outcome: str = ""
    plan_json: str = "{}"
    step_index: int = 0
    workspace_path: str = ""
    lease_owner: str = ""
    lease_expires: float = 0.0
    heartbeat_at: float = 0.0
    started_at: float = 0.0
    remote_job_id: str = ""
    event_key: str = ""
    parent_task_id: int = 0
    provider_override: str = ""
    replan_count: int = 0
    feedback_revision: int = 0
    waiting_seconds: float = 0.0
    wait_deadline: float = 0.0
    plan_revision: int = 0
    review_reason: str = ''
    event_review_count: int = 0
    inputs_json: str = "[]"
    source_path: str = ""
    plan_approval: str = "none"  # "required" only when the user asked to approve the plan
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
