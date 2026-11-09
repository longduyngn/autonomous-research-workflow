"""Stdio MCP entry point for task-scoped notebook and cloud tools."""
import importlib
import json
import os
import sys
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CONTROLLER))
import core.cloud
from main import load_dotenv

WORKSPACE_ROOT = Path(os.environ.get("WORKFLOW_WORKSPACE_ROOT", str(CONTROLLER))).resolve()


def handle_rpc_request(msg):
    request_id = msg.get("id")
    if request_id is None:
        return None
    if os.environ.get("WORKFLOW_NO_ENV") != "1" and "unittest" not in sys.modules:
        load_dotenv(CONTROLLER / ".env")
        load_dotenv(CONTROLLER / "config/.env")
    importlib.reload(core.cloud)
    method, params = msg.get("method"), msg.get("params") or {}
    cfg = json.loads((CONTROLLER / "config/workflow_config.json").read_text(encoding="utf-8"))
    tools = core.cloud.CloudTools(WORKSPACE_ROOT, cfg)
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "research-notebook-cloud", "version": "2.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tools.schemas()}
    elif method == "tools/call":
        try:
            name = params["name"]
            if os.environ.get("WORKFLOW_ROLE", "operator") not in {"operator", "validator"} and name not in {'notebook_inspect','compute_capabilities','agent_checkin','experiment_job_status'}:
                raise PermissionError("This role can only inspect notebook artifacts")
            value = tools.call(name, params.get("arguments", {}))
            failed = bool(value.get("error") or value.get("status") in {"error", "missing_credentials"}
                          or value.get("state") == "submission_unknown" or value.get("isError"))
            result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": failed}
        except Exception as exc:
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Unknown method"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    if os.environ.get("WORKFLOW_NO_ENV") != "1" and "unittest" not in sys.modules:
        load_dotenv(CONTROLLER / ".env")
        load_dotenv(CONTROLLER / "config/.env")
    for line in sys.stdin:
        try:
            result = handle_rpc_request(json.loads(line))
            if result is not None:
                print(json.dumps(result, ensure_ascii=False), flush=True)
        except ValueError:
            print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid JSON"}}), flush=True)


if __name__ == "__main__":
    main()
