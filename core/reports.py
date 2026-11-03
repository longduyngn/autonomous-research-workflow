"""Small human-readable views over the authoritative task database."""
import json
import uuid
from pathlib import Path


def write_task_summary(db, task):
    if not task.workspace_path:
        return
    workspace = Path(task.workspace_path)
    timeline = db.task_timeline(task.id)
    if task.parent_task_id:
        timeline['previous_run'] = db.task_timeline(task.parent_task_id)
    state = workspace / ".workflow"
    state.mkdir(exist_ok=True)
    (state / "timeline.json").write_text(json.dumps(timeline, indent=2), encoding="utf-8")
    rows = [f"| {r['role']} | {r['status']} | {r['spawned_at']} | {r['shutdown_at'] or 'running'} |" for r in timeline["roles"]]
    text = (f"# Task {task.id}: {task.status}\n\n{task.title}\n\n"
        f"Received (UTC): {task.created_at}\n\nPrevious task: {task.parent_task_id or 'none'}\n\n"
        "| Role | State | Started UTC | Finished UTC |\n|---|---|---|---|\n" + "\n".join(rows)
        + f"\n\n## Result\n\n{task.final_response}\n\n## Validation evidence\n\n{task.validation_feedback}\n"
        + f"\nFull role logs: {task.context_log_path}\n")
    (workspace / "TASK_RESULT.md").write_text(text, encoding="utf-8")


def write_board(db, destination):
    rows = []
    for task in db.get_all_tasks(limit=100):
        title = task.title.replace('|', '/').replace('\n', ' ')
        location = f"[{task.id}]({Path(task.workspace_path).as_posix()}/TASK_RESULT.md)" if task.workspace_path else str(task.id)
        rows.append(f"| {location} | {task.status} | {task.parent_task_id or '-'} | {title} |")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text("# Workflow tasks\n\nGenerated from storage/events.db. Old failures remain historical; a recovery has its own task ID.\n\n"
        "| Task | State | Recovery of | Request |\n|---|---|---|---|\n" + '\n'.join(rows) + '\n', encoding='utf-8')
    temporary.replace(destination)
