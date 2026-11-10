"""CLI cloud tools shared by all coding-agent providers."""
import argparse
import json
import os
import sys
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CONTROLLER))
from core.cloud import CloudTools
from main import load_dotenv


def main():
    load_dotenv(CONTROLLER / ".env")
    load_dotenv(CONTROLLER / "config/.env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=os.environ.get("WORKFLOW_WORKSPACE_ROOT", "."))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    call = sub.add_parser("call")
    call.add_argument("tool")
    call.add_argument("--args", default="{}")
    snippet = sub.add_parser('colab_run_snippet')
    snippet.add_argument('--code', required=True)
    snippet.add_argument('--hardware', default='T4')
    snippet.add_argument('--timeout-seconds', type=int, default=300)
    args = parser.parse_args()
    cfg = json.loads((CONTROLLER / "config/workflow_config.json").read_text(encoding="utf-8"))
    tools = CloudTools(args.workspace, cfg)
    try:
        tool = 'colab_run_snippet' if args.command == 'colab_run_snippet' else getattr(args, 'tool', '')
        if os.environ.get("WORKFLOW_ROLE") in {"researcher", "communicator", "guardian", "world_watcher", "workspace_watcher"} and args.command != 'list' and tool not in {'notebook_inspect','agent_checkin','experiment_job_status'}:
            raise PermissionError("This role may only inspect cloud artifacts")
        result = (tools.schemas() if args.command == 'list' else
                  tools.call('colab_run_snippet', {'code': args.code, 'hardware': args.hardware,
                      'timeout_seconds': args.timeout_seconds}) if args.command == 'colab_run_snippet' else
                  tools.call(args.tool, json.loads(args.args)))
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if isinstance(result, dict) and (result.get("error") or result.get("status") == "error" or result.get("isError")):
            sys.exit(1)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
