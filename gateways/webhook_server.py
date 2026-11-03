"""Unified Inbound Webhook HTTP Server for Discord, Zalo, Messenger, and REST clients.

Endpoints:
- `GET  /health`              -> Returns workspace status, active tasks, and recent event count
- `POST /webhook/task`        -> Generic JSON webhook (`{"prompt": "...", "channel": "discord", "user": "u1"}`)
- `POST /webhook/zalo`        -> Zalo OA webhook receiver (`user_send_text` event)
- `GET|POST /webhook/messenger` -> Facebook Messenger webhook verification & incoming message receiver
- `POST /webhook/world`       -> Push external world events directly to the World Watcher
"""

import json
import os
import threading
import hmac
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional
from core.database import WorkspaceDatabase


class InboundWebhookServer:
    """Background HTTP server that converts incoming messaging webhooks into workspace tasks."""

    def __init__(
        self,
        host: str,
        port: int,
        db: WorkspaceDatabase,
        enqueue_task_fn: Callable[[str, str, str], int],
        inject_world_event_fn: Optional[Callable[[str, str, str], None]] = None,
        auth_token: str = "",
    ) -> None:
        self.host = host
        self.port = port
        self.db = db
        self.enqueue_task_fn = enqueue_task_fn
        self.inject_world_event_fn = inject_world_event_fn
        self.auth_token = auth_token
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start_background(self) -> None:
        """Start the webhook server in a background daemon thread."""
        parent = self
        if not self.auth_token:
            raise ValueError("Webhook ingress requires WORKFLOW_WEBHOOK_TOKEN")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return  # Keep console output clean; all requests are logged to SQLite

            def _send_json(self, status: int, data: Dict[str, Any]) -> None:
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if not hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {parent.auth_token}"):
                    self._send_json(401, {"error": "Unauthorized"})
                    return
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/health":
                    tasks = parent.db.get_all_tasks(limit=5)
                    self._send_json(
                        200,
                        {
                            "status": "ok",
                            "recent_tasks": [
                                {"id": t.id, "title": t.title, "status": t.status}
                                for t in tasks
                            ],
                        },
                    )
                    return

                # Facebook Messenger Webhook Verification (`hub.mode=subscribe`)
                if parsed.path == "/webhook/messenger":
                    qs = urllib.parse.parse_qs(parsed.query)
                    mode = qs.get("hub.mode", [""])[0]
                    token = qs.get("hub.verify_token", [""])[0]
                    challenge = qs.get("hub.challenge", [""])[0]
                    expected = os.environ.get("MESSENGER_VERIFY_TOKEN", "antigravity_verify_token")
                    if mode == "subscribe" and token == expected:
                        body = challenge.encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "text/plain")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    else:
                        self._send_json(403, {"error": "Invalid verify token"})
                    return

                self._send_json(404, {"error": "Not found"})

            def do_POST(self) -> None:
                if not hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {parent.auth_token}"):
                    self._send_json(401, {"error": "Unauthorized"})
                    return
                parsed = urllib.parse.urlparse(self.path)
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._send_json(400, {"error": "Invalid content length"})
                    return
                if not 0 < length <= 256000:
                    self._send_json(413, {"error": "Body must be between 1 and 256000 bytes"})
                    return
                raw = self.rfile.read(length).decode("utf-8", errors="replace") if length > 0 else "{}"
                try:
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        raise json.JSONDecodeError("Object required", raw, 0)
                except json.JSONDecodeError:
                    self._send_json(400, {"error": "Invalid JSON body"})
                    return

                # 1. Generic / Discord / Custom Task Webhook
                if parsed.path == "/webhook/task":
                    prompt = str(data.get("prompt") or data.get("content") or "").strip()
                    channel = str(data.get("channel") or "console")
                    user = str(data.get("user") or "webhook_user")
                    if not prompt:
                        self._send_json(400, {"error": "Missing 'prompt' field"})
                        return
                    event_key = self.headers.get("Idempotency-Key", "")[:200]
                    task_id = parent.enqueue_task_fn(prompt, channel, user, f"webhook:{event_key}" if event_key else "")
                    self._send_json(202, {"accepted": True, "task_id": task_id})
                    return

                # 2. Zalo Official Account Webhook (`user_send_text`)
                if parsed.path == "/webhook/zalo":
                    event_name = data.get("event_name", "")
                    if event_name == "user_send_text":
                        sender_id = str(data.get("sender", {}).get("id", "zalo_user"))
                        text = str(data.get("message", {}).get("text", "")).strip()
                        if text:
                            task_id = parent.enqueue_task_fn(text, "zalo", sender_id)
                            self._send_json(200, {"error": 0, "task_id": task_id})
                            return
                    self._send_json(200, {"error": 0, "message": "ignored"})
                    return

                # 3. Facebook Messenger Webhook
                if parsed.path == "/webhook/messenger":
                    spawned = []
                    for entry in data.get("entry", []):
                        for messaging in entry.get("messaging", []):
                            sender_id = str(messaging.get("sender", {}).get("id", "fb_user"))
                            text = str(messaging.get("message", {}).get("text", "")).strip()
                            if text:
                                tid = parent.enqueue_task_fn(text, "messenger", sender_id)
                                spawned.append(tid)
                    self._send_json(200, {"status": "EVENT_RECEIVED", "spawned_tasks": spawned})
                    return

                # 4. External World Event Webhook
                if parsed.path == "/webhook/world":
                    title = str(data.get("title") or "External Webhook Event")
                    url = str(data.get("url") or "")
                    source = str(data.get("source") or "webhook")
                    if parent.inject_world_event_fn:
                        parent.inject_world_event_fn(source, title, url)
                    self._send_json(202, {"accepted": True})
                    return

                self._send_json(404, {"error": "Unknown webhook endpoint"})

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.db.log_event(
            event_type="WEBHOOK_SERVER_STARTED",
            actor="gateway:webhook_server",
            summary=f"Inbound webhook server listening on http://{self.host}:{self.port}",
        )

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
