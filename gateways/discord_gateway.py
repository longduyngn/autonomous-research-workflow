"""Discord Messaging Gateway (Webhook & Bot REST API) + Console Gateway."""

import json
import os
import urllib.request
from typing import Any, Dict, Optional
from gateways.base import BaseMessagingGateway


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
        if not self.webhook_url and not self.bot_token:
            return False

        # Discord message limit is 2000 characters
        content = message[:1990]
        if self.webhook_url:
            req = urllib.request.Request(
                self.webhook_url,
                data=json.dumps({"content": content}).encode("utf-8"),
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

        target_channel = recipient_id or self.default_channel_id
        if self.bot_token and target_channel:
            url = f"https://discord.com/api/v10/channels/{target_channel}/messages"
            req = urllib.request.Request(
                url,
                data=json.dumps({"content": content}).encode("utf-8"),
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
