"""Scripted OpenAI-compatible chat-completions server for offline provider tests.

Each POST to ``/v1/chat/completions`` consumes the next scripted step whose
``match`` (substring of the serialized request) fits, or the next unmatched
step otherwise. When the script is exhausted a plain ``"OK"`` answer is sent.

Step forms::

    {"text": "answer"}                                  final text
    {"tool": "read", "arguments": {"filePath": "x"}}    one tool call
    {"status": 429, "headers": {"Retry-After": "7"}, "body": {...}}
    {"usage": {"prompt_tokens": 3, "completion_tokens": 2}}   optional on text/tool
    {"drop_after_chunks": 1}                             cut the stream mid-answer
    {"need_tools": true, ...}                            only for requests offering tools

Requests without tools (for example OpenCode's session-title call) never
consume a ``need_tools`` step.

Requests are appended (JSON lines) to ``requests_path`` for assertions.
Credentials in the Authorization header are never recorded.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class ScriptedAPIServer:
    def __init__(self, script, requests_path=None, host="127.0.0.1", port=0):
        self.script = list(script)
        self.requests_path = Path(requests_path) if requests_path else None
        self.lock = threading.Lock()
        self.requests = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path.rstrip("/").endswith("/models"):
                    server._json(self, 200, {"object": "list", "data": [
                        {"id": "fake-model", "object": "model", "owned_by": "fake"}]})
                else:
                    server._json(self, 404, {"error": {"message": "not found"}})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0") or 0)
                raw = self.rfile.read(length).decode("utf-8", errors="replace")
                try:
                    body = json.loads(raw) if raw else {}
                except ValueError:
                    body = {}
                server._record(self.path, body, bool(self.headers.get("Authorization")))
                step = server._next_step(raw, bool(body.get("tools")))
                server._respond(self, body, step)

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self):
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}/v1"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()

    def _record(self, path, body, had_auth):
        entry = {"path": path, "had_authorization": had_auth, "model": body.get("model"),
                 "stream": bool(body.get("stream")), "tools": [t.get("function", {}).get("name")
                 for t in body.get("tools") or []], "messages": body.get("messages", [])}
        with self.lock:
            self.requests.append(entry)
            if self.requests_path:
                self.requests_path.parent.mkdir(parents=True, exist_ok=True)
                with self.requests_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _next_step(self, raw, has_tools=False):
        with self.lock:
            eligible = [(i, s) for i, s in enumerate(self.script) if has_tools or not s.get("need_tools")]
            for index, step in eligible:
                if step.get("match") and step["match"] in raw:
                    return self.script.pop(index)
            for index, step in eligible:
                if not step.get("match"):
                    return self.script.pop(index)
        return {"text": "OK"}

    @staticmethod
    def _json(handler, status, payload, headers=None):
        data = json.dumps(payload).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            handler.send_header(key, value)
        handler.end_headers()
        handler.wfile.write(data)

    def _respond(self, handler, body, step):
        if "status" in step:
            self._json(handler, step["status"], step.get("body", {"error": {"message": "scripted error",
                       "code": step["status"]}}), step.get("headers"))
            return
        usage = step.get("usage", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        model = body.get("model", "fake-model")
        if "tool" in step:
            delta = {"role": "assistant", "content": None, "tool_calls": [{"index": 0, "id": step.get("id", "call_1"),
                     "type": "function", "function": {"name": step["tool"],
                     "arguments": json.dumps(step.get("arguments", {}))}}]}
            finish = "tool_calls"
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": step.get("id", "call_1"),
                       "type": "function", "function": {"name": step["tool"],
                       "arguments": json.dumps(step.get("arguments", {}))}}]}
        else:
            delta = {"role": "assistant", "content": step.get("text", "OK")}
            finish = "stop"
            message = {"role": "assistant", "content": step.get("text", "OK")}
        if not body.get("stream"):
            self._json(handler, 200, {"id": "cmpl-fake", "object": "chat.completion", "created": 0, "model": model,
                       "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage})
            return
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.end_headers()
        chunks = [{"id": "cmpl-fake", "object": "chat.completion.chunk", "created": 0, "model": model,
                   "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                  {"id": "cmpl-fake", "object": "chat.completion.chunk", "created": 0, "model": model,
                   "choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": usage}]
        limit = step.get("drop_after_chunks")
        for index, chunk in enumerate(chunks):
            if limit is not None and index >= limit:
                handler.wfile.flush()
                handler.close_connection = True
                return
            handler.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            handler.wfile.flush()
        handler.wfile.write(b"data: [DONE]\n\n")
        handler.wfile.flush()
