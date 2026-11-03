"""Interchangeable CLI agents and bounded OpenAI-compatible tool loops."""
import json
import os
import sys
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from core.models import AgentRunResult
from core.processes import run_process
from core.executables import find_executable

ACTION_ROLES = {"researcher", "operator", "validator"}


class EphemeralAgentRunner:
    def __init__(self, workspace_root, config):
        self.workspace_root = Path(workspace_root).resolve()
        self.instructions_root = self.workspace_root
        self.config = config
        self.providers_cfg = config.get("providers", {})
        self.roles_cfg = config.get("roles", {})
        self.cancel_event = None
        self.deadline = None
        from core.provider_registry import ProviderRegistry
        self.extensions=ProviderRegistry()

    def for_workspace(self, workspace):
        runner = EphemeralAgentRunner(workspace, self.config)
        runner.instructions_root = self.instructions_root
        runner.extensions=self.extensions
        return runner

    def resolve_model(self, role, provider):
        cfg = self.roles_cfg.get(role, {})
        tier = cfg.get("tier", "worker")
        model_env = cfg.get("model_env")
        if model_env and os.environ.get(model_env):
            return os.environ[model_env]
        if cfg.get("model"):
            return cfg["model"]
        entry = provider.get("models", {}).get(tier, provider.get("default_model", ""))
        if isinstance(entry, dict):
            return os.environ.get(entry.get("env", ""), "") or entry.get("default", "")
        return entry

    def run_role(self, role, task_prompt, cli_log_path=None, provider_override=None):
        from core.role_memory import RoleMemory
        import hashlib
        memory = RoleMemory(self.workspace_root, role)
        cfg = self.roles_cfg.get(role, {})
        name = provider_override or cfg.get('provider', 'agy_headless_safe')
        provider = self.providers_cfg.get(name, {})
        instructions = self.instructions_root / cfg.get('prompt_file', f'roles/{role}.md')
        identity = {'provider': name, 'model': self.resolve_model(role, provider),
                    'instructions': hashlib.sha256(instructions.read_bytes()).hexdigest() if instructions.exists() else ''}
        with memory.locked():
            self.role_session = memory.load(identity)
            resumed=bool(self.role_session.get('session_id'))
            prior = memory.context()
            prompt = ('Role memory (past self-reports, NOT current verified facts):\n' + prior +
                      '\nRe-read the current handoff and actual evidence on every wake. '
                      'Previous verdicts do not validate changed artifacts.\n\n' + task_prompt)
            result = self._run_role(role, prompt, cli_log_path, provider_override)
            memory.save(self.role_session, result)
            result.structured_data.update(session_id=self.role_session.get('session_id',''),
                session_generation=self.role_session.get('generation'),
                session_rotation_reason=self.role_session.get('rotation_reason',''),
                portable_memory=str(memory.directory),resume_requested=resumed)
            if cli_log_path:
                receipt=Path(cli_log_path).with_suffix('.result.json')
                if receipt.exists():
                    from core.role_memory import atomic_json
                    saved=json.loads(receipt.read_text(encoding='utf8'))
                    saved['structured_data']=result.structured_data
                    atomic_json(receipt,saved)
            return result

    def _run_role(self, role, task_prompt, cli_log_path=None, provider_override=None):
        started = time.monotonic()
        cfg = self.roles_cfg.get(role, {})
        name = provider_override or cfg.get("provider", "agy_headless_safe")
        try:
            provider = self.providers_cfg[name]
            instructions = self.instructions_root / cfg.get("prompt_file", f"roles/{role}.md")
            text = instructions.read_text(encoding="utf-8") if instructions.exists() else ""
            cloud = self.instructions_root / "scripts" / "cloud.py"
            task_context = (f"Task workspace (all artifacts belong here): {self.workspace_root}\n"
                      f"Run name: {getattr(self, 'run_label', role)}\n"
                      f"Role: {role}. Do not modify the workflow controller or access credentials.\n"
                      f"For notebooks/cloud use: {sys.executable} {cloud} --workspace . list\n"
                      "Then call a listed tool with: ... call TOOL_NAME --args 'JSON_OBJECT'. "
                      "Colab uses the persistent official browser bridge; discover its tools with colab_tools.\n\n"
                      + task_prompt)
            prompt = f"{text}\n\n{task_context}"
            timeout = int(cfg.get("timeout_seconds", 600))
            if self.deadline:
                timeout = min(timeout, max(1, self.deadline - time.time()))
            kind = provider.get("type")
            extension=self.extensions.get(kind)
            if extension:
                result=extension.run(role=role,prompt=prompt,workspace=self.workspace_root,
                    timeout=timeout,model=self.resolve_model(role,provider),log_path=cli_log_path,cancel_event=self.cancel_event)
                if not isinstance(result,AgentRunResult):raise TypeError('Provider plugin must return AgentRunResult')
            elif kind == "mock":
                out = "VERDICT: PASS\nEvidence: mock only; no real execution." if role == "validator" else f"Mock {role}; no real execution."
                result = AgentRunResult(role, name, True, out)
            elif kind in {"agy_cli", "codex_cli", "claude_cli", "opencode_cli", "custom_cli"}:
                result = self._run_cli(role, name, provider, cfg, prompt, timeout, cli_log_path)
            elif kind == "http_llm":
                result = self._run_http(role, name, provider, cfg, text, task_context, timeout, cli_log_path)
            elif kind == "gemini_http":
                if role in ACTION_ROLES:
                    raise ValueError("gemini_http is text-only; choose agy_cli/codex_cli/claude_cli or a tool-capable http_llm")
                key = os.environ.get(provider.get("api_key_env", "GEMINI_API_KEY"), "")
                if not key:
                    raise ValueError("Missing Gemini API key")
                model = self.resolve_model(role, provider)
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                data = self._post(url, {"contents": [{"parts": [{"text": prompt}]}]}, {"x-goog-api-key": key}, timeout)
                out = "\n".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
                result = AgentRunResult(role, name, bool(out.strip()), out)
            else:
                raise ValueError(f"Unknown provider type: {kind}")
        except Exception as exc:
            result = AgentRunResult(role, name, False, "", exit_code=1, error=str(exc))
        result.duration_seconds = time.monotonic() - started
        if cli_log_path:
            result.log_file = str(cli_log_path)
            log = Path(cli_log_path)
            log.parent.mkdir(parents=True, exist_ok=True)
            # Store final evidence for every provider, not just native CLI logging.
            log.with_suffix(".result.json").write_text(json.dumps({"role": role, "provider": name,
                "success": result.success, "exit_code": result.exit_code, "output": result.output,
                "error": result.error, "duration_seconds": result.duration_seconds,
                "structured_data": result.structured_data}, indent=2), encoding="utf-8")
        return result

    def _run_cli(self, role, name, provider, cfg, prompt, timeout, log):
        kind = provider["type"]
        executable = provider.get("executable", {"agy_cli": "agy", "codex_cli": "codex", "claude_cli": "claude", "opencode_cli": "opencode"}.get(kind, ""))
        resolved = find_executable(executable) if executable else None
        if kind != "custom_cli" and not resolved:
            raise FileNotFoundError(f"{executable} is not installed/on PATH. Install/login or choose another provider.")
        model = self.resolve_model(role, provider)
        effort = cfg.get("effort", "medium")
        writable = role == "operator"
        session = getattr(self, 'role_session', {}).get('session_id', '')
        log = Path(log or self.workspace_root / ".workflow" / f"{role}.log")
        log.parent.mkdir(parents=True, exist_ok=True)
        # Files avoid Windows command length limits and shell quoting of user text.
        # Native sandbox readers must be able to open the assigned instructions.
        # Keep the live prompt in the isolated workspace and a review copy in logs.
        prompt_file = self.workspace_root / ".workflow" / (log.stem + ".prompt.md")
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text(prompt, encoding="utf-8")
        log.with_suffix(".prompt.md").write_text(prompt, encoding="utf-8")
        final_file = log.with_suffix(".final.txt")
        if final_file.exists():
            final_file.unlink()
        from core.integrations import verify_bundle
        manifest = None
        if kind != "custom_cli" and (self.workspace_root / ".workflow/integration.json").exists():
            manifest = verify_bundle(self.workspace_root, self.instructions_root)
        elif kind != "custom_cli" and role in ACTION_ROLES:
            raise ValueError("Native execution roles require a prepared isolated task integration bundle")
        if kind == "agy_cli":
            mode = provider.get("mode") or ("auto" if writable or role == "validator" else "plan")
            cmd = [resolved, "--mode", mode,
                   "--output-format", "json", "--effort", effort,
                   "--log-file", str(log)]
            if provider.get("sandbox", True):
                cmd.append("--sandbox")
            if provider.get("skip_permissions", False):
                cmd.append("--dangerously-skip-permissions")
            if model:
                cmd += ["--model", model]
            if session:
                cmd += ["--conversation", session]
            cmd += ["-p", f"Read {prompt_file} and carry out its assigned {role} step. Workspace: {self.workspace_root}"]
            input_text = None
        elif kind == "codex_cli":
            cmd = [resolved, "exec", *(["resume"] if session else []), "--json",
                   "--skip-git-repo-check", "--output-last-message", str(final_file),
                   "-c", f'model_reasoning_effort="{effort}"']
            if session:
                cmd += ['-c', 'sandbox_mode="workspace-write"' if writable or role == 'validator' else 'sandbox_mode="read-only"']
            elif writable or role == "validator":
                cmd.append("--approve-for-me")
            else:
                cmd += ["--sandbox", "read-only"]
            if manifest:
                import tomllib
                user = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
                global_config = user / "config.toml"
                settings = tomllib.loads(global_config.read_text(encoding="utf-8")) if global_config.exists() else {}
                if (user / "hooks.json").exists() or settings.get("hooks"):
                    raise ValueError("Global Codex hooks need separate review; refusing to bypass their trust together with generated task hooks")
                # Only our hash-checked generated definitions are trusted for this run.
                # This does not bypass command approvals or the OS sandbox.
                cmd += ["--dangerously-bypass-hook-trust", "-c", "features.hooks=true",
                        "-c", "projects." + json.dumps(str(self.workspace_root)) + '.trust_level="trusted"']
                # Inject reviewed hook definitions into the active CLI layer as well:
                # project-local .codex discovery varies across packaged CLI versions.
                from core.integrations import toml_value
                native = json.loads((self.workspace_root / ".workflow/codex-hooks.json").read_text(encoding="utf-8"))
                for event, definition in native["hooks"].items():
                    cmd += ["-c", f"hooks.{event}=" + toml_value(definition)]
                notebook = json.loads((self.workspace_root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["notebook"]
                cmd += ["-c", "mcp_servers.notebook=" + toml_value(notebook)]
            if model:
                cmd += ["--model", model]
            if session:
                cmd.append(session)
            cmd.append("-")
            input_text = prompt
        elif kind == "claude_cli":
            cmd = [resolved, "-p", "--output-format", "json",
                   "--permission-mode", "acceptEdits" if writable or role == "validator" else "plan",
                   "--effort", effort]
            if session:
                cmd += ['--resume', session]
            # Explicit tool permissions avoid headless approval deadlocks.
            cmd += ["--allowedTools", "Read,Glob,Grep,WebFetch,WebSearch" + (",Edit,Write,Bash" if writable else ",Bash" if role == "validator" else "")]
            if model:
                cmd += ["--model", model]
            input_text = prompt
            if manifest:
                cmd += ["--strict-mcp-config", "--mcp-config", str(self.workspace_root / ".mcp.json")]
        elif kind == "opencode_cli":
            if not model:
                raise ValueError("Set an explicit OpenCode provider/model for this role; no implicit paid fallback")
            cmd = [resolved, "run", "--format", "json", "--title", getattr(self, "run_label", role),
                   "--agent", "build" if writable or role == "validator" else "plan", "--model", model,
                   f"Read {prompt_file} and perform its assigned {role} step."]
            input_text = None
        else:
            cmd = [part.format(prompt_file=str(prompt_file), prompt=prompt, role=role, log_file=str(log),
                               workspace=str(self.workspace_root)) for part in provider.get("command_template", [])]
            input_text = None
        cmd += [str(a) for a in provider.get("extra_args", [])]
        env = dict(os.environ)
        env["WORKFLOW_WORKSPACE_ROOT"] = str(self.workspace_root)
        env["WORKFLOW_ROLE"] = role
        env["WORKFLOW_CONTROLLER_ROOT"] = str(self.instructions_root)
        env["WORKFLOW_RUN_ID"] = uuid.uuid4().hex
        env["WORKFLOW_RUN_LABEL"] = getattr(self, "run_label", role)
        env.update(getattr(self,'control_environment',{}))
        if kind == "opencode_cli":
            permissions = {"*": "deny", "read": "allow", "glob": "allow", "grep": "allow",
                           "list": "allow", "webfetch": "allow", "websearch": "allow", "skill": "allow",
                           "edit": "allow" if writable else "deny",
                           "bash": "allow" if writable or role == "validator" else "deny",
                           "external_directory": "deny", "question": "deny", "task": "deny"}
            env["OPENCODE_PERMISSION"] = json.dumps(permissions)
            env["OPENCODE_DISABLE_AUTOUPDATE"] = "true"
        env["PYTHONIOENCODING"] = "utf-8"
        # Do not attach a child CLI to the parent app's thread/shutdown channel.
        for key in ["CODEX_SESSION_ID", "CODEX_THREAD_ID", "CODEX_TOOL_CALL_ID", "CODEX_DAEMON_SHUTDOWN_FILE", "CODEX_DAEMON_SHUTDOWN_SOCKET"]:
            env.pop(key, None)
        native_hooks = manifest and kind in {"agy_cli", "codex_cli", "claude_cli"}
        if native_hooks:
            hook_start = run_process([sys.executable, str(self.instructions_root / "scripts/agent_hooks.py"),
                "--provider", "agy" if kind == "agy_cli" else "codex" if kind == "codex_cli" else "claude",
                "--event", "RoleStart", "--workspace", str(self.workspace_root)], self.workspace_root,
                timeout=10, env=env, input_text="{}")
            if hook_start["exit_code"]:
                raise RuntimeError("Workflow lifecycle hook failed: " + hook_start["stderr"])
        stdout_file, stderr_file = log.with_suffix(".stdout.log"), log.with_suffix(".stderr.log")
        result = run_process(cmd, self.workspace_root, timeout=timeout, env=env, input_text=input_text,
                             cancel_event=self.cancel_event, stdout_path=stdout_file, stderr_path=stderr_file)
        # Custom test runners may return output without writing the optional streams.
        for path, key in ((stdout_file, "stdout"), (stderr_file, "stderr")):
            if not path.exists():
                path.write_text(result[key], encoding="utf-8")
        out = final_file.read_text(encoding="utf-8") if final_file.exists() else result["stdout"]
        evidence = {"run_id": env["WORKFLOW_RUN_ID"], "executable": resolved,
                    "skills": manifest["skills"] if manifest else [], "native_hook_events": [], "native_tools": []}
        evidence["integration_mode"] = "native_hooks" if native_hooks else "controller_observed"
        if kind in {'agy_cli', 'claude_cli'}:
            try:
                native = json.loads(result['stdout'])
                if isinstance(native, dict):
                    evidence['usage']=native.get('usage',{})
                    evidence['session_id'] = native.get('session_id') or native.get('conversation_id') or native.get('conversationId') or native.get('sessionId') or ''
                    conversation=native.get('conversation')
                    if not evidence['session_id'] and isinstance(conversation,dict):
                        evidence['session_id']=conversation.get('id','')
                    message = native.get('result') or native.get('response') or native.get('text')
                    if isinstance(message,str):
                        out = message
                    if native.get('is_error'):
                        result['exit_code'] = result['exit_code'] or 1
            except ValueError:
                pass  # Older CLIs retain portable memory even without native IDs.
        if kind == "codex_cli":
            # Session files may be ephemeral; preserve the provider ID in our index.
            with stdout_file.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get("type") == "thread.started":
                        evidence["session_id"] = event.get("thread_id", "")
        opencode_error = None
        if kind == "opencode_cli":
            chunks = []
            for line in stdout_file.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("sessionID"):
                    evidence["session_id"] = event["sessionID"]
                if event.get("type") == "text":
                    chunks.append(event.get("part", {}).get("text", ""))
                if event.get("type") == "error":
                    opencode_error = "OpenCode returned an error event; inspect the saved transcript"
            out = "\n".join(chunks)
            final_file.write_text(out, encoding="utf-8")
        hook_error = None
        if native_hooks:
            receipts = self.workspace_root / ".workflow/hook-events.jsonl"
            records = [json.loads(line) for line in receipts.read_text(encoding="utf-8").splitlines()]
            records = [record for record in records if record.get("run_id") == env["WORKFLOW_RUN_ID"]]
            evidence["native_hook_events"] = [record["event"] for record in records if record["event"] != "RoleStart"]
            evidence["native_tools"] = [record["tool"] for record in records if record["event"] == "PreToolUse"]
            evidence["native_mcp_tools"] = [record["mcp_tool"] for record in records if record["event"] == "PreToolUse" and record.get("mcp_tool")]
            if "SessionStart" not in evidence["native_hook_events"]:
                hook_error = "CLI did not execute its required native SessionStart hook; inspect native config/trust and stderr log"
        cli_error = opencode_error
        if result['exit_code']:
            if 'authentication required' in result['stderr'].lower() or 'authentication failed or timed out' in result['stderr'].lower():
                cli_error = 'CLI authentication required in this execution environment; defer until the user can sign in or approve using the authenticated environment.'
            else:
                cli_error = result['stderr'] or f"CLI exited with code {result['exit_code']}"
        # A CLI which never authenticated cannot reach hooks. Report its actual
        # startup failure before secondary missing-hook symptoms.
        return AgentRunResult(role, name, result["exit_code"] == 0 and bool(out.strip()) and not hook_error and not cli_error, out.strip(),
            exit_code=result["exit_code"], error=cli_error or hook_error, structured_data=evidence)

    @staticmethod
    def _post(url, payload, headers, timeout):
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", **headers}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=max(1, timeout)) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            # Response bodies may contain credentials or request echoes; don't log them.
            raise RuntimeError(f"Model endpoint HTTP {exc.code}") from None

    def _run_http(self, role, name, provider, cfg, instructions, prompt, timeout, log):
        from core.tools import AgentTools
        key_env = provider.get("api_key_env", "OPENROUTER_API_KEY")
        key = os.environ.get(key_env, "")
        if provider.get("require_api_key", True) and not key:
            raise ValueError(f"Missing {key_env}")
        model = self.resolve_model(role, provider)
        if not model:
            raise ValueError(f"No model configured for {role}; set its provider tier model/environment variable")
        if role in ACTION_ROLES and not provider.get("supports_tools", False):
            raise ValueError("Execution/research role requires supports_tools=true and a tool-capable model")
        tools = AgentTools(self.workspace_root, role, self.config, self.instructions_root)
        tools.control_environment=getattr(self,'control_environment',{})
        schemas = tools.schemas() if role in ACTION_ROLES else []
        messages = [{"role": "system", "content": instructions + f"\nWorkspace: {self.workspace_root}"},
                    {"role": "user", "content": prompt}]
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        deadline = time.monotonic() + timeout
        token_total = 0
        trace = []
        for turn in range(int(provider.get("max_tool_rounds", 24))):
            remaining = deadline - time.monotonic()
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise InterruptedError("Task cancelled")
            if remaining <= 0:
                raise TimeoutError("Agent wall-clock budget exceeded")
            payload = {"model": model, "messages": messages, "max_tokens": int(cfg.get("max_output_tokens", 4096))}
            if schemas:
                payload.update(tools=schemas, tool_choice="auto")
            if "openrouter.ai" in provider.get("base_url", ""):
                payload["provider"] = {"require_parameters": True}
                if provider.get("free_only", False):
                    payload["provider"]["max_price"] = {"prompt": 0, "completion": 0}
            try:
                data = self._post(provider["base_url"], payload, headers, remaining)
            except urllib.error.HTTPError as exc:
                if exc.code==429 and turn==0 and not trace:
                    try: delay=max(15,min(3600,int(exc.headers.get('Retry-After','60'))))
                    except (ValueError,TypeError,AttributeError): delay=60
                    return AgentRunResult(role,name,False,'',error='Provider rate limited before any tool call',
                        structured_data={'retry_safe':True,'retry_after':delay})
                raise  # A later failure can follow side effects; do not automatically replay it.
            if "error" in data:
                raise RuntimeError("Model endpoint returned an error")
            token_total += int(data.get("usage", {}).get("total_tokens", 0))
            if token_total > int(provider.get("max_total_tokens", 100000)):
                raise RuntimeError("Agent token budget exceeded")
            choice = data["choices"][0]
            message = choice["message"]
            calls = message.get("tool_calls", [])
            if not calls:
                out = message.get("content") or ""
                if choice.get("finish_reason") in {"length", "content_filter"} or not out.strip():
                    raise RuntimeError("Model returned an incomplete or empty answer")
                return AgentRunResult(role, name, True, out, structured_data={"tokens": token_total, "tool_trace": trace})
            if not schemas:
                raise ValueError("Text-only role attempted a tool call")
            messages.append(message)  # Preserve provider reasoning details needed by reasoning models.
            for call in calls:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Agent tool budget exceeded")
                tool_name = call["function"]["name"]
                try:
                    args = json.loads(call["function"].get("arguments", "{}"))
                    result = tools.call(tool_name, args, timeout=max(1, deadline - time.monotonic()))
                except Exception as exc:
                    result = {"error": str(exc)}
                trace.append({"name": tool_name, "result": result})
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})
        raise RuntimeError("Maximum agent tool rounds exceeded")
