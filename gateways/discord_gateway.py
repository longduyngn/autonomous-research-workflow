"""Discord Messaging Gateway (Webhook & Bot REST API) + Console Gateway."""

import json
import os
import urllib.request
from typing import Any, Dict, Optional
from gateways.base import BaseMessagingGateway
import re


def _norm_channel_name(name: str) -> str:
    return re.sub(r"[\s_-]+", "_", (name or "").strip().lower()).strip("_")


def discover_playground_channels(bot_token: str, category_name: str = "antigravity_playground") -> Dict[str, str]:
    """Find `task_proposal`, `approval_list`, and `task_log` under `antigravity_playground` via Discord REST API."""
    if not bot_token or "unittest" in os.sys.modules:
        return {}
    headers = {
        "Authorization": f"Bot {bot_token}",
        "User-Agent": "AntigravityWorkspaceBot/1.0",
    }
    target_cat = _norm_channel_name(category_name)
    roles_aliases = {
        "task_proposal": {"task_proposal", "task_proposals"},
        "approval_list": {"approval_list", "approvals", "approval"},
        "task_log": {"task_log", "task_logs"},
    }
    found: Dict[str, str] = {}
    try:
        req = urllib.request.Request("https://discord.com/api/v10/users/@me/guilds", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            guilds = json.loads(resp.read().decode("utf-8"))
        for g in guilds:
            gid = g.get("id")
            if not gid:
                continue
            creq = urllib.request.Request(f"https://discord.com/api/v10/guilds/{gid}/channels", headers=headers)
            with urllib.request.urlopen(creq, timeout=10) as cresp:
                channels = json.loads(cresp.read().decode("utf-8"))
            cat_ids = {
                str(ch.get("id"))
                for ch in channels
                if ch.get("type") == 4 and _norm_channel_name(ch.get("name", "")) == target_cat
            }
            for pass_in_cat in (True,):
                if not cat_ids:
                    continue
                for ch in channels:
                    if ch.get("type") not in (0, 5):
                        continue
                    if pass_in_cat and cat_ids and str(ch.get("parent_id")) not in cat_ids:
                        continue
                    cname = _norm_channel_name(ch.get("name", ""))
                    for key, names in roles_aliases.items():
                        if key not in found and cname in names:
                            found[key] = str(ch.get("id"))
            if found:
                found["guild_id"] = str(gid)
                if cat_ids:
                    found["category_id"] = next(iter(cat_ids))
                break
    except Exception:
        return found
    return found


class ConsoleGateway(BaseMessagingGateway):
    """Local terminal gateway (always active for immediate visibility and testing)."""

    channel_name = "console"

    def send_message(self, message: str, recipient_id: Optional[str] = None) -> bool:
        target = f" (@{recipient_id})" if recipient_id else ""
        print(
            f"\n==================== [COMMUNICATOR -> CONSOLE{target}] ====================\n"
            f"{message}\n"
            f"===========================================================================\n"
        )
        return True


class DiscordGateway(BaseMessagingGateway):
    """Discord adapter supporting outgoing Webhooks and Channel REST messages."""

    channel_name = "discord"

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg
        self.webhook_url = os.environ.get(cfg.get("webhook_url_env", "DISCORD_WEBHOOK_URL"), "")
        self.bot_token = os.environ.get(cfg.get("bot_token_env", "DISCORD_BOT_TOKEN"), "")
        self.default_channel_id = os.environ.get(
            cfg.get("default_channel_id_env", "DISCORD_CHANNEL_ID"), ""
        )

    def send_message(self, message: str, recipient_id: Optional[str] = None) -> bool:
        parts = [message[i:i+1900] for i in range(0, len(message), 1900)] or [""]
        for part in parts:
            if not self._send_chunk(part, recipient_id):
                return False
        return True

    def _send_chunk(self, message: str, recipient_id: Optional[str] = None) -> bool:
        if not self.webhook_url and not self.bot_token:
            return False

        # Discord message limit is 2000 characters
        content = message[:1990]
        if self.webhook_url and not (self.bot_token and recipient_id):
            req = urllib.request.Request(
                self.webhook_url,
                data=json.dumps({"content": content, "allowed_mentions": {"parse": []}}).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "AntigravityWorkspaceBot/1.0",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return 200 <= resp.status < 300
            except Exception:
                return False

        target_channel = recipient_id or self.default_channel_id or (discover_playground_channels(self.bot_token).get("task_log") if self.bot_token else "")
        if self.bot_token and target_channel:
            url = f"https://discord.com/api/v10/channels/{target_channel}/messages"
            req = urllib.request.Request(
                url,
                data=json.dumps({"content": content, "allowed_mentions": {"parse": []}}).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bot {self.bot_token}",
                    "User-Agent": "AntigravityWorkspaceBot/1.0",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return 200 <= resp.status < 300
            except Exception:
                return False
        return False
