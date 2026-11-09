"""Bounded, read-only Codex app-server queries using the CLI's own login."""
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

from core.executables import find_executable
from core.processes import stop_process_tree


def read_app_server(method, params=None, executable='codex', cwd=None, timeout=25):
    # This client cannot start turns, change auth/config, or redeem quota resets.
    if method not in {'account/rateLimits/read', 'model/list'}:
        raise ValueError('Only read-only quota and model queries are supported')
    resolved = find_executable(executable)
    if not resolved:
        raise FileNotFoundError('Codex executable unavailable')
    env = dict(os.environ)
    if not env.get('HOME'):
        env['HOME'] = str(Path.home())
    if not env.get('CODEX_HOME'):
        env['CODEX_HOME'] = str(Path.home() / '.codex')
    for key in ('CODEX_SESSION_ID', 'CODEX_THREAD_ID', 'CODEX_TOOL_CALL_ID',
                'CODEX_DAEMON_SHUTDOWN_FILE', 'CODEX_DAEMON_SHUTDOWN_SOCKET'):
        env.pop(key, None)
    proc = subprocess.Popen([resolved, 'app-server', '--listen', 'stdio://'], cwd=cwd,
                            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace',
                            start_new_session=os.name != 'nt',
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
    messages = queue.Queue()

    def read_lines():
        try:
            for line in proc.stdout:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                # Do not retain unrelated notifications or server requests.
                if isinstance(item, dict) and item.get('id') in (1, 2) and ('result' in item or 'error' in item):
                    messages.put(item)
        finally:
            messages.put(None)

    reader = threading.Thread(target=read_lines, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout

    def send(value):
        proc.stdin.write(json.dumps(value) + '\n')
        proc.stdin.flush()

    def response(request_id):
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Codex app-server read timed out')
            try:
                item = messages.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError('Codex app-server read timed out') from None
            if item is None:
                raise RuntimeError('Codex app-server closed before responding')
            if item.get('id') != request_id:
                continue
            if 'error' in item:
                # Never expose raw errors, which may include authentication details.
                raise RuntimeError('Codex app-server rejected read-only query')
            return item.get('result')

    try:
        send({'id': 1, 'method': 'initialize', 'params': {
            'clientInfo': {'name': 'workflow_quota', 'version': '1.0'},
            'capabilities': {'experimentalApi': True}}})
        response(1)
        send({'method': 'initialized'})
        send({'id': 2, 'method': method, 'params': params or {}})
        return response(2)
    finally:
        proc.stdin.close()
        if proc.poll() is None:
            stop_process_tree(proc)
        proc.wait(timeout=10)
        reader.join(timeout=2)
        proc.stdout.close()
