"""Additional Antigravity command policy with request-specific Discord approval.

Restricted command patterns require an allowed user's correlated reply. Unknown
or malformed payloads are denied. Other commands still rely on native CLI sandbox
and permissions; a regex classifier is not a security boundary for arbitrary code.
"""

import json
import os
import re
import sys
import time
import uuid
import urllib.request
from pathlib import Path
from typing import Any, Dict, Tuple

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent

# Commands matching these regexes require explicit human approval via Discord in headless mode
DANGEROUS_PATTERNS = [
    r"\bRemove-Item\b",
    r"\b(rm|del|erase|rd|rmdir)\b",
    r"\bdel\s+/[fqs]",
    r"\brmdir\b",
    r"\bFormat-Volume\b",
    r"\bStop-Process\b",
    r"\btaskkill\b",
    r"\bgit\s+push\s+.*--force\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-fd\b",
    r"\bshutdown\b",
    r"\brestart-computer\b",
]


def load_env_vars() -> Tuple[str, str]:
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    channel_id = os.environ.get("DISCORD_CHANNEL_ID", "").strip()
    env_file = WORKSPACE_ROOT / ".env"
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("DISCORD_BOT_TOKEN=") and not token:
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("DISCORD_CHANNEL_ID=") and not channel_id:
                channel_id = line.split("=", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("DISCORD_ALLOWED_USER_IDS="):
                os.environ.setdefault("DISCORD_ALLOWED_USER_IDS", line.split("=", 1)[1].strip().strip('"').strip("'"))
    return token, channel_id or (
        __import__("gateways.discord_gateway", fromlist=["discover_playground_channels"])
        .discover_playground_channels(token)
        .get("approval_list", "")
        if token else ""
    )


def is_dangerous_command(cmd_line: str) -> Tuple[bool, str]:
    # Literal read/search command chains may contain policy words as DATA.
    # Keep this narrow: no expansion, expressions, redirection, pipes or invokes.
    # Executable code (python -c, scripts, etc.) still uses the full classifier.
    if not re.search(r"[$`&|<>\r\n(){}]", cmd_line):
        parts = [part.strip() for part in cmd_line.split(";")]
        if parts and all(re.match(r"^(?:Get-Content|rg)\s+", part, re.I) for part in parts):
            return False, ""
    # This workflow flag observes Colab state; it is not the OS shutdown command.
    classified = cmd_line.replace('--verify-shutdown', '--verify-stop-state')
    for pat in DANGEROUS_PATTERNS:
        if re.search(pat, classified, re.IGNORECASE):
            return True, f"Matched restricted pattern `{pat}`"
    return False, ""


def send_discord_msg(token: str, channel_id: str, content: str) -> str:
    """Send a message to the Discord channel and return the created message ID."""
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    req = urllib.request.Request(
        url,
        data=json.dumps({"content": content[:1990]}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bot {token}",
            "User-Agent": "AntigravityApprovalGate/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return str(data.get("id", ""))


def poll_discord_decision(token: str, channel_id: str, after_msg_id: str, timeout_sec: int = 85, request_id: str = "") -> Tuple[str, str]:
    """Poll the Discord channel for a human reply (`approve` / `!approve` or `deny` / `!deny`)."""
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages?after={after_msg_id}&limit=10"
    headers = {
        "Authorization": f"Bot {token}",
        "User-Agent": "AntigravityApprovalGate/1.0",
    }
    allowed_ids = {x.strip() for x in os.environ.get("DISCORD_ALLOWED_USER_IDS", "").split(",") if x.strip()}
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        time.sleep(3.0)
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                messages = json.loads(resp.read().decode("utf-8"))
                for msg in reversed(messages):
                    author = msg.get("author", {})
                    if author.get("bot") or str(author.get("id")) not in allowed_ids:
                        continue
                    text = re.sub(r"<@!?\d+>", "", str(msg.get("content", ""))).strip().lower()
                    parts = text.split()
                    if len(parts) != 2 or parts[1] != request_id:
                        continue
                    text = parts[0]
                    user_name = author.get("username", "user")
                    if text in ("approve", "!approve", "yes", "y", "allow", "!allow"):
                        send_discord_msg(
                            token,
                            channel_id,
                            f"✅ **Approved by @{user_name}** — resuming headless agent execution.",
                        )
                        return "allow", f"Approved by {user_name} on Discord"
                    if text in ("deny", "!deny", "no", "n", "reject", "!reject"):
                        send_discord_msg(
                            token,
                            channel_id,
                            f"🛑 **Denied by @{user_name}** — blocking command execution.",
                        )
                        return "deny", f"Denied by {user_name} on Discord"
        except Exception:
            continue

    try:
        send_discord_msg(
            token,
            channel_id,
            "⏱️ **Approval Timed Out (85s)** — command was automatically denied for safety.",
        )
    except Exception:
        pass
    return "deny", "Human approval timed out on Discord."


def main() -> None:
    try:
        raw_input = sys.stdin.read()
        payload: Dict[str, Any] = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        sys.stdout.write(json.dumps({"decision": "deny", "reason": "Malformed or unsupported hook payload"}) + "\n")
        return

    if not isinstance(payload, dict) or not isinstance(payload.get("toolCall"), dict):
        sys.stdout.write(json.dumps({"decision": "deny", "reason": "Malformed hook payload"}) + "\n")
        return
    tool_call = payload.get("toolCall", {})
    tool_name = tool_call.get("name", "")
    tool_args = tool_call.get("args", {})
    if not isinstance(tool_args, dict):
        sys.stdout.write(json.dumps({"decision": "deny", "reason": "Malformed tool arguments"}) + "\n")
        return
    cmd_line = str(tool_args.get("CommandLine", ""))

    if tool_name != "run_command" or not cmd_line:
        sys.stdout.write(json.dumps({"decision": "deny", "reason": "Malformed or unsupported hook payload"}) + "\n")
        return

    dangerous, reason = is_dangerous_command(cmd_line)
    if not dangerous:
        # Non-dangerous command: auto-authorize in headless mode!
        sys.stdout.write(
            json.dumps(
                {
                    "decision": "allow",
                    "reason": "Allowed by workspace command policy; native CLI permissions still apply.",
                }
            )
            + "\n"
        )
        return

    # Dangerous command detected: request human approval on Discord!
    token, channel_id = load_env_vars()
    if not token or not channel_id:
        sys.stdout.write(
            json.dumps(
                {
                    "decision": "deny",
                    "reason": f"Blocked dangerous command ({reason}) and Discord approval channel is not configured.",
                }
            )
            + "\n"
        )
        return

    request_id = uuid.uuid4().hex[:12]
    prompt_msg = (
        f"🛡️ **Human Approval Required in Headless Session**\n"
        f"An ephemeral agent is requesting permission to run a restricted command:\n"
        f"```powershell\n{cmd_line}\n```\n"
        f"• **Reason flagged**: {reason}\n"
        f"👉 Reply **`approve {request_id}`** or **`deny {request_id}`** within **85 seconds**."
    )
    try:
        msg_id = send_discord_msg(token, channel_id, prompt_msg)
        decision, dec_reason = poll_discord_decision(token, channel_id, msg_id, timeout_sec=85, request_id=request_id)
        sys.stdout.write(json.dumps({"decision": decision, "reason": dec_reason}) + "\n")
    except Exception as exc:
        sys.stdout.write(
            json.dumps(
                {
                    "decision": "deny",
                    "reason": f"Failed to reach Discord approval gate ({exc}); denying dangerous command.",
                }
            )
            + "\n"
        )


if __name__ == "__main__":
    main()
