"""Portable hook protocol adapter. Native permissions still decide allowed calls."""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CONTROLLER))


def context(workspace):
    manifest = json.loads((Path(workspace) / ".workflow/integration.json").read_text(encoding="utf-8"))
    return (f"Workflow workspace: {workspace}. Python: {sys.executable}. "
            f"Available task skills: {', '.join(manifest['skills'])}. "
            "Read the appropriate SKILL.md before use. Keep artifacts here; inspect actual outputs before PASS.")


def handle(provider, event, payload, workspace):
    if not isinstance(payload, dict):
        raise ValueError("Hook input must be an object")
    tool = payload.get("tool_name", "")
    args = payload.get("tool_input", {})
    if provider == "agy":
        call = payload.get("toolCall", {})
        if not isinstance(call, dict):
            raise ValueError("Malformed Antigravity tool call")
        tool, args = call.get("name", ""), call.get("args", {})
    if not isinstance(args, dict):
        # apply_patch can report a free-form patch; do not reinterpret it as shell.
        args = {"patch": str(args)}
    command = args.get("CommandLine", args.get("command", args.get("cmd", "")))
    denied, reason = False, ""
    if event=='PreToolUse':
        from core.control import check_worker_lease
        try: check_worker_lease(os.environ)
        except (PermissionError,KeyError,ValueError):
            denied,reason=True,'Worker lease expired or changed; refusing stale tool effects'
    if not denied and event=='PreToolUse' and os.environ.get('WORKFLOW_DIAGNOSTIC_READONLY')=='1':
        allowed={'Read','read_file','view_file','list_dir','list_files','Glob','Grep','grep_search','find_by_name'}
        if tool not in allowed:
            denied,reason=True,'Diagnostic consultations are read-only; command/cloud/write tools are unavailable'
        else:
            from core.tools import AgentTools
            paths=AgentTools(workspace,'workspace_watcher')
            try:
                selected=[args[key] for key in ('path','Path','file_path','AbsolutePath','FilePath','DirectoryPath','SearchDirectory','TargetFile')
                          if args.get(key)]
                if not selected or any(not isinstance(value,str) for value in selected):
                    raise PermissionError('Diagnostic reads require an explicit scoped path')
                for value in selected:paths.path(value)
            except (ValueError,PermissionError):
                denied,reason=True,'Diagnostic read must remain in its isolated facts workspace'
    if not denied and event == "PreToolUse" and tool in {"Bash", "run_command", "exec_command", "shell", "shell_command"}:
        if not isinstance(command, str) or not command.strip():
            denied, reason = True, "Missing shell command"
        else:
            from scripts.pre_tool_approval_hook import is_dangerous_command
            denied, reason = is_dangerous_command(command)
            if denied and os.environ.get('WORKFLOW_DB_PATH') and os.environ.get('WORKFLOW_TASK_ID'):
                from core.approvals import command_decision
                allowed,reason=command_decision(os.environ['WORKFLOW_DB_PATH'],int(os.environ['WORKFLOW_TASK_ID']),workspace,tool,args)
                denied=not allowed
            if not denied and ("colab" in command or "scripts/cloud.py" in command):
                # Existing on-demand bridge startup, normalized across CLI protocols.
                from scripts.cloud_hooks import handle_pre_tool_use
                handle_pre_tool_use({"toolCall": {"name": "run_command", "args": {"CommandLine": command}}})
    state = Path(workspace) / ".workflow"
    state.mkdir(parents=True, exist_ok=True)
    record = {"provider": provider, "event": event, "role": os.environ.get("WORKFLOW_ROLE", ""),
        "run_id": os.environ.get("WORKFLOW_RUN_ID", ""), "tool": tool, "time": time.time(),
        "decision": "deny" if denied else "observe", "command_sha256": hashlib.sha256(str(command).encode()).hexdigest()}
    if tool == "call_mcp_tool":
        record["mcp_tool"] = str(args.get("ToolName", args.get("tool_name", args.get("name", ""))))
    with (state / "hook-events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record) + "\n")
    if provider == "agy":
        if event == "SessionStart":
            return {"injectSteps": [{"systemMessage": {"systemMessage": context(workspace)}}]}
        # Antigravity's protocol requires an explicit allow; an empty decision
        # is interpreted as a denial even for read_file and other harmless tools.
        if event == "PreToolUse":
            return {"decision": "deny", "reason": reason} if denied else {"decision": "allow"}
        return {}
    if event == "SessionStart":
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context(workspace)}}
    if denied:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}
    if event=='PreToolUse' and (state/'checkin-request.json').exists():
        return {'hookSpecificOutput':{'hookEventName':event,'additionalContext':
            'Supervisor requests a factual check-in. Read .workflow/checkin-request.json and answer its nonce through agent_checkin. Do not restart a running job.'}}
    return {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["codex", "claude", "agy"], required=True)
    parser.add_argument("--event", required=True)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        print(json.dumps(handle(args.provider, args.event, payload, args.workspace)), flush=True)
    except Exception as exc:
        print(f"Workflow hook failed: {exc}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
