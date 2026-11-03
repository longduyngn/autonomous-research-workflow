"""Lifecycle hooks for seamless Google Colab & Kaggle execution in Antigravity."""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _load_env():
    if os.environ.get("WORKFLOW_NO_ENV") == "1" or "unittest" in sys.modules:
        return
    for env_file in [ROOT / ".env", ROOT / "config/.env"]:
        if env_file.exists():
            for raw in env_file.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _is_port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _ensure_colab_bridge(port: int = 8765) -> bool:
    if _is_port_open(port):
        return True
    if os.environ.get('WORKFLOW_ROLE'):
        # Managed agents must use the service prepared by the parent controller.
        # A child started here can inherit the terminal sandbox's network token.
        return False
    _load_env()
    if not os.environ.get("COLAB_BRIDGE_TOKEN"):
        return False
    py_exe = sys.executable
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
    try:
        subprocess.Popen(
            [py_exe, str(ROOT / "main.py"), "colab-bridge", "--port", str(port)],
            cwd=str(ROOT),
            env=dict(os.environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        for _ in range(10):
            if _is_port_open(port):
                return True
            time.sleep(0.15)
    except OSError:
        pass
    return _is_port_open(port)


def handle_session_start(payload: dict) -> dict:
    _load_env()
    cdp_open = _is_port_open(9222)
    bridge_open = _is_port_open(8765)
    msg = (
        f"[Cloud Workflow Ready] Coc Coc CDP (:9222)={'OPEN' if cdp_open else 'CLOSED'} | "
        f"Colab Bridge (:8765)={'RUNNING' if bridge_open else 'AUTO-START ON DEMAND'}. "
        f"Use `.conda\\python.exe .agents/skills/colab-coccoc-playwright/scripts/colab_playwright.py` "
        f"or `colab_connect` / `colab_call` for zero-click Colab & `.agents/skills/kaggle-playwright-cancel/scripts/kaggle_cancel.py` for Kaggle."
    )
    return {
        "injectSteps": [
            {
                "systemMessage": {
                    "systemMessage": msg
                }
            }
        ]
    }


def handle_pre_tool_use(payload: dict) -> dict:
    tool_call = payload.get("toolCall") or {}
    name = tool_call.get("name", "")
    args = tool_call.get("args") or {}

    if name == "call_mcp_tool" or name.startswith("mcp_ck_nb_"):
        tool_name = args.get("ToolName", "") or name
        if "colab" in tool_name:
            _ensure_colab_bridge(8765)
        return {"decision": "allow"}

    if name == "run_command":
        cmd = (args.get("CommandLine") or "").replace("\\", "/")
        bypass = bool(args.get("BypassSandbox", False))
        sanctioned = [
            "scripts/cloud.py",
            "colab-coccoc-playwright/scripts/colab_playwright.py",
            "kaggle-playwright-cancel/scripts/kaggle_cancel.py",
            "main.py colab-bridge",
        ]
        if any(s in cmd for s in sanctioned):
            if "colab" in cmd or "cloud.py" in cmd:
                _ensure_colab_bridge(8765)
            return {
                "decision": "allow",
                "reason": "Sanctioned Colab/Kaggle workflow command.",
            }
        if not bypass:
            return {"decision": "allow"}
        return {"decision": "ask"}

    return {"decision": "allow"}


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else "PreToolUse"
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    if event == "SessionStart":
        out = handle_session_start(payload)
    elif event == "PreToolUse":
        out = handle_pre_tool_use(payload)
    else:
        out = {}

    print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
