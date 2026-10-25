"""Hybrid Event-Driven Orchestrator (State Machine Dispatcher).

Routes tasks through ephemeral agents:
  PENDING -> RESEARCHING (Researcher)
          -> OPERATING   (Operator)
          -> VALIDATING  (Validator)
             - If PASS: -> COMMUNICATING (Communicator) -> COMPLETED
             - If FAIL and retry_count < max_retries: -> OPERATING (retry loop)
             - If FAIL and retry_count >= max_retries: -> COMMUNICATING -> ESCALATED

Each agent is spawned *only* for its specific step, writes its output to the
per-task context log file and SQLite database, and is immediately shut down.
"""

import re
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.models import RoleName, TaskRecord, TaskStatus
from core.providers import EphemeralAgentRunner


class HybridOrchestrator:
    """State-machine dispatcher coordinating ephemeral agents and context logs."""

    def __init__(
        self,
        workspace_root: Path,
        config: Dict[str, Any],
        db: WorkspaceDatabase,
        log_manager: ContextLogManager,
        runner: EphemeralAgentRunner,
        outbound_callback: Optional[Callable[[TaskRecord, str], None]] = None,
        provider_override: Optional[str] = None,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.config = config
        self.db = db
        self.log_manager = log_manager
        self.runner = runner
        self.outbound_callback = outbound_callback
        self.provider_override = provider_override

    def submit_task(
        self,
        user_prompt: str,
        title: Optional[str] = None,
        source_channel: str = "console",
        source_user: str = "local_user",
    ) -> TaskRecord:
        """Create a new task, initialize its context log file, and enqueue it."""
        max_retries = int(
            self.config.get("orchestrator", {}).get("max_validation_retries", 3)
        )
        logs_dir = self.workspace_root / self.config.get("logs_dir", "storage/logs")
        task = self.db.create_task(
            user_prompt=user_prompt,
            title=title,
            source_channel=source_channel,
            source_user=source_user,
            max_retries=max_retries,
            logs_dir=logs_dir,
        )
        self.log_manager.initialize_task_log(task)
        return task

    def process_next_step(self, task: TaskRecord) -> TaskRecord:
        """Advance `task` by one ephemeral agent step based on its current state."""
        status = task.status

        if status == TaskStatus.PENDING.value:
            task = self._transition(task.id, TaskStatus.RESEARCHING, RoleName.RESEARCHER.value)
            return self._execute_researcher(task)

        if status == TaskStatus.RESEARCHING.value:
            return self._execute_researcher(task)

        if status == TaskStatus.OPERATING.value:
            return self._execute_operator(task)

        if status == TaskStatus.VALIDATING.value:
            return self._execute_validator(task)

        if status == TaskStatus.COMMUNICATING.value:
            return self._execute_communicator(task)

        return task

    def run_task_to_completion(self, task_id: int) -> TaskRecord:
        """Step a single task through the state machine until it reaches a terminal state."""
        terminal = {
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
            TaskStatus.ESCALATED.value,
        }
        task = self.db.get_task(task_id)
        if not task:
            raise ValueError(f"Task #{task_id} not found.")

        while task.status not in terminal:
            task = self.process_next_step(task)
        return task

    def _execute_researcher(self, task: TaskRecord) -> TaskRecord:
        role = RoleName.RESEARCHER.value
        context_text = self.log_manager.read_context(task.context_log_path)
        prompt = (
            f"Task ID: #{task.id}\n"
            f"Task Title: {task.title}\n"
            f"Context Log File: {task.context_log_path}\n\n"
            f"### Current Task Context Log\n"
            f"{context_text}\n\n"
            f"### Instructions for Researcher\n"
            f"Investigate the user's request, inspect relevant files or information, "
            f"and produce a concise Research & Execution Plan for the Operator agent."
        )
        result = self._spawn_and_log(task, role, prompt)
        if not result.success:
            return self._fail_or_escalate(task, role, result.error or "Researcher failed.")

        self.db.update_task(task.id, research_notes=result.output)
        return self._transition(task.id, TaskStatus.OPERATING, RoleName.OPERATOR.value)

    def _execute_operator(self, task: TaskRecord) -> TaskRecord:
        role = RoleName.OPERATOR.value
        context_text = self.log_manager.read_context(task.context_log_path)
        retry_banner = ""
        if task.retry_count > 0 and task.validation_feedback:
            retry_banner = (
                f"\n⚠️ RETRY ATTEMPT {task.retry_count}/{task.max_retries}\n"
                f"Previous Validation Feedback to fix:\n{task.validation_feedback}\n"
            )

        prompt = (
            f"Task ID: #{task.id}\n"
            f"Task Title: {task.title}\n"
            f"Context Log File: {task.context_log_path}\n"
            f"{retry_banner}\n"
            f"### Accumulated Context Log\n"
            f"{context_text}\n\n"
            f"### Instructions for Operator\n"
            f"Execute the required changes or operations in the workspace based on the "
            f"User Request and Researcher Notes. Summarize every action and file changed."
        )
        result = self._spawn_and_log(
            task,
            role,
            prompt,
            attempt=task.retry_count + 1,
        )
        if not result.success:
            return self._handle_retry_or_escalate(
                task,
                reason=f"Operator execution failed: {result.error or 'non-zero exit code'}",
            )

        self.db.update_task(task.id, operator_output=result.output)
        return self._transition(task.id, TaskStatus.VALIDATING, RoleName.VALIDATOR.value)

    def _execute_validator(self, task: TaskRecord) -> TaskRecord:
        role = RoleName.VALIDATOR.value
        context_text = self.log_manager.read_context(task.context_log_path)
        prompt = (
            f"Task ID: #{task.id}\n"
            f"Task Title: {task.title}\n"
            f"Retry Count: {task.retry_count}/{task.max_retries}\n\n"
            f"### Accumulated Context Log\n"
            f"{context_text}\n\n"
            f"### Instructions for Validator\n"
            f"Verify the Operator's work against the original user request. "
            f"Your output MUST include a line starting with either:\n"
            f"VERDICT: PASS\n"
            f"or\n"
            f"VERDICT: FAIL\n"
            f"followed by detailed verification notes."
        )
        result = self._spawn_and_log(
            task,
            role,
            prompt,
            attempt=task.retry_count + 1,
        )
        if not result.success:
            return self._handle_retry_or_escalate(
                task,
                reason=f"Validator execution error: {result.error or 'unknown error'}",
            )

        passed = self._parse_validation_verdict(result.output)
        self.db.update_task(task.id, validation_feedback=result.output)

        if passed:
            self.db.log_event(
                event_type="VALIDATION_PASSED",
                actor="role:validator",
                task_id=task.id,
                summary=f"Task #{task.id} passed validation on attempt {task.retry_count + 1}.",
            )
            return self._transition(task.id, TaskStatus.COMMUNICATING, RoleName.COMMUNICATOR.value)

        return self._handle_retry_or_escalate(
            task,
            reason=f"Validator returned FAIL:\n{result.output}",
        )

    def _execute_communicator(self, task: TaskRecord) -> TaskRecord:
        role = RoleName.COMMUNICATOR.value
        context_text = self.log_manager.read_context(task.context_log_path)
        is_escalated = task.retry_count >= task.max_retries and "FAIL" in task.validation_feedback.upper()
        prompt = (
            f"Task ID: #{task.id}\n"
            f"Target Channel: {task.source_channel} (Recipient: {task.source_user})\n"
            f"Outcome State: {'ESCALATED (Needs User Attention)' if is_escalated else 'COMPLETED'}\n\n"
            f"### Accumulated Context Log\n"
            f"{context_text}\n\n"
            f"### Instructions for Communicator\n"
            f"Write a clear, concise, user-friendly response suitable for {task.source_channel} "
            f"(Discord / Zalo / Messenger / Console) summarizing what was done, key results, "
            f"and any follow-up needed."
        )
        result = self._spawn_and_log(task, role, prompt)
        final_message = result.output if result.success else (
            f"Task #{task.id} ({task.title}) finished. "
            f"Operator summary:\n{task.operator_output[:500]}"
        )

        final_state = TaskStatus.ESCALATED if is_escalated else TaskStatus.COMPLETED
        updated = self.db.update_task(
            task.id,
            status=final_state.value,
            current_role=None,
            final_response=final_message,
        )
        self.db.log_event(
            event_type=f"TASK_{final_state.value}",
            actor="orchestrator",
            task_id=task.id,
            summary=f"Task #{task.id} finished with state {final_state.value}.",
            payload={"channel": task.source_channel, "response_preview": final_message[:300]},
        )

        if updated and self.outbound_callback:
            self.outbound_callback(updated, final_message)
        return updated  # type: ignore[return-value]

    def _spawn_and_log(
        self,
        task: TaskRecord,
        role: str,
        prompt: str,
        attempt: int = 1,
    ):
        role_cfg = self.config.get("roles", {}).get(role, {})
        provider_name = self.provider_override or role_cfg.get("provider", "agy_headless_safe")
        cli_log_path = self.log_manager.create_agent_cli_log_path(task.id, role)

        run_id = self.db.record_agent_spawn(
            role=role,
            provider_name=provider_name,
            task_id=task.id,
            cli_log_path=str(cli_log_path),
        )
        result = self.runner.run_role(
            role=role,
            task_prompt=prompt,
            cli_log_path=cli_log_path,
            provider_override=self.provider_override,
        )
        self.db.record_agent_shutdown(
            run_id=run_id,
            role=role,
            task_id=task.id,
            success=result.success,
            duration_seconds=result.duration_seconds,
            exit_code=result.exit_code,
            output_preview=result.output,
            error_text=result.error,
        )
        self.log_manager.append_agent_step(
            task_log_path=task.context_log_path,
            result=result,
            attempt=attempt,
        )
        return result

    def _handle_retry_or_escalate(self, task: TaskRecord, reason: str) -> TaskRecord:
        next_retry = task.retry_count + 1
        self.db.update_task(
            task.id,
            retry_count=next_retry,
            validation_feedback=reason,
        )
        if next_retry < task.max_retries:
            self.db.log_event(
                event_type="TASK_RETRY_SCHEDULED",
                actor="orchestrator",
                task_id=task.id,
                severity="WARN",
                summary=f"Task #{task.id} retry {next_retry}/{task.max_retries} routed back to Operator.",
                payload={"reason": reason[:500]},
            )
            return self._transition(task.id, TaskStatus.OPERATING, RoleName.OPERATOR.value)

        self.db.log_event(
            event_type="TASK_MAX_RETRIES_EXCEEDED",
            actor="orchestrator",
            task_id=task.id,
            severity="ERROR",
            summary=f"Task #{task.id} reached max retries ({task.max_retries}). Escalating via Communicator.",
            payload={"reason": reason[:500]},
        )
        return self._transition(task.id, TaskStatus.COMMUNICATING, RoleName.COMMUNICATOR.value)

    def _fail_or_escalate(self, task: TaskRecord, role: str, error_msg: str) -> TaskRecord:
        self.db.update_task(
            task.id,
            validation_feedback=f"Critical failure in {role}: {error_msg}",
            retry_count=task.max_retries,
        )
        return self._transition(task.id, TaskStatus.COMMUNICATING, RoleName.COMMUNICATOR.value)

    def _transition(self, task_id: int, new_status: TaskStatus, next_role: Optional[str]) -> TaskRecord:
        updated = self.db.update_task(
            task_id,
            status=new_status.value,
            current_role=next_role,
        )
        self.db.log_event(
            event_type="STATE_TRANSITION",
            actor="orchestrator",
            task_id=task_id,
            summary=f"Task #{task_id} -> {new_status.value} (assigned role: {next_role})",
        )
        return updated  # type: ignore[return-value]

    @staticmethod
    def _parse_validation_verdict(output: str) -> bool:
        """Check whether the Validator output indicates PASS or FAIL."""
        match = re.search(r"VERDICT\s*:\s*(PASS|FAIL)", output, re.IGNORECASE)
        if match:
            return match.group(1).upper() == "PASS"
        # Fallback heuristic if the model omitted the strict prefix
        upper = output.upper()
        if "VERDICT: FAIL" in upper or "STATUS: FAIL" in upper:
            return False
        return True
