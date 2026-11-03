"""Loopback-only read-only HTTP view; never starts workers or sends messages."""
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from core.observability import task_list, task_detail, run_log


def make_server(db, root, port=8787, host='127.0.0.1'):
    root = Path(root).resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, body, content_type="application/json; charset=utf-8"):
            data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            host = self.headers.get("Host", "")
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if host not in allowed:
                return self.respond(403, {"error": "Use the local dashboard address"})
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://{h}" for h in allowed}:
                return self.respond(403, {"error": "Cross-origin access refused"})
            url = urlsplit(self.path)
            assets = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8")}
            if url.path in assets:
                name, kind = assets[url.path]
                return self.respond(200, (root / "ui" / name).read_text(encoding="utf-8"), kind)
            try:
                if url.path == "/api/tasks":
                    return self.respond(200, task_list(db))
                match = re.fullmatch(r"/api/tasks/(\d+)", url.path)
                if match:
                    return self.respond(200, task_detail(db, int(match[1])))
                match = re.fullmatch(r"/api/runs/(\d+)/log", url.path)
                if match:
                    kind = parse_qs(url.query).get("kind", ["stdout"])[0]
                    return self.respond(200, run_log(db, root, int(match[1]), kind))
            except (ValueError, OSError):
                return self.respond(404, {"error": "Task, run or transcript not found"})
            self.respond(404, {"error": "Not found"})

        def do_POST(self):
            self.respond(405, {"error": "This dashboard is read-only"})

    if host not in {'127.0.0.1','0.0.0.0'}:
        raise ValueError('Unsupported dashboard bind address')
    return ThreadingHTTPServer((host, port), Handler)


def serve_dashboard(db, root, port=8787, host='127.0.0.1'):
    server = make_server(db, root, port,host)
    print(f"Task dashboard: http://127.0.0.1:{server.server_port} (read-only)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
