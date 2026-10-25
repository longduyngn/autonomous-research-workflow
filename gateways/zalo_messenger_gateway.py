"""Zalo Official Account (OA) & Facebook Messenger Gateways."""

import json
import os
import urllib.request
from typing import Any, Dict, Optional
from gateways.base import BaseMessagingGateway


class ZaloGateway(BaseMessagingGateway):
    """Zalo Official Account (OA) OpenAPI v3.0 Customer Service Messaging Adapter."""

    channel_name = "zalo"

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg
        self.access_token = os.environ.get(
            cfg.get("oa_access_token_env", "ZALO_OA_ACCESS_TOKEN"), ""
        )
        self.default_user_id = os.environ.get(
            cfg.get("default_recipient_id_env", "ZALO_DEFAULT_USER_ID"), ""
        )

    def send_message(self, message: str, recipient_id: Optional[str] = None) -> bool:
        target_user = recipient_id or self.default_user_id
        if not self.access_token or not target_user:
            return False

        url = "https://openapi.zalo.me/v3.0/oa/message/cs"
        payload = {
            "recipient": {"user_id": target_user},
            "message": {"text": message[:2000]},
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "access_token": self.access_token,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("error", -1) == 0
        except Exception:
            return False


class MessengerGateway(BaseMessagingGateway):
    """Facebook Messenger Platform Graph API Send Adapter."""

    channel_name = "messenger"

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg
        self.page_access_token = os.environ.get(
            cfg.get("page_access_token_env", "MESSENGER_PAGE_ACCESS_TOKEN"), ""
        )
        self.default_user_id = os.environ.get(
            cfg.get("default_recipient_id_env", "MESSENGER_DEFAULT_USER_ID"), ""
        )

    def send_message(self, message: str, recipient_id: Optional[str] = None) -> bool:
        psid = recipient_id or self.default_user_id
        if not self.page_access_token or not psid:
            return False

        url = f"https://graph.facebook.com/v19.0/me/messages?access_token={self.page_access_token}"
        payload = {
            "recipient": {"id": psid},
            "messaging_type": "RESPONSE",
            "message": {"text": message[:2000]},
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return 200 <= resp.status < 300
        except Exception:
            return False
