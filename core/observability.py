"""Read-only task/run views shared by the CLI and local dashboard."""
import json
import time
from pathlib import Path

TERMINAL = {"COMPLETED", "FAILED", "ESCALATED"}


def task_summary(task):
    try:
        plan = json.loads(task.plan_json)
    except ValueError:
        plan = {}
    steps = plan.get("steps", []) if plan.get("mode") == "steps" else []
    total = len(steps) or 1
    verified = total if task.status == "COMPLETED" or task.outcome == "COMPLETED" else min(task.step_index, total)
    return {"id": task.id, "title": task.title, "status": task.status,
            "role": task.current_role, "step_index": task.step_index,
            "step_title": steps[task.step_index].get("title", "") if steps and task.step_index < len(steps) else "Direct task",
            "verified_steps": verified, "total_steps": total,
            "attempt": min(task.retry_count + 1, task.max_retries), "max_attempts": task.max_retries,
            "replans": task.replan_count, "parent_task_id": task.parent_task_id,
            "plan_revision":task.plan_revision,"feedback_revision":task.feedback_revision,
            "waiting_seconds":task.waiting_seconds,"wait_deadline":task.wait_deadline,
            "provider_override": task.provider_override, "workspace": task.workspace_path,
            "created_at": task.created_at, "updated_at": task.updated_at,
            "heartbeat_age_seconds": round(max(0, time.time() - task.heartbeat_at), 1) if task.heartbeat_at else None,
            "worker_owned": bool(task.lease_owner), "remote_job_id": task.remote_job_id}


def task_list(db, limit=200):
    with db._connect() as connection:
        counts = {row[0]: row[1] for row in connection.execute("SELECT status,COUNT(*) FROM tasks GROUP BY status")}
    return {"tasks": [task_summary(t) for t in db.get_all_tasks(limit)], "counts": counts,
            "observed_at_unix": time.time()}


def task_detail(db, task_id):
    task = db.get_task(task_id)
    if task is None:
        raise ValueError("Task not found")
    with db._connect() as connection:
        tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        wait=connection.execute('SELECT * FROM task_waits WHERE task_id=?',(task_id,)).fetchone()
        approvals=[dict(r) for r in connection.execute('SELECT id,actor,action,action_hash,expires,decision FROM approvals WHERE task_id=? ORDER BY created',(task_id,))] if 'approvals' in tables else []
        feedback=[dict(r) for r in connection.execute('SELECT id,actor,message,applied FROM task_feedback WHERE task_id=? ORDER BY id',(task_id,))] if 'task_feedback' in tables else []
        runs = [dict(row) for row in connection.execute(
            "SELECT id,role,provider_name,status,spawned_at,shutdown_at,duration_seconds,exit_code,"
            "step_index,attempt,run_label,session_id,error_text FROM agent_runs WHERE task_id=? ORDER BY id", (task_id,))]
    for run in runs:
        if not run["run_label"]:
            run["run_label"] = f"task-{task.id:05d}/{run['role']}/legacy-run-{run['id']}"
    events = list(reversed(db.get_recent_events(limit=200, task_id=task_id)))
    checks = []
    for event in events:
        if event["event_type"] == "ACCEPTANCE_CHECKS":
            payload = json.loads(event["payload_json"])
            checks.append({"timestamp": event["timestamp"], **payload})
    return {**task_summary(task), "request": task.user_prompt, "research": task.research_notes,
            "plan": json.loads(task.plan_json), "validation": task.validation_feedback,
            "result": task.final_response, "runs": runs, "checks": checks,
            "wait":dict(wait) if wait else None,"approvals":approvals,"user_feedback":feedback,
            "events": [{k: e[k] for k in ("timestamp", "event_type", "summary", "severity")} for e in events]}


def run_log(db, root, run_id, kind="stdout", limit=64000):
    suffixes = {"stdout": ".stdout.log", "stderr": ".stderr.log", "prompt": ".prompt.md",
                "result": ".result.json", "final": ".final.txt", "native": None}
    if kind not in suffixes:
        raise ValueError("Unknown transcript kind")
    with db._connect() as connection:
        row = connection.execute("SELECT cli_log_path FROM agent_runs WHERE id=?", (run_id,)).fetchone()
    if row is None or not row[0]:
        raise ValueError("Run log not found")
    path = Path(row[0])
    path = (path.with_suffix(suffixes[kind]) if suffixes[kind] else path).resolve()
    if (not path.is_relative_to(Path(root).resolve()) or not path.is_file()
            or any(p.startswith(".env") or p.endswith(".env") for p in path.parts)):
        raise ValueError("Run log unavailable")
    with path.open("rb") as stream:
        size = stream.seek(0, 2)
        stream.seek(max(0, size - limit))
        text = stream.read(limit).decode("utf-8", errors="replace")
    return {"text": text, "truncated": size > limit, "bytes": size, "kind": kind}
