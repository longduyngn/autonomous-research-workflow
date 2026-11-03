"""Small persistent stdio MCP client; accepts tool-list-change notifications."""
import json
import os
import queue
import subprocess
import threading
import time
from collections import deque
from core.processes import stop_process_tree


class StdioMCPClient:
    def __init__(self, command, cwd=None, env=None):
        self.command, self.cwd, self.env = command, cwd, env
        self.proc = None
        self.messages = queue.Queue()
        self.lock = threading.Lock()
        self.sequence = 0
        self.tools_changed = True
        self.stderr_tail = deque(maxlen=15)

    def start(self):
        if self.proc and self.proc.poll() is None:
            return
        self.proc = subprocess.Popen(self.command, cwd=self.cwd, env=self.env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        def reader():
            for line in self.proc.stdout:
                try:
                    message = json.loads(line)
                    if message.get("method") == "notifications/tools/list_changed":
                        self.tools_changed = True
                    self.messages.put(message)
                except ValueError:
                    continue
            self.messages.put({"_closed": True})
        threading.Thread(target=reader, daemon=True).start()
        def errors():
            for line in self.proc.stderr:
                self.stderr_tail.append(line.strip())
        threading.Thread(target=errors, daemon=True).start()
        self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "research-workflow", "version": "2.0"}}, timeout=120)
        self.notify("notifications/initialized", {})

    def notify(self, method, params):
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params=None, timeout=120):
        with self.lock:
            self.sequence += 1
            request_id = self.sequence
            self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id,
                "method": method, "params": params or {}}) + "\n")
            self.proc.stdin.flush()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    message = self.messages.get(timeout=max(0.01, deadline - time.monotonic()))
                except queue.Empty:
                    break
                if message.get("_closed"):
                    raise RuntimeError("MCP server exited; check its command/dependencies. " + " ".join(self.stderr_tail)[-1500:])
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    raise RuntimeError(f"MCP request failed: {message['error']}")
                return message.get("result", {})
            self.notify("notifications/cancelled", {"requestId": request_id, "reason": "deadline exceeded"})
            raise TimeoutError(f"MCP {method} timed out; remote operation may still need inspection")

    def tools(self):
        # Re-list every time; browser connection changes schemas dynamically.
        result = self.request("tools/list")
        self.tools_changed = False
        return result.get("tools", [])

    def call(self, name, arguments, timeout=180):
        return self.request("tools/call", {"name": name, "arguments": arguments}, timeout)

    def close(self):
        if self.proc:
            if self.proc.poll() is None:
                stop_process_tree(self.proc)
            self.proc.wait(timeout=10)
            self.proc.stdin.close()
            self.proc.stdout.close()
            self.proc.stderr.close()
