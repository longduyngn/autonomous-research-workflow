"""Role-scoped file/tools for API-backed agents. Shell commands are trusted code."""
import fnmatch
import json
import os
from pathlib import Path
from core.processes import run_process


def function(name, description, properties, required=()):
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}}}


class AgentTools:
    def __init__(self, root, role, config=None, controller=None):
        self.root, self.role = Path(root).resolve(), role
        self.config, self.controller = config or {}, Path(controller or root).resolve()

    def path(self, value):
        path = (self.root / value).resolve()
        if not path.is_relative_to(self.root):
            raise PermissionError("Path escapes the task workspace")
        rel = path.relative_to(self.root)
        if any(p.lower() in {".git", ".aws", ".codex", ".kaggle", ".ssh", ".config", ".credentials", "auth.json", "kaggle.json", "credentials.json", "token.json", "access_token"}
               or p.lower().startswith(".env") or p.lower().endswith(".env")
               or p.lower().endswith((".pem", ".key", ".p12", ".pfx")) for p in rel.parts):
            raise PermissionError("Credential/internal path is not an agent artifact")
        return path

    def schemas(self):
        tools = [
            function('tail_log', 'Read a bounded log tail; use for logs instead of read_file',
                     {'path': {'type': 'string'}, 'n_lines': {'type': 'integer'}}, ['path']),
            function('search_log', 'Regex search with bounded matches and line numbers',
                     {'path': {'type': 'string'}, 'pattern': {'type': 'string'},
                      'max_matches': {'type': 'integer'}}, ['path', 'pattern']),
            function("read_file", "Read UTF-8 text; start_char continues long files",
                     {"path": {"type": "string"}, "start_char": {"type": "integer"}}, ["path"]),
            function("read_document", "Extract local PDF text by page range (no images or OCR)",
                     {"path": {"type": "string"}, "start_page": {"type": "integer"},
                      "page_count": {"type": "integer"}}, ["path"]),
            function("list_files", "List workspace files (bounded; ignores secrets/runtime dirs)", {"pattern": {"type": "string"}}),
            function("search_files", "Search a literal string in text files", {"text": {"type": "string"}}, ["text"]),
            function("fetch_url", "Fetch a public http(s) paper/document URL", {"url": {"type": "string"}}, ["url"]),
        ]
        if self.role == 'communicator':
            return [t for t in tools if t['function']['name'] in
                    {'read_file', 'list_files', 'tail_log', 'search_log'}]
        tools += [function('verify_artifacts', 'Verify registered file hashes and sizes', {}),
                  function('status_digest', 'Read campaign status and handoff once in <=2KB',
                           {'campaign': {'type': 'string'}})]
        if self.role in {'operator', 'validator'}:
            tools.append(function('register_artifact', 'Hash a workspace file and append a receipt',
                {'name': {'type': 'string'}, 'path': {'type': 'string'}, 'metadata': {'type': 'object'}},
                ['name', 'path']))
        if self.role in {'operator', 'researcher'}:
            tools.append(function('fetch_repo', 'Fetch source as untrusted data; never execute it',
                {'url': {'type': 'string'}, 'target_subdir': {'type': 'string'}}, ['url', 'target_subdir']))
        if self.role == "operator":
            tools.append(function("write_file", "Write a UTF-8 file within this task workspace",
                {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]))
        if self.role in {'researcher','operator','validator'}:
            tools.append(function('agent_checkin','Answer supervisor nonce with an explicitly unverified self-report',
                {'nonce':{'type':'string'},'stage':{'type':'string'},'completed':{'type':'number'},
                 'blocking_on':{'enum':['none','model_download','tool','gpu','network','unknown']}},
                ['nonce','stage','completed','blocking_on']))
        if self.role in {"operator", "validator"}:
            tools += [function("run_command", "Execute argv in this task workspace; use for experiments/tests",
                {"argv": {"type": "array", "items": {"type": "string"}}, "timeout_seconds": {"type": "integer"}}, ["argv"]),
                function("cloud_tool", "Call notebook/cloud tools. Use tool=list to discover schemas; colab_tools discovers live Colab tools.",
                    {"tool": {"type": "string"}, "arguments": {"type": "object"}}, ["tool"])]
        return tools

    def call(self, name, args, timeout=120):
        from core.control import check_worker_lease
        check_worker_lease(getattr(self,'control_environment',{}))
        if (getattr(self,'control_environment',{}).get('WORKFLOW_DIAGNOSTIC_READONLY')=='1'
                and name not in {'read_file','list_files','search_files','tail_log','search_log','status_digest'}):
            raise PermissionError('Recovery facts consultation is read-only; network/write/shell/cloud tools unavailable')
        allowed = {t["function"]["name"] for t in self.schemas()}
        if name not in allowed:
            raise PermissionError(f"{self.role} cannot use {name}")
        if not isinstance(args, dict):
            raise ValueError("Tool arguments must be an object")
        if name in {'tail_log', 'search_log'}:
            from core import log_tools
            return getattr(log_tools, name)(self.path(args['path']), **{k: v for k, v in args.items() if k != 'path'})
        if name == 'register_artifact':
            from core.artifacts import register
            return register(self.root, args['name'], args['path'], args.get('metadata'))
        if name == 'verify_artifacts':
            from core.artifacts import verify
            return verify(self.root)
        if name == 'status_digest':
            from core.campaign_tools import status_digest
            return status_digest(self.root, args.get('campaign', '.'))
        if name == 'fetch_repo':
            from core.repo_fetch import fetch_repo
            return fetch_repo(self.root, args['url'], args['target_subdir'], timeout=min(timeout, 120))
        if name=='agent_checkin':
            from core.cloud import CloudTools
            return CloudTools(self.root,self.config).agent_checkin(args)
        if name == "read_file":
            text = self.path(args["path"]).read_text(encoding="utf-8")
            start = max(0, int(args.get("start_char", 0)))
            return {"text": text[start:start + 24000], "total_chars": len(text),
                    "next_start_char": start + 24000 if start + 24000 < len(text) else None}
        if name == "read_document":
            from pypdf import PdfReader
            path = self.path(args["path"])
            if path.suffix.lower() != ".pdf":
                raise ValueError("read_document supports PDF; use read_file for text")
            reader = PdfReader(path)
            start = max(0, int(args.get("start_page", 0)))
            end = min(len(reader.pages), start + min(10, max(1, int(args.get("page_count", 5)))))
            pages = [{"page_index": i, "text": (reader.pages[i].extract_text() or "")[:24000]}
                     for i in range(start, end)]
            return {"pages": pages, "total_pages": len(reader.pages),
                    "next_start_page": end if end < len(reader.pages) else None,
                    "note": "Text only; diagrams and scans need separate visual inspection"}
        if name == "write_file":
            path = self.path(args["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args["content"], encoding="utf-8")
            return {"written": str(path.relative_to(self.root))}
        if name in {"list_files", "search_files"}:
            results = []
            for directory, dirs, files in os.walk(self.root):
                dirs[:] = [d for d in dirs if d not in {".git", ".conda", ".venv", "node_modules", "__pycache__", "storage"}
                           and not (Path(directory) / d).is_symlink()]
                for filename in files:
                    path = Path(directory) / filename
                    try:
                        path = self.path(str(path.relative_to(self.root)))
                        rel = path.relative_to(self.root).as_posix()
                        if name == "list_files" and fnmatch.fnmatch(rel, args.get("pattern", "*")):
                            results.append(rel)
                        elif name == "search_files" and path.stat().st_size < 1_000_000:
                            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                                if args["text"] in line:
                                    results.append({"path": rel, "line": i, "text": line[:400]})
                                    if len(results) >= 200:
                                        return {"results": results, "truncated": True}
                    except (OSError, UnicodeError, PermissionError):
                        continue
                    if len(results) >= 200:
                        return {"results": results, "truncated": True}
            return {"results": results}
        if name == "fetch_url":
            from core.network import fetch_public_text
            return {"text": fetch_public_text(args["url"], timeout=min(timeout, 30))}
        if name == "run_command":
            import subprocess
            from scripts.pre_tool_approval_hook import is_dangerous_command
            context=getattr(self,'control_environment',{})
            dangerous,reason=is_dangerous_command(subprocess.list2cmdline(args['argv']))
            if dangerous:
                if not context.get('WORKFLOW_DB_PATH'):
                    raise PermissionError(reason)
                from core.approvals import command_decision
                allowed,reason=command_decision(context['WORKFLOW_DB_PATH'],int(context['WORKFLOW_TASK_ID']),self.root,name,args)
                if not allowed:
                    raise PermissionError(reason)
            return run_process(args["argv"], self.root, timeout=min(timeout, max(1, args.get("timeout_seconds", 120))))
        if name == "cloud_tool":
            from core.cloud import CloudTools
            tools = CloudTools(self.root, self.config)
            return tools.schemas() if args["tool"] == "list" else tools.call(args["tool"], args.get("arguments", {}))
        raise ValueError(f"Unknown tool {name}")
