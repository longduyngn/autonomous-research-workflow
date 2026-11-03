"""Authenticated loopback bridge keeping Google's browser MCP session alive."""
import hmac
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from core.mcp_client import StdioMCPClient
from core.executables import find_executable
from core.resource_gate import ColabGate, ResourceBusy


def browser_action(name, args):
    """Only the bridge performs managed browser effects under its fencing lock."""
    import importlib.util
    script=Path(__file__).resolve().parent.parent/'.agents/skills/colab-coccoc-playwright/scripts/colab_playwright.py'
    spec=importlib.util.spec_from_file_location('colab_playwright',script)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from core.colab_runtime import runtime_status, prepare_runtime_controls
    if name=='runtime_status':
        return runtime_status(mod)
    if name=='set_runtime':
        if not args.get('hardware'):
            raise ValueError('Choose hardware explicitly')
        prepare_runtime_controls(mod)
        result=mod.set_runtime(args['hardware'])
    elif name=='disconnect_runtime':
        prepare_runtime_controls(mod)
        result=mod.disconnect_runtime()
    elif name=='trigger_cell_run':
        result=mod.trigger_cell_run(args.get('cellId'),args.get('cellIndex',-1))
    else:
        raise ValueError('Unknown managed browser action')
    result.pop('url',None)
    return result


def serve_colab_bridge(config, host="127.0.0.1", port=8765, *, start=True, controller_root=None):
    token = os.environ.get(config.get("colab_bridge_token_env", "COLAB_BRIDGE_TOKEN"), "")
    if not token:
        raise ValueError("Set COLAB_BRIDGE_TOKEN before starting the bridge")
    command = list(config.get("colab_command", ["uvx", "--from", "git+https://github.com/googlecolab/colab-mcp", "colab-mcp"]))
    controller = Path(controller_root) if controller_root else Path(__file__).resolve().parent.parent
    from core.portability import resolve_command,browser_executable
    command = resolve_command(command,controller)
    if not find_executable(command[0]) and config.get("colab_bootstrap_command"):
        command = [part.replace("{controller}", str(controller))
                   for part in config["colab_bootstrap_command"]]
    command[0] = find_executable(command[0]) or command[0]
    environment = dict(os.environ)
    environment["UV_PYTHON_INSTALL_DIR"] = str(controller / "storage/tools/python")
    environment["UV_TOOL_DIR"] = str(controller / "storage/tools/uv-tools")
    browser = browser_executable(config.get('colab_browser',''))
    if browser:
        environment["COLAB_BROWSER"] = browser
        hook_dir = controller / "storage/tools/browser_hook"
        hook_dir.mkdir(parents=True, exist_ok=True)
        environment["COLAB_URL_FILE"] = str(controller / "storage/tools/colab_connect_url.txt")
        (hook_dir / "sitecustomize.py").write_text(
            "import os, socket, subprocess, webbrowser\n"
            "_b = os.environ.get('COLAB_BROWSER', '')\n"
            "_f = os.environ.get('COLAB_URL_FILE', '')\n"
            "def _cdp_alive():\n"
            "    try:\n"
            "        with socket.create_connection(('127.0.0.1', 9222), timeout=0.3):\n"
            "            return True\n"
            "    except OSError:\n"
            "        return False\n"
            "class _ColabBrowser(webbrowser.BaseBrowser):\n"
            "    def open(self, url, new=0, autoraise=True):\n"
            "        if _f:\n"
            "            try:\n"
            "                open(_f, 'w', encoding='utf-8').write(url)\n"
            "            except OSError:\n"
            "                pass\n"
            "        if not _cdp_alive() and _b and os.path.isfile(_b):\n"
            "            try:\n"
            "                subprocess.Popen([_b, url])\n"
            "            except OSError:\n"
            "                pass\n"
            "        return True\n"
            "webbrowser.register('colab-custom', None, _ColabBrowser('colab-custom'), preferred=True)\n",
            encoding="utf-8",
        )
        existing_pp = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = f"{hook_dir}{os.pathsep}{existing_pp}" if existing_pp else str(hook_dir)
    shared_session = environment.get("COLAB_SHARED_SESSION", "1") != "0" and config.get("colab_shared_session", True)
    clients = {}
    clients_lock = threading.Lock()
    # Personal Colab always has one managed owner, even if transports are separate.
    gate=ColabGate(controller/config.get('resource_directory','storage/resource-control'),
                   lambda: browser_action('runtime_status',{}))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            status, result = 200, {}
            try:
                if not hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}"):
                    status, result = 401, {"error": "Unauthorized"}
                elif not 0 < int(self.headers.get("Content-Length", 0)) <= 2_000_000:
                    status, result = 400, {"error": "Invalid body size"}
                else:
                    data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    session = self.headers.get("X-Workflow-Session", "")
                    if not re.fullmatch(r"[0-9a-f]{64}", session):
                        raise ValueError("A task workspace session is required")
                    key = "shared" if shared_session else session
                    generation=int(self.headers.get('X-Workflow-Generation','-1'))
                    if self.path=='/claim':
                        result=gate.claim(session)
                    elif self.path=='/release':
                        def close_forced_client():
                            if data.get('force'):
                                with clients_lock:
                                    client=clients.pop(key,None)
                                    if client: client.close()
                        result=gate.release(session,generation,before_release=close_forced_client)
                    elif self.path=='/browser':
                        result=gate.execute(session,generation,lambda:browser_action(data['name'],data.get('arguments',{})))
                    elif self.path not in {'/tools','/call'}:
                        status,result=404,{'error':'Not found'}
                    else:
                        def invoke():
                            with clients_lock:
                                if key not in clients or clients[key].proc is None or clients[key].proc.poll() is not None:
                                    stale=clients.pop(key,None)
                                    if stale:
                                        try: stale.close()
                                        except Exception: pass
                                    client=StdioMCPClient(command,env=environment)
                                    try: client.start()
                                    except Exception:
                                        client.close()
                                        raise
                                    clients[key]=client
                                client=clients[key]
                            if self.path=='/tools':
                                return {'tools':client.tools()}
                            if data['name'] not in {t['name'] for t in client.tools()}:
                                raise ValueError('Tool not available; reconnect browser and list again')
                            return client.call(data['name'],data.get('arguments',{}),timeout=300)
                        result=gate.execute(session,generation,invoke)
            except ResourceBusy as exc:
                status,result=409,{'error':str(exc),'kind':'resource_busy'}
            except Exception as exc:
                status, result = 400, {"error": str(exc)}
            body = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer((host, port), Handler)
    if not start:
        return server
    print(f"Colab browser bridge at http://{host}:{port}. Keep this process running.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        for client in clients.values():
            client.close()
