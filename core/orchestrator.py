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
import json
import threading
import time
import uuid
import sys
import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.models import RoleName, TaskRecord, TaskStatus, utc_now_iso
from core.providers import EphemeralAgentRunner
from core.plans import parse_plan
from core.processes import run_process
from core.workspaces import WorkspaceManager


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
        self.workspaces = WorkspaceManager(self.workspace_root, config)
        self._worker_local = threading.local()
        self._cancellations = {}
        self._cancel_lock = threading.Lock()
        from core.conversation import Conversation
        self.conversation = Conversation(db, self.submit_task, self._conversation_reply)
        from core.control import ControlPlane
        self.control = ControlPlane(db)

    def _conversation_reply(self, scope, actor, message, history, tasks):
        if (not self.config.get('conversation',{}).get('model_replies',True)
                or 'communicator' not in self.config.get('roles',{}) or not hasattr(self.runner,'for_workspace')):
            return None
        workspace,identity=self._communicator_workspace(scope,actor)
        brief=[{'id':t['id'],'title':t['title'],'status':t['status'],'result':t['final_response'][-2000:]} for t in tasks]
        prompt=('You are the ongoing user-facing communicator, separate from task execution. '
                'Respond naturally in the user language using the saved conversation and controller facts. '
                'No task was created for this message. You cannot create tasks, approve actions, change files '
                'or run experiments from this chat. For new work guide the user to !task <instructions>; '
                'for changes use !feedback <id> <change>; status/search/retry/cancel and approvals are explicit commands. '
                'Ask which task when ambiguous. Never claim a queued/executed action unless controller facts confirm it.\n'
                +json.dumps({'history':history,'tasks':brief,'message':message},ensure_ascii=False))
        runner=self.runner.for_workspace(workspace)
        runner.run_label='conversation-'+identity[:12]+'/communicator'
        result=runner.run_role('communicator',prompt,workspace/'.workflow'/f'chat-{uuid.uuid4().hex}.log')
        return result.output[:1800] if result.success else None

    def _communicator_workspace(self, scope, actor):
        import hashlib
        from core.integrations import prepare_integrations
        identity=hashlib.sha256(json.dumps([os.environ.get('DISCORD_GUILD_ID') or scope,actor]).encode()).hexdigest()
        workspace=self.workspace_root/'storage/conversations'/identity
        workspace.mkdir(parents=True,exist_ok=True)
        if not (workspace/'.workflow/integration.json').is_file():
            prepare_integrations(self.workspace_root,workspace,workspace)
        return workspace,identity

    def cancel_running(self):
        with self._cancel_lock:
            for event in self._cancellations.values():
                event.set()

    def submit_task(
        self,
        user_prompt: str,
        title: Optional[str] = None,
        source_channel: str = "console",
        source_user: str = "local_user",
        event_key: str = "",
        parent_task_id: int = 0,
        provider_override: str = "",
        input_files=None,
        source_path: str = "",
        source_actor: str = "",
    ) -> TaskRecord:
        """Create a new task, initialize its context log file, and enqueue it."""
        # Resolve terse retry commands to real historical instructions, without
        # replacing history or replaying an old workspace's cloud job records.
        match = re.fullmatch(r"(?:reopen|retry)\s+(?:task\s*)?#?(\d+)\.?", user_prompt.strip(), re.I)
        if match:
            parent_task_id = int(match[1])
        provider_request = user_prompt
        if parent_task_id:
            additional_request = "" if match else user_prompt
            seen = set()
            parent = self.db.get_task(parent_task_id)
            if not parent:
                raise ValueError(f"Task #{parent_task_id} does not exist")
            if parent.source_channel != source_channel or parent.source_user != source_user:
                raise ValueError("Retry must come from the original task's channel")
            original = parent
            while original.parent_task_id or re.fullmatch(r"(?:reopen|retry)\s+(?:task\s*)?#?(\d+)\.?", original.user_prompt.strip(), re.I):
                if original.id in seen:
                    raise ValueError("Circular retry reference")
                seen.add(original.id)
                m = re.fullmatch(r"(?:reopen|retry)\s+(?:task\s*)?#?(\d+)\.?", original.user_prompt.strip(), re.I)
                original = self.db.get_task(original.parent_task_id or int(m[1]))
                if not original:
                    raise ValueError("Retry original request is missing")
            user_prompt = (f"Recovery of task #{parent.id}. Original task #{original.id}:\n{original.user_prompt}\n\n"
                f"Original request received at (UTC): {original.created_at}\n"
                f"Previous outcome: {parent.status}\nPrevious feedback:\n{parent.validation_feedback[-5000:]}\n"
                "Execute the original request and verify actual outputs. Historical outputs are not proof of a fresh cloud run.")
            if additional_request:
                user_prompt += "\n\nAdditional recovery instructions:\n" + additional_request
            provider_request = original.user_prompt
            provider_override = provider_override or original.provider_override
            if input_files is None:
                input_files = [item["snapshot"] for item in json.loads(original.inputs_json)]
            source_path = source_path or original.source_path
        chosen = re.match(r"(?:--provider\s+|use\s+provider\s+)(codex|antigravity|claude|opencode)(?:\s|,|$)", provider_request.strip(), re.I)
        if chosen and not provider_override:
            provider_override = {"codex": "codex_cli", "antigravity": "agy_headless_auto", "claude": "claude_cli", "opencode": "opencode_cli"}[chosen[1].lower()]
        # Persist this choice so another daemon claiming the task uses the same CLI.
        provider_override = provider_override or self.provider_override or ""
        if provider_override and provider_override not in self.config.get("providers", {}):
            raise ValueError(f"Unknown provider: {provider_override}")
        from core.inputs import validate_inputs, store_inputs
        sources = validate_inputs(input_files or [])
        if source_path and not Path(source_path).is_dir():
            raise ValueError("Task source directory does not exist")
        max_retries = int(
            self.config.get("orchestrator", {}).get("max_attempts", self.config.get("orchestrator", {}).get("max_validation_retries", 3))
        )
        logs_dir = self.workspace_root / self.config.get("logs_dir", "storage/logs")
        task = self.db.create_task(
            user_prompt=user_prompt,
            title=title,
            source_channel=source_channel,
            source_user=source_user,
            max_retries=max_retries,
            logs_dir=logs_dir,
            event_key=event_key,
            ready=False,
        )
        if not getattr(task, "_newly_created", True):
            return task
        try:
            inputs = store_inputs(self.workspace_root, task.id, sources)
            self.log_manager.initialize_task_log(task)
            if source_actor:
                with self.db._connect() as c:
                    c.execute('INSERT OR IGNORE INTO task_actors VALUES(?,?,?)',(task.id,source_actor,source_user))
            return self.db.update_task(task.id, status="PENDING", parent_task_id=parent_task_id,
                provider_override=provider_override, inputs_json=json.dumps(inputs),
                source_path=str(Path(source_path).resolve()) if source_path else "")
        except Exception as exc:
            self.db.update_task(task.id, status="ESCALATED", outcome="ESCALATED",
                                validation_feedback=f"Input preparation failed: {exc}")
            self.db.log_event("INPUT_PREPARATION_FAILED", "orchestrator", str(exc), task_id=task.id, severity="ERROR")
            raise

    def process_next_step(self, task: TaskRecord) -> TaskRecord:
        """Advance `task` by one ephemeral agent step based on its current state."""
        if self.conversation.apply_feedback(task.id):
            task = self.db.get_task(task.id)
            self.db.log_event('FEEDBACK_APPLIED', 'orchestrator',
                              f'Feedback revision {task.feedback_revision}', task_id=task.id)
        status = task.status
        role = {'PENDING':'researcher','RESEARCHING':'researcher','OPERATING':'operator',
                'VALIDATING':'validator','COMMUNICATING':'communicator'}.get(status)
        if role:
            provider = task.provider_override or self.config.get('roles',{}).get(role,{}).get('provider','')
            if provider and not self.control.available(provider):
                return self.control.wait(task.id,'PROVIDER',status,'Configured provider is cooling down',provider)

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

        if status == TaskStatus.WAITING_REMOTE.value:
            return self._poll_remote(task)

        return task

    def run_task_to_completion(self, task_id: int, owner=None) -> TaskRecord:
        """Step a single task through the state machine until it reaches a terminal state."""
        terminal = {
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
            TaskStatus.ESCALATED.value,
        }
        task = self.db.get_task(task_id)
        if not task:
            raise ValueError(f"Task #{task_id} not found.")

        if task.status in terminal:
            return task
        if owner is None:
            owner = uuid.uuid4().hex
            task = self.db.claim_task(owner, int(self.config.get("orchestrator", {}).get("max_concurrent_tasks", 2)), task_id=task_id)
            if task is None:
                raise RuntimeError("Task is queued or already owned by another worker")
        elif task.lease_owner != owner:
            raise RuntimeError("Worker does not own this task")
        stopped = threading.Event()
        lost = threading.Event()
        cancelled = threading.Event()
        with self._cancel_lock:
            self._cancellations[task_id] = cancelled
        self._worker_local.owner = owner
        self._worker_local.task_id = task_id
        self._worker_local.cancelled = cancelled
        def heartbeat():
            while not stopped.wait(15):
                if not self.db.renew_lease(task_id, owner):
                    lost.set()
                    cancelled.set()
                    return
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            if not Path(task.context_log_path).exists():
                self.log_manager.initialize_task_log(task)
            workspace = self.workspaces.prepare(task)
            task = self.db.update_task(task_id, workspace_path=str(workspace))
            self._worker_local.runner = self.runner.for_workspace(workspace) if hasattr(self.runner, "for_workspace") else self.runner
            self._worker_local.runner.cancel_event = cancelled
            self._worker_local.runner.deadline = task.started_at + task.waiting_seconds + float(self.config.get("orchestrator", {}).get("task_timeout_seconds", 86400))
            while task.status not in terminal:
                current = self.db.get_task(task_id)
                if lost.is_set() or current.lease_owner != owner:
                    raise RuntimeError("Task lease was lost; stopping worker")
                if cancelled.is_set():
                    raise InterruptedError("Worker shutdown requested; inspect artifacts before resuming")
                budget = float(self.config.get("orchestrator", {}).get("task_timeout_seconds", 86400))
                if time.time() - current.started_at - current.waiting_seconds > budget:
                    task = self.db.finalize_task(task_id, "ESCALATED", "Task wall-clock budget exceeded",
                                                 feedback="Task wall-clock budget exceeded")
                    break
                task = self.process_next_step(current)
                if task.status.startswith('WAITING_'):
                    break  # Durable wait retains state; release this task's worker slot.
            return task
        except Exception as exc:
            from core.approvals import TaskDeferred
            if isinstance(exc,TaskDeferred):
                return self.db.get_task(task_id)
            if self.db.get_task(task_id).lease_owner == owner:
                self.db.finalize_task(task_id, "ESCALATED", f"Task #{task_id} interrupted: {exc}\nWorkspace: {task.workspace_path}",
                                      feedback=f"Worker interrupted: {exc}")
            raise
        finally:
            stopped.set()
            thread.join(timeout=2)
            self.db.release_lease(task_id, owner)
            token_env = self.config.get("cloud", {}).get("colab_bridge_token_env", "COLAB_BRIDGE_TOKEN")
            if (task.workspace_path and os.environ.get(token_env)
                    and json.loads(task.plan_json).get("backend") == "colab"):
                try:
                    from core.cloud import CloudTools
                    CloudTools(task.workspace_path, self.config).colab_release({})
                except Exception:
                    pass
            self._worker_local.runner = None
            self._worker_local.owner = None
            with self._cancel_lock:
                self._cancellations.pop(task_id, None)

    def _execute_researcher(self, task: TaskRecord) -> TaskRecord:
        role = RoleName.RESEARCHER.value
        context_text = self._context(task)
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
        if task.replan_count or task.feedback_revision or task.review_reason:
            prompt += (f"\nResearch review event: {task.review_reason or 'Repeated validation failures'}. Diagnose the actual cause. "
                       "Preserve verified steps exactly, the requested backend and all existing executable "
                       "acceptance checks; revise the remaining implementation strategy. Do not weaken "
                       "checks or treat missing inputs as success. Attempt/time budgets do not reset.\n")
        result = self._spawn_and_log(task, role, prompt, attempt=task.retry_count + 1)
        if not result.success:
            return self._fail_or_escalate(task, role, result.error or "Researcher failed.")

        try:
            plan = parse_plan(result.output)
            if json.loads(task.plan_json):
                previous = json.loads(task.plan_json)
                if plan.get("backend", "local") != previous.get("backend", "local"):
                    raise ValueError("Replanning cannot change the requested backend")
                if previous.get("mode") == "steps":
                    if (plan.get("mode") != "steps" or len(plan["steps"]) <= task.step_index
                            or plan["steps"][:task.step_index] != previous["steps"][:task.step_index]):
                        raise ValueError("Replanning must preserve the verified prefix and current step")
                    current = plan["steps"][task.step_index]
                    current["checks"] = previous["steps"][task.step_index].get("checks", []) + [
                        c for c in current.get("checks", [])
                        if c not in previous["steps"][task.step_index].get("checks", [])]
                plan["checks"] = previous.get("checks", []) + [
                    c for c in plan.get("checks", []) if c not in previous.get("checks", [])]
        except (ValueError, TypeError) as exc:
            return self._fail_or_escalate(task, role, f"Invalid research plan: {exc}")
        self.db.update_task(task.id, research_notes=result.output, plan_json=json.dumps(plan),
                            plan_revision=task.plan_revision+1,review_reason='')
        if task.workspace_path:
            state = Path(task.workspace_path) / ".workflow"
            state.mkdir(exist_ok=True)
            (state / "research_brief.md").write_text(result.output, encoding="utf-8")
            (state / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
            (state / "plans").mkdir(exist_ok=True)
            revision_name = f'revision_{task.replan_count:02d}' + (f'_feedback_{task.feedback_revision:04d}' if task.feedback_revision else '')
            (state / "plans" / f"{revision_name}.json").write_text(
                json.dumps(plan, indent=2), encoding="utf-8")
            from core.role_memory import atomic_json
            atomic_json(state/'plans'/f'plan_{task.plan_revision+1:04d}.json',
                        {'revision':task.plan_revision+1,'reason':task.review_reason,'plan':plan})
        return self._transition(task.id, TaskStatus.OPERATING, RoleName.OPERATOR.value)

    def _execute_operator(self, task: TaskRecord) -> TaskRecord:
        role = RoleName.OPERATOR.value
        backend=json.loads(task.plan_json).get('backend','local')
        if backend not in {'local','colab','kaggle'}:
            return self.control.wait(task.id,'INPUT','OPERATING',
                f'Compute backend {backend} has no active executor; configure and approve it explicitly. No allocation attempted.',backend)
        if json.loads(task.plan_json).get('backend') == 'colab':
            try:
                from core.cloud import CloudTools
                prepared = CloudTools(task.workspace_path, self.config).prepare_colab_bridge()
                self.db.log_event('COLAB_BACKEND_READY', 'orchestrator',
                    'Host Colab bridge prepared before starting the operator', task_id=task.id, payload=prepared)
            except Exception as exc:
                from core.resource_gate import ResourceBusy
                if isinstance(exc,ResourceBusy):
                    return self.control.wait(task.id,'RESOURCE','OPERATING',str(exc),'colab',delay=30)
                return self._fail_or_escalate(task, role, f'Colab host bridge preparation failed: {exc}')
            self.db.update_task(task.id,wait_deadline=0)
        context_text = self._context(task)
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
            f"User Request and Researcher Notes. If the plan mode is steps, implement only "
            f"the item at step_index={task.step_index}; otherwise execute directly. "
            f"Summarize actions and preserve experiment artifacts and evidence."
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
        review=re.search(r'^RESEARCH_REVIEW: (.+)$',result.output,re.MULTILINE)
        if review and not task.remote_job_id and 'WAIT_FOR_JOB:' not in result.output:
            if task.event_review_count>=int(self.config.get('orchestrator',{}).get('max_event_reviews',2)):
                return self._fail_or_escalate(task,role,'Research event review budget exhausted: '+review[1])
            self.db.update_task(task.id,event_review_count=task.event_review_count+1,review_reason='Operator reported: '+review[1])
            return self._transition(task.id,TaskStatus.RESEARCHING,'researcher')
        marker = re.search(r"^WAIT_FOR_JOB: ([0-9a-f]{32})\s*$", result.output, re.MULTILINE)
        if marker:
            self.db.update_task(task.id, remote_job_id=marker.group(1))
            return self.control.wait(task.id,'REMOTE','WAITING_REMOTE','Remote job executing',marker.group(1),
                delay=float(self.config.get('cloud',{}).get('poll_interval_seconds',15)),
                timeout=float(self.config.get('cloud',{}).get('remote_timeout_seconds',86400)))
        return self._transition(task.id, TaskStatus.VALIDATING, RoleName.VALIDATOR.value)

    def _poll_remote(self, task):
        from core.cloud import CloudTools
        cloud = CloudTools(task.workspace_path, self.config)
        try:
            job = cloud.kaggle_job_status({"job_id": task.remote_job_id})
            if job["state"] == "complete":
                result = cloud.kaggle_export_data({"job_id": task.remote_job_id})
                if result.get("status") != "completed":
                    raise RuntimeError("Remote artifacts were not downloaded")
                self.db.update_task(task.id, operator_output=task.operator_output + "\n" + json.dumps(result), remote_job_id="", wait_deadline=0)
                return self._transition(task.id, TaskStatus.VALIDATING, "validator")
            if job["state"] in {"error", "cancelled", "canceled"}:
                return self._fail_or_escalate(task, "kaggle", json.dumps(job))
        except Exception as exc:
            # Polling is read-only: transient failures need not repeat submission.
            self.db.log_event("REMOTE_POLL_ERROR", "orchestrator", str(exc), task_id=task.id, severity="WARN")
        delay = float(self.config.get("cloud", {}).get("poll_interval_seconds", 15))
        return self.control.wait(task.id,'REMOTE','WAITING_REMOTE','Waiting for remote completion or reliable observation',task.remote_job_id,delay)

    def _execute_validator(self, task: TaskRecord) -> TaskRecord:
        role = RoleName.VALIDATOR.value
        try:
            checks = self._run_checks(task)
        except Exception as exc:
            return self._handle_retry_or_escalate(task, f"Acceptance check error: {exc}")
        self.db.log_event("ACCEPTANCE_CHECKS", "orchestrator", "Recorded executable checks",
                          task_id=task.id, payload={"step": task.step_index, "checks": checks})
        if any(c["exit_code"] != 0 for c in checks):
            return self._handle_retry_or_escalate(task, "Acceptance checks failed:\n" + json.dumps(checks))
        context_text = self._context(task) + "\nHarness check results:\n" + json.dumps(checks)
        prompt = (
            f"Task ID: #{task.id}\n"
            f"Task Title: {task.title}\n"
            f"Retry Count: {task.retry_count}/{task.max_retries}\n\n"
            f"### Accumulated Context Log\n"
            f"{context_text}\n\n"
            f"### Instructions for Validator\n"
            f"Verify the Operator's work against the original user request. "
            f"Use plan revision {task.plan_revision} and applied user feedback revision {task.feedback_revision}. "
            f"Do not introduce new blocking requirements. Separate mandatory failures or missing evidence "
            f"from optional improvements. Previous PASS is not evidence for changed files. "
            f"Your final response MUST begin with exactly one standalone verdict line:\n"
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
            plan = json.loads(task.plan_json)
            steps = plan.get("steps", []) if plan.get("mode") == "steps" else []
            self.db.log_event("ITEM_VERIFIED", "orchestrator", f"Verified item {task.step_index + 1}", task_id=task.id)
            self._checkpoint(task)
            if task.step_index + 1 < len(steps):
                self.db.update_task(task.id, step_index=task.step_index + 1, retry_count=0, validation_feedback="")
                if self.config.get('orchestrator',{}).get('research_review_at_milestones',True):
                    self.db.update_task(task.id,review_reason=f'Milestone {task.step_index+1} independently verified; choose next sequential action')
                    return self._transition(task.id,TaskStatus.RESEARCHING,RoleName.RESEARCHER.value)
                return self._transition(task.id, TaskStatus.OPERATING, RoleName.OPERATOR.value)
            self.db.update_task(task.id, outcome="COMPLETED")
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
        context_text = self._context(task)
        is_escalated = task.outcome != "COMPLETED"
        prompt = (
            f"Task ID: #{task.id}\n"
            f"Target Channel: {task.source_channel} (Channel ID: {task.source_user})\n"
            f"Outcome State: {'ESCALATED (Needs User Attention)' if is_escalated else 'COMPLETED'}\n\n"
            f"### Accumulated Context Log\n"
            f"{context_text}\n\n"
            f"### Instructions for Communicator\n"
            f"Write a clear, concise, user-friendly response suitable for {task.source_channel} "
            f"(Discord / Zalo / Messenger / Console) summarizing what was done, key results, "
            f"and any follow-up needed. Do NOT tag the Channel ID as a user mention."
        )
        # Do not start another login wait merely to explain an auth failure.
        result = None if 'CLI authentication required' in task.validation_feedback else self._spawn_and_log(task, role, prompt)
        final_message = result.output if result and result.success else (
            f"Task #{task.id}: {'ESCALATED' if is_escalated else 'COMPLETED'}\n"
            f"Workspace: {task.workspace_path}\n"
            f"{task.validation_feedback if is_escalated else task.operator_output[:1500]}"
        )

        final_state = TaskStatus.ESCALATED if is_escalated else TaskStatus.COMPLETED
        final_message = f"Task #{task.id}: {final_state.value}\nWorkspace: {task.workspace_path}\n\n{final_message}"
        updated = self.db.finalize_task(task.id, final_state.value, final_message, expected_feedback_revision=task.feedback_revision)
        if updated.status not in {'COMPLETED', 'ESCALATED', 'FAILED'}:
            return updated
        self._context(updated)
        from core.reports import write_task_summary, write_board
        try:
            write_task_summary(self.db, updated)
            write_board(self.db, self.db.db_path.parent / 'TASKS.md')
        except (OSError, ValueError) as exc:
            # A human-readable index must not overturn validated execution or
            # replace the durable outbox result already committed above.
            self.db.log_event('TASK_REPORT_WRITE_FAILED', 'orchestrator', str(exc), task_id=task.id, severity='WARN')
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

    def _context(self, task):
        # Persist complete handoffs; keep repeated transcript text out of the prompt.
        # Original request is always preserved, even after many retries/milestones.
        state = Path(task.workspace_path or self.workspace_root) / ".workflow"
        state.mkdir(parents=True, exist_ok=True)
        handoff = {"request": task.user_prompt, "research": task.research_notes,
                   "feedback_revision": task.feedback_revision,
                   "plan_revision":task.plan_revision,
                   "user_feedback": self.conversation.feedback(task.id, applied_only=True),
                   "pending_user_feedback": [item for item in self.conversation.feedback(task.id) if not item.get("applied")],
                   "plan": json.loads(task.plan_json), "step_index": task.step_index,
                   "operator_output": task.operator_output, "feedback": task.validation_feedback,
                   "context_log": task.context_log_path}
        from core.role_memory import atomic_json
        atomic_json(state / 'handoff.json', handoff)
        from core.control import digest
        atomic_json(state/'state.json',{
            'controller_facts':{'task_id':task.id,'status':task.status,'step_index':task.step_index,
                'observed_at':utc_now_iso(),
                'plan_revision':task.plan_revision,'feedback_revision':task.feedback_revision,
                'acceptance_plan_sha256':digest(json.loads(task.plan_json)),
                'attempts_used_current_step':task.retry_count,'remote_job_id':task.remote_job_id,
                'next_action':task.review_reason or task.status,
                'evidence_paths':['.workflow/progress.json','.workflow/timeline.json',task.context_log_path]},
            'model_notes_are_unverified':{'research':'.workflow/research_brief.md','role_history':'.workflow/roles/'},
            'full_context':'.workflow/handoff.json'})
        return (f"Task workspace: {task.workspace_path}\nRequest received at (UTC): {task.created_at}\n"
                f"Worker claimed at (Unix seconds): {task.started_at}\nCurrent role started at (UTC): "
                f"{utc_now_iso()}\n"
                f"Full durable handoff: .workflow/handoff.json; full log: {task.context_log_path}\n"
                f"Explicit inputs in inputs/: {', '.join(i['name'] for i in json.loads(task.inputs_json)) or 'none'}\n"
                f"Original request:\n{task.user_prompt}\nResearch brief:\n{task.research_notes[:12000]}\n"
                f"User feedback revision {task.feedback_revision} (only applied user_feedback is active for this revision; any pending_user_feedback in .workflow/handoff.json is queued for the next role boundary and not yet active): {json.dumps(handoff['user_feedback'])[-12000:]}\n"
                f"Plan: {task.plan_json}\nCurrent step index: {task.step_index}\n"
                f"Latest operator output:\n{task.operator_output[-8000:]}\n"
                f"Latest feedback:\n{task.validation_feedback[-8000:]}\n")

    def _run_checks(self, task):
        plan = json.loads(task.plan_json)
        steps = plan.get("steps", []) if plan.get("mode") == "steps" else []
        checks = steps[task.step_index].get("checks", []) if steps else []
        if not steps or task.step_index == len(steps) - 1:
            checks = checks + plan.get("checks", [])
        results = []
        for check in checks:
            argv = [sys.executable if arg == "{python}" else arg for arg in check["argv"]]
            result = run_process(argv, Path(task.workspace_path or self.workspace_root),
                timeout=min(300, max(1, int(check.get("timeout_seconds", 120)))),
                env={**os.environ, "WORKFLOW_ACCEPTANCE_TASK_ID": str(task.id)},
                cancel_event=getattr(self._worker_local, "cancelled", None))
            results.append({"argv": check["argv"], **result})
        return results

    def _checkpoint(self, task):
        if not task.workspace_path:
            return
        workspace = Path(task.workspace_path)
        state = workspace / ".workflow"
        state.mkdir(exist_ok=True)
        from core.role_memory import atomic_json
        atomic_json(state/'progress.json',{'verified_step':task.step_index,'plan_revision':task.plan_revision,
            'feedback_revision':task.feedback_revision,'validation':self.db.get_task(task.id).validation_feedback})
        if not (workspace / ".git").is_dir():
            return
        for argv in (["git", "add", "--all"],
                     ["git", "-c", "user.name=Workflow", "-c", "user.email=workflow@localhost",
                      "-c", "core.hooksPath=" + str(state / "empty-hooks"),
                      "commit", "--allow-empty", "-qm", f"Verified task {task.id} item {task.step_index + 1}"]):
            result = run_process(argv, workspace, timeout=120,
                                 cancel_event=getattr(self._worker_local, "cancelled", None))
            if result["exit_code"]:
                raise RuntimeError("Verified item could not be checkpointed: " + result["stderr"])

    def _spawn_and_log(
        self,
        task: TaskRecord,
        role: str,
        prompt: str,
        attempt: int = 1,
    ):
        role_cfg = self.config.get("roles", {}).get(role, {})
        selected_provider = task.provider_override or self.provider_override
        provider_name = selected_provider or role_cfg.get("provider", "agy_headless_safe")
        step = task.step_index if role in {"operator", "validator"} else -1
        label = f"task-{task.id:05d}/" + (f"step-{step + 1:02d}/" if step >= 0 else "") + f"{role}/attempt-{attempt:02d}"
        cli_log_path = self.log_manager.create_agent_cli_log_path(task.id, role, step, attempt)

        run_id = self.db.record_agent_spawn(
            role=role,
            provider_name=provider_name,
            task_id=task.id,
            cli_log_path=str(cli_log_path),
            step_index=step, attempt=attempt, run_label=label,
        )
        if task.workspace_path:
            (Path(task.workspace_path) / ".workflow").mkdir(parents=True, exist_ok=True)
            timeline = self.db.task_timeline(task.id)
            if task.parent_task_id:
                timeline["previous_run"] = self.db.task_timeline(task.parent_task_id)
            (Path(task.workspace_path) / ".workflow/timeline.json").write_text(json.dumps(timeline, indent=2), encoding="utf-8")
            prompt += "\nController-recorded timestamps are in .workflow/timeline.json. Do not invent future role timestamps.\n"
        active_runner = getattr(self._worker_local, "runner", None) or self.runner
        if role=='communicator' and task.source_channel=='discord' and hasattr(self.runner,'for_workspace'):
            with self.db._connect() as c:
                owner=c.execute('SELECT actor FROM task_actors WHERE task_id=?',(task.id,)).fetchone()
            if owner:
                shared,_=self._communicator_workspace(task.source_user,owner['actor'])
                active_runner=self.runner.for_workspace(shared)
                active_runner.cancel_event=getattr(self._worker_local,'cancelled',None)
        active_runner.run_label = label
        active_runner.control_environment={'WORKFLOW_DB_PATH':str(self.db.db_path.resolve()),'WORKFLOW_TASK_ID':str(task.id)}
        with self.db._connect() as c:
            approved=[dict(r) for r in c.execute("SELECT id,action FROM approvals WHERE task_id=? AND decision='APPROVED' AND expires>?",(task.id,time.time()))]
        if approved:
            prompt+='\nApproved exact actions (recheck inputs; changed actions require a new decision):\n'+json.dumps(approved)
        try:
            result = active_runner.run_role(
                role=role, task_prompt=prompt, cli_log_path=cli_log_path,
                provider_override=selected_provider,
            )
        except Exception as exc:
            from core.models import AgentRunResult
            result = AgentRunResult(role, provider_name, False, "", exit_code=1, error=str(exc))
        self.db.record_agent_shutdown(
            run_id=run_id,
            role=role,
            task_id=task.id,
            success=result.success,
            duration_seconds=result.duration_seconds,
            exit_code=result.exit_code,
            output_preview=result.output,
            error_text=result.error,
            session_id=result.structured_data.get("session_id", ""),
        )
        if task.workspace_path:
            timeline = self.db.task_timeline(task.id)
            if task.parent_task_id:
                timeline["previous_run"] = self.db.task_timeline(task.parent_task_id)
            (Path(task.workspace_path) / ".workflow/timeline.json").write_text(json.dumps(timeline, indent=2), encoding="utf-8")
        self.log_manager.append_agent_step(
            task_log_path=task.context_log_path,
            result=result,
            attempt=attempt,
        )
        owner = getattr(self._worker_local, "owner", None)
        if owner and self.db.get_task(task.id).lease_owner != owner:
            raise RuntimeError("Lease lost while executing agent; refusing stale state update")
        cancelled = getattr(self._worker_local, "cancelled", None)
        if cancelled is not None and cancelled.is_set():
            raise InterruptedError("Task cancelled during agent execution")
        with self.db._connect() as c:
            pending=c.execute("SELECT * FROM approvals WHERE task_id=? AND (decision IN ('PENDING','DENIED') OR (decision='APPROVED' AND expires<=?)) ORDER BY created DESC LIMIT 1",(task.id,time.time())).fetchone()
            unused=c.execute("SELECT 1 FROM approvals WHERE task_id=? AND decision='APPROVED' LIMIT 1",(task.id,)).fetchone()
            consumed=c.execute("SELECT 1 FROM approvals WHERE task_id=? AND decision='CONSUMED' LIMIT 1",(task.id,)).fetchone()
        if pending:
            from core.approvals import TaskDeferred
            self.control.wait(task.id,'APPROVAL',task.status,'Exact action needs owner decision',pending['id'],timeout=7*86400)
            raise TaskDeferred()
        if unused or (consumed and not result.success):
            from core.approvals import TaskDeferred
            self.control.wait(task.id,'INPUT',task.status,'Approved action unused or its result uncertain; reconcile before retry',timeout=7*86400)
            raise TaskDeferred()
        if result.structured_data.get('retry_safe') and result.structured_data.get('retry_after'):
            from core.approvals import TaskDeferred
            delay=float(result.structured_data['retry_after'])
            self.control.cool_down(provider_name,delay,'Rate limited before tools')
            self.control.wait(task.id,'PROVIDER',task.status,'Provider rate limited before tools',provider_name,delay)
            raise TaskDeferred()
        if result.success:
            self.db.update_task(task.id,wait_deadline=0)
        return result

    def _handle_retry_or_escalate(self, task: TaskRecord, reason: str) -> TaskRecord:
        next_retry = task.retry_count + 1
        self.db.update_task(
            task.id,
            retry_count=next_retry,
            validation_feedback=reason,
            outcome="ESCALATED",
        )
        if next_retry < task.max_retries:
            options = self.config.get("orchestrator", {})
            replan_after = int(options.get("replan_after_failures", 2))
            if (replan_after > 0 and next_retry >= replan_after
                    and task.replan_count < int(options.get("max_replans", 1))
                    and not task.remote_job_id):
                self.db.update_task(task.id, replan_count=task.replan_count + 1,review_reason='Repeated validation failures')
                self.db.log_event("TASK_REPLAN_SCHEDULED", "orchestrator",
                    "Repeated failure routed to researcher within the remaining attempt budget",
                    task_id=task.id, payload={"attempts_used": next_retry, "reason": reason[:1000]})
                return self._transition(task.id, TaskStatus.RESEARCHING, RoleName.RESEARCHER.value)
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
            outcome="ESCALATED",
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
        """Accept one explicit PASS with evidence, allowing CLI progress before it.

        Some text-mode CLIs concatenate progress updates and the final response.
        Count every verdict mention to reject quotes/conflicts, then require an
        exact standalone verdict and evidence after it (not merely a preamble).
        """
        if not isinstance(output, str):
            return False
        lines = re.findall(r"\bVERDICT\s*:\s*(PASS|FAIL)\b", output, re.IGNORECASE)
        verdict = re.search(r"(?m)^VERDICT: PASS[ \t]*\r?$", output)
        fence = None
        for line in output[:verdict.start() if verdict else 0].splitlines():
            marker = re.match(r"^\s*(`{3,}|~{3,})", line)
            if marker:
                token = marker.group(1)
                if fence is None:
                    fence = token
                elif token[0] == fence[0] and len(token) >= len(fence):
                    fence = None
        return (len(lines) == 1 and lines[0] == "PASS" and verdict is not None
                and fence is None and bool(output[verdict.end():].strip()))
