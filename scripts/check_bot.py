"""Simple Health & Status Checker for Discord Bot Gateway.

Checks:
1. Credentials: Validates DISCORD_BOT_TOKEN via Discord REST API
2. Guild & Channel: Checks access to server and configured DISCORD_CHANNEL_ID
3. Local Listener Process: Checks if `main.py discord-bot` is currently running and active
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Ensure UTF-8 output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent

# Load .env
env_file = WORKSPACE_ROOT / ".env"
token = ""
channel_id = ""

if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DISCORD_BOT_TOKEN="):
            token = line.split("=", 1)[1].strip().strip('"').strip("'")
        elif line.startswith("DISCORD_CHANNEL_ID="):
            channel_id = line.split("=", 1)[1].strip().strip('"').strip("'")

if not token:
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
if not channel_id:
    channel_id = os.environ.get("DISCORD_CHANNEL_ID", "").strip()


def check_bot_status() -> None:
    print("=" * 60)
    print("DISCORD BOT GATEWAY HEALTH CHECK")
    print("=" * 60)

    # 1. Token Check
    if not token:
        print("❌ [Token]: DISCORD_BOT_TOKEN is missing in .env")
        return
    print(f"✅ [Token Configured]: {'*' * 20}...{token[-6:]}")

    # 2. Discord API & Identity Check
    headers = {
        "Authorization": f"Bot {token}",
        "User-Agent": "AntigravityWorkspaceChecker/1.0",
    }
    user_url = "https://discord.com/api/v10/users/@me"
    try:
        req = urllib.request.Request(user_url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            user_data = json.loads(resp.read().decode("utf-8"))
            bot_tag = f"{user_data.get('username')}#{user_data.get('discriminator', '0')}"
            bot_id = user_data.get("id")
            print(f"✅ [Discord API]: Authenticated successfully as '{bot_tag}' (ID: {bot_id})")
    except urllib.error.HTTPError as e:
        print(f"❌ [Discord API]: Authentication failed (HTTP {e.code}: {e.reason})")
        return
    except Exception as e:
        print(f"❌ [Discord API]: Connection error: {e}")
        return

    # 3. Guilds Check
    guilds_url = "https://discord.com/api/v10/users/@me/guilds"
    try:
        req = urllib.request.Request(guilds_url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            guilds = json.loads(resp.read().decode("utf-8"))
            print(f"✅ [Server Access]: Bot is in {len(guilds)} server(s):")
            for g in guilds:
                print(f"    • '{g.get('name')}' (ID: {g.get('id')})")
    except Exception as e:
        print(f"⚠️ [Server Access]: Could not fetch servers: {e}")

    # 4. Configured Channel Check
    if channel_id:
        ch_url = f"https://discord.com/api/v10/channels/{channel_id}"
        try:
            req = urllib.request.Request(ch_url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                ch_data = json.loads(resp.read().decode("utf-8"))
                ch_name = ch_data.get("name", "unknown")
                ch_type = ch_data.get("type")
                type_name = "Text Channel" if ch_type == 0 else "Voice Channel Text" if ch_type == 2 else f"Type {ch_type}"
                print(f"✅ [Target Channel]: #{ch_name} (ID: {channel_id}, {type_name}) is reachable")
        except urllib.error.HTTPError as e:
            if e.code == 403:
                print(f"⚠️ [Target Channel]: Channel {channel_id} exists but bot lacks 'View Channel' permissions (403 Forbidden)")
            elif e.code == 404:
                print(f"❌ [Target Channel]: Channel {channel_id} not found (404)")
            else:
                print(f"⚠️ [Target Channel]: HTTP {e.code}: {e.reason}")
        except Exception as e:
            print(f"⚠️ [Target Channel]: Error checking channel: {e}")
    else:
        print("ℹ️ [Target Channel]: No default DISCORD_CHANNEL_ID configured in .env (auto-discovery will be used)")

    # 5. Local Listener Process (Heartbeat Check)
    hb_file = WORKSPACE_ROOT / "storage" / "bot_heartbeat.json"
    is_running = False
    if hb_file.exists():
        try:
            hb_data = json.loads(hb_file.read_text(encoding="utf-8"))
            ts = float(hb_data.get("timestamp", 0))
            pid = hb_data.get("pid")
            elapsed = time.time() - ts
            if elapsed < 15:
                is_running = True
                print("-" * 60)
                print(f"🟢 [LISTENER STATUS]: ACTIVE & LISTENING on Discord!")
                print(f"    • Process PID: {pid}")
                print(f"    • Connected as: {hb_data.get('bot_user')}")
                print(f"    • Active Guilds: {', '.join(hb_data.get('guilds', []))}")
                print(f"    • Last Heartbeat: {round(elapsed, 1)}s ago")
            else:
                print("-" * 60)
                print(f"🟡 [LISTENER STATUS]: INACTIVE (Last heartbeat was {int(elapsed)}s ago)")
        except Exception:
            pass

    if not is_running:
        print("-" * 60)
        print("🔴 [LISTENER STATUS]: Bot is NOT running locally right now.")
        print("    To start the bot listener and begin receiving tasks:")
        print(r'    & "D:\Antigravity_playgournd\.conda\python.exe" D:\Antigravity_playgournd\main.py discord-bot')

    print("=" * 60)


if __name__ == "__main__":
    check_bot_status()
