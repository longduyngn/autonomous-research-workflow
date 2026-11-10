"""Interchangeable CLI agents and bounded OpenAI-compatible tool loops."""
import json
import os
import re
import sys
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from core.models import AgentRunResult
from core.processes import run_process
from core.executables import find_executable

from core.role_names import ACTION_ROLES  # planner, operator, validator


def claude_stream_result(lines):
    """Last final result + initialization identity; metadata alone cannot succeed."""
    session_id, final, denied = '', None, []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        session_id = event.get('session_id') or session_id
        denied += event.get('permission_denials') or event.get('denied_actions') or []
        if event.get('type') == 'result' or (not event.get('type') and 'result' in event):
            final = event
    if final is not None:
        final = {**final, 'session_id': final.get('session_id') or session_id}
        if denied:
            final['permission_denials'] = denied
    return final


class ModelHTTPError(RuntimeError):
    """HTTP failure from a model endpoint with allow-listed fields only (no raw body)."""

    def __init__(self, status, retry_after=None, safe_message=''):
        self.status, self.retry_after, self.safe_message = status, retry_after, safe_message
        self.error_class = _opencode_error_class(status, safe_message)
        super().__init__(f'Model endpoint HTTP {status}' + (f': {safe_message}' if safe_message else ''))

    @classmethod
    def from_http_error(cls, exc):
        retry = None
        try:
            header = exc.headers.get('Retry-After') if exc.headers else None
            retry = float(header) if header is not None else None
        except (TypeError, ValueError, AttributeError):
            retry = None
        message = ''
        try:
            body = json.loads(exc.read().decode('utf-8', errors='replace') or '{}')
            error = body.get('error') if isinstance(body, dict) else None
            if isinstance(error, dict):
                message = str(error.get('message') or '')[:200]
            elif isinstance(error, str):
                message = error[:200]
        except Exception:
            message = ''
        return cls(exc.code, retry, message)


def _opencode_error_class(status, message):
    text = (message or '').lower()
    if status == 429 or 'rate limit' in text or 'rate-limit' in text:
        return 'QUOTA_EXHAUSTED' if any(m in text for m in ('per-day', 'per day', 'daily', 'quota', 'credits')) else 'RATE_LIMIT'
    if status == 402 or 'insufficient credits' in text or 'payment required' in text:
        return 'QUOTA_EXHAUSTED'
    if status in (401, 403) or 'unauthorized' in text or 'invalid api key' in text:
        return 'AUTH_REQUIRED'
    if status == 404 or 'model not found' in text or 'no endpoints found' in text:
        return 'MODEL_UNAVAILABLE'
    if 'context length' in text or 'context_length' in text or 'too many tokens' in text:
        return 'CONTEXT_LIMIT'
    if status is not None and status >= 500 or 'econnrefused' in text or 'cannot connect' in text:
        return 'TRANSPORT'
    return 'PROTOCOL'


def parse_opencode_stream(lines):
    """Summarize ``opencode run --format json`` events (schema of the pinned 1.19.5 CLI).

    A run is ``completed`` only when its last step finished with a non-tool reason and no
    error event arrived; usage is summed over steps and stays ``None`` when never reported.
    """
    summary = {'session_id': '', 'text': '', 'tools': [], 'steps': 0, 'finish_reason': '', 'completed': False,
               'usage': None, 'cost_usd': None, 'error': '', 'error_class': None, 'status_code': None,
               'retry_after': None}
    chunks = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        summary['session_id'] = event.get('sessionID') or summary['session_id']
        part = event.get('part') or {}
        kind = event.get('type')
        if kind == 'text':
            chunks.append(part.get('text', ''))
        elif kind == 'tool_use':
            state = part.get('state') or {}
            summary['tools'].append({'tool': part.get('tool', ''), 'status': state.get('status', '')})
        elif kind == 'step_finish':
            summary['steps'] += 1
            summary['finish_reason'] = part.get('reason', '')
            tokens = part.get('tokens')
            if isinstance(tokens, dict):
                usage = summary['usage'] or {'input_tokens': 0, 'output_tokens': 0, 'reasoning_tokens': 0,
                                             'cache_read_tokens': 0, 'cache_write_tokens': 0}
                cache = tokens.get('cache') or {}
                for key, value in (('input_tokens', tokens.get('input')), ('output_tokens', tokens.get('output')),
                                   ('reasoning_tokens', tokens.get('reasoning')),
                                   ('cache_read_tokens', cache.get('read')), ('cache_write_tokens', cache.get('write'))):
                    if isinstance(value, (int, float)):
                        usage[key] += int(value)
                # Context size is the last step's prompt, not the sum billed across steps.
                usage['context_tokens'] = int(tokens.get('input') or 0) + int(cache.get('read') or 0)
                summary['usage'] = usage
            if isinstance(part.get('cost'), (int, float)):
                summary['cost_usd'] = (summary['cost_usd'] or 0.0) + float(part['cost'])
        elif kind == 'error':
            error = event.get('error') or {}
            data = error.get('data') or {}
            message = data.get('message') or error.get('message') or error.get('name') or 'error event'
            status = data.get('statusCode')
            headers = {str(k).lower(): v for k, v in (data.get('responseHeaders') or {}).items()}
            try:
                summary['retry_after'] = float(headers['retry-after']) if 'retry-after' in headers else None
            except (TypeError, ValueError):
                summary['retry_after'] = None
            summary['status_code'] = status
            # Only allow-listed fields: response bodies may echo request content.
            summary['error'] = f"{error.get('name', 'Error')}: {str(message)[:300]}" + (f' (HTTP {status})' if status else '')
            summary['error_class'] = _opencode_error_class(status, message)
    summary['text'] = '\n'.join(c for c in chunks if c)
    summary['completed'] = (summary['steps'] > 0 and summary['finish_reason'] not in ('tool-calls', '')
                            and not summary['error'])
    return summary


_PINNED_FALLBACK_MARKERS = ('quota', 'usage limit', 'rate limit', 'rate-limit', 'capacity', 'overloaded',
                            'model not found', 'model is not available', 'unavailable model', 'unknown model',
                            'timed out', 'timeout', 'no output', 'resource exhausted', '429', '503')
_PINNED_STOP_MARKERS = ('authentication required', 'authentication failed', 'not installed', 'denied an action',
                        'permission', 'unauthorized', 'login')


def _pinned_should_fall_back(result):
    """Next pinned model only for capacity/model problems, never for auth or permission failures."""
    data = result.structured_data or {}
    if data.get('quota'):
        return True
    text = (result.error or '').lower()
    if any(marker in text for marker in _PINNED_STOP_MARKERS):
        return False
    return any(marker in text for marker in _PINNED_FALLBACK_MARKERS)


class EphemeralAgentRunner:
    def _free_selector(self):
        from core.openrouter_free import FreeModelSelector
        state = Path(self.config.get('openrouter_state_dir') or self.instructions_root / 'storage' / 'openrouter')
        if not state.is_absolute():
            state = self.instructions_root / state
        selector = getattr(self, '_free_selector_cache', None)
        if selector is None or selector.state_dir != state:
            selector = FreeModelSelector(state)
            self._free_selector_cache = selector
        return selector

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
        from core.remote_claude import RemoteClaudeOperatorProvider
        self.extensions.register('remote_claude_operator', RemoteClaudeOperatorProvider(self.instructions_root, self.config))

    def for_workspace(self, workspace):
        runner = EphemeralAgentRunner(workspace, self.config)
        runner.instructions_root = self.instructions_root
        runner.extensions=self.extensions
        return runner

    def resolve_model(self, role, provider, override=None):
        cfg = self.roles_cfg.get(role, {})
        tier = cfg.get("tier", "worker")
        model_env = cfg.get("model_env")
        # A router override replaces the role pin; "" means this provider's tier default.
        if override:
            return override
        if override is None and model_env and os.environ.get(model_env):
            return os.environ[model_env]
        if override is None and cfg.get("model"):
            return cfg["model"]
        entry = provider.get("models", {}).get(tier, provider.get("default_model", ""))
        if isinstance(entry, dict):
            return os.environ.get(entry.get("env", ""), "") or entry.get("default", "")
        return entry

    def pinned_chain(self, role):
        """[(provider, model), ...] for a role pinned to one provider type (D1), else None."""
        cfg = self.roles_cfg.get(role, {})
        if not cfg.get('pinned'):
            return None
        return [(cfg.get('provider', ''), cfg.get('model', ''))] + [
            (item.get('provider', ''), item.get('model', '')) for item in cfg.get('fallbacks', [])]

    def run_role(self, role, task_prompt, cli_log_path=None, provider_override=None, model_override=None):
        chain = self.pinned_chain(role)
        if not chain:
            return self._run_role_with_memory(role, task_prompt, cli_log_path, provider_override, model_override)
        # Pinned role: task/global provider overrides never move it; a router choice is honoured
        # only as a starting point inside the chain.
        start = next((i for i, (p, m) in enumerate(chain)
                      if p == provider_override and model_override and m == model_override), 0)
        attempts = []
        result = None
        for index, (provider, model) in enumerate(chain[start:], start):
            result = self._run_role_with_memory(role, task_prompt, cli_log_path, provider, model)
            data = result.structured_data
            attempts.append({'provider': provider, 'model': model, 'success': result.success,
                             'error': (result.error or '')[:200]})
            if result.success:
                break
            if not _pinned_should_fall_back(result):
                break  # auth/permission/CLI problems are not solved by another model
        data = result.structured_data
        data['pinned_route'] = {'attempts': attempts, 'chain': [list(item) for item in chain]}
        if not result.success and len(attempts) and _pinned_should_fall_back(result):
            # Whole pinned chain exhausted: callers use a no-LLM template; do not park the task
            # in a provider wait for a reporting/watching role.
            data['pinned_exhausted'] = True
            data['pinned_quota'] = data.pop('quota', None)
        return result

    def _run_role_with_memory(self, role, task_prompt, cli_log_path=None, provider_override=None, model_override=None):
        from core.role_memory import RoleMemory
        import hashlib
        memory = RoleMemory(self.workspace_root, role)
        cfg = self.roles_cfg.get(role, {})
        name = provider_override or cfg.get('provider', 'agy_headless_safe')
        provider = self.providers_cfg.get(name, {})
        instructions = self.instructions_root / cfg.get('prompt_file', f'roles/{role}.md')
        identity = {'provider': name, 'model': self.resolve_model(role, provider, model_override),
                    'instructions': hashlib.sha256(instructions.read_bytes()).hexdigest() if instructions.exists() else ''}
        with memory.locked():
            self.role_session = memory.load(identity)
            if role == 'guardian':
                self.role_session['session_id'] = ''
                self.role_session['rotation_reason'] = 'fresh guardian check'
            resumed=bool(self.role_session.get('session_id'))
            prior = memory.context()
            prompt = ('Role memory (past self-reports, NOT current verified facts):\n' + prior +
                      '\nRead status_digest once and inspect changed evidence on every wake. '
                      'Do not reread SKILL.md already present in context. '
                      'Previous verdicts do not validate changed artifacts.\n\n' + task_prompt)
            result = self._run_role(role, prompt, cli_log_path, provider_override, model_override)
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

    def _run_role(self, role, task_prompt, cli_log_path=None, provider_override=None, model_override=None):
        started = time.monotonic()
        cfg = self.roles_cfg.get(role, {})
        name = provider_override or cfg.get("provider", "agy_headless_safe")
        provider = self.providers_cfg.get(name, {})
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
            from core.configuration import role_turn_budget
            budget = role_turn_budget(self.config, role)
            prompt += (f'\nSoft turn budget: about {budget} turns. It is guidance, not a hard stop: '
                       'finish one bounded item; if you go past it, wrap up the current step, then save '
                       'a checkpoint/handoff with completed actions, evidence and remaining work. '
                       'Use scripts/artifacts.py register instead of ad-hoc hashing; '
                       'use scripts/logs.py tail_log/search_log for logs.\n')
            if self.deadline:
                timeout = min(timeout, max(1, self.deadline - time.time()))
            kind = provider.get("type")
            extension=self.extensions.get(kind)
            if extension:
                result=extension.run(role=role,prompt=prompt,workspace=self.workspace_root,
                    timeout=timeout,model=self.resolve_model(role,provider,model_override),log_path=cli_log_path,cancel_event=self.cancel_event)
                if not isinstance(result,AgentRunResult):raise TypeError('Provider plugin must return AgentRunResult')
            elif kind == "mock":
                out = "VERDICT: PASS\nEvidence: mock only; no real execution." if role == "validator" else f"Mock {role}; no real execution."
                result = AgentRunResult(role, name, True, out)
            elif kind in {"agy_cli", "codex_cli", "claude_cli", "opencode_cli", "custom_cli"}:
                result = self._run_cli(role, name, provider, cfg, prompt, timeout, cli_log_path, model_override)
            elif kind == "http_llm":
                result = self._run_http(role, name, provider, cfg, text, task_context, timeout, cli_log_path, model_override)
            elif kind == "gemini_http":
                if role in ACTION_ROLES:
                    raise ValueError("gemini_http is text-only; choose agy_cli/codex_cli/claude_cli or a tool-capable http_llm")
                key = os.environ.get(provider.get("api_key_env", "GEMINI_API_KEY"), "")
                if not key:
                    raise ValueError("Missing Gemini API key")
                model = self.resolve_model(role, provider, model_override)
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                data = self._post(url, {"contents": [{"parts": [{"text": prompt}]}]}, {"x-goog-api-key": key}, timeout)
                out = "\n".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
                result = AgentRunResult(role, name, bool(out.strip()), out)
            else:
                raise ValueError(f"Unknown provider type: {kind}")
        except Exception as exc:
            result = AgentRunResult(role, name, False, "", exit_code=1, error=str(exc))
        result.duration_seconds = time.monotonic() - started
        if provider.get('type') == 'claude_cli':
            from core.quota import persist_claude_run
            context = getattr(self, 'control_environment', {})
            database = context.get('WORKFLOW_DB_PATH') or self.config.get('database_path')
            if database:
                database = Path(database)
                if not database.is_absolute():
                    database = self.instructions_root / database
                result.log_file = str(cli_log_path) if cli_log_path else None
                try:
                    persist_claude_run(database, result, task_id=context.get('WORKFLOW_TASK_ID'),
                        agent_run_id=context.get('WORKFLOW_AGENT_RUN_ID'), config=self.config)
                except Exception as exc:
                    # Never replay tool side effects because telemetry failed.
                    result.structured_data['telemetry_error'] = str(exc)
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

    def _run_cli(self, role, name, provider, cfg, prompt, timeout, log, model_override=None):
        kind = provider["type"]
        executable = provider.get("executable", {"agy_cli": "agy", "codex_cli": "codex", "claude_cli": "claude", "opencode_cli": "opencode"}.get(kind, ""))
        resolved = find_executable(executable) if executable else None
        if kind != "custom_cli" and not resolved:
            raise FileNotFoundError(f"{executable} is not installed/on PATH. Install/login or choose another provider.")
        model = self.resolve_model(role, provider, model_override)
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
            mode = provider.get("mode") or ("accept-edits" if writable or role == "validator" else "plan")
            if role == 'communicator':
                mode = 'plan'
            if mode == "auto":
                mode = "accept-edits"
            cmd = [resolved, "--mode", mode,
                   "--output-format", "json", "--effort", effort,
                   "--log-file", str(log)]
            if provider.get("sandbox", True):
                cmd.append("--sandbox")
            if provider.get("skip_permissions", False) and role != 'communicator':
                cmd.append("--dangerously-skip-permissions")
            if model:
                cmd += ["--model", model]
            if session:
                cmd += ["--conversation", session]
            # Text-only management roles can receive their bounded prompt inline;
            # requiring a file-read tool here deadlocks on unavailable host sandboxes.
            cli_prompt = prompt if provider.get('inline_prompt', False) else (
                f"Read {prompt_file} and carry out its assigned {role} step. Workspace: {self.workspace_root}")
            cmd += ["-p", cli_prompt]
            input_text = None
        elif kind == "codex_cli":
            # Packaged Windows CLIs may not discover the profile even when
            # Python can. Use one home for both policy inspection and execution;
            # never copy auth/config or silently switch to an empty task home.
            profile_home = Path.home()
            codex_home = Path(provider.get("codex_home") or os.environ.get("CODEX_HOME")
                              or profile_home / ".codex").expanduser()
            if not codex_home.is_absolute():
                codex_home = self.workspace_root / codex_home
            codex_home = codex_home.resolve()
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
                user = codex_home
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
            # No --max-turns: the turn budget is soft (prompt + over-budget signal).
            cmd = [resolved, "-p", "--output-format", "stream-json", "--verbose",
                   "--permission-mode", "acceptEdits" if writable or role == "validator" else "plan",
                   "--effort", effort]
            if session:
                cmd += ['--resume', session]
            # Explicit tool permissions avoid headless approval deadlocks.
            allowed_tools = ('Read,Glob,Grep' if role in {'communicator', 'guardian'} else
                             'Read,Glob,Grep,WebFetch,WebSearch' +
                             (',Edit,Write,Bash' if writable else ',Bash' if role == 'validator' else ''))
            cmd += ['--tools', allowed_tools, '--allowedTools', allowed_tools]
            if model:
                cmd += ["--model", model]
            input_text = prompt
            if manifest:
                cmd += ["--strict-mcp-config", "--mcp-config", str(self.workspace_root / ".mcp.json")]
        elif kind == "opencode_cli":
            from core.opencode_config import api_profile, qualified_model
            profile = api_profile(provider)
            free_selector = None
            if profile and (profile['auto_free'] or profile['free_only']):
                free_selector = self._free_selector()
            if profile and not model and profile['auto_free']:
                model = free_selector.select(cfg.get('tier', 'worker'), need_tools=role != 'communicator',
                                             min_context=profile['min_context'])
                if not model:
                    reset = free_selector.next_reset()
                    if reset is None:
                        raise ValueError('No free tool-capable OpenRouter model in the current catalog')
                    # Whole free pool cooling down: report a quota wait for the profile account.
                    return AgentRunResult(role, name, False, '', exit_code=1,
                        error='Provider quota exhausted: every free OpenRouter model is cooling down',
                        structured_data={'quota_account': profile['account'], 'model': '',
                            'quota': {'exhausted': True, 'matched': 'all free models cooling down',
                                      'resets_at': reset, 'account': profile['account'], 'provider': name, 'model': ''}})
            if not model:
                raise ValueError("Set an explicit OpenCode provider/model for this role; no implicit paid fallback")
            if profile and profile['free_only'] and not free_selector.is_free(model):
                raise ValueError(f"free_only profile refuses model {model}: not zero-priced in the current catalog")
            opencode_model = qualified_model(profile, model) if profile else model
            state = getattr(self, 'role_session', None)
            if session and isinstance(state, dict) and state.get('native_model') not in (None, opencode_model):
                # A native session is bound to the model that produced it (auto_free may pick
                # another model): start a fresh session; portable memory/handoff carry context.
                session = ''
                state['session_id'] = ''
                state['rotation_reason'] = 'native model changed'
            if isinstance(state, dict):
                state['native_model'] = opencode_model
            # --dir: tool paths otherwise resolve against the inherited PWD (verified on 1.19.5).
            cmd = [resolved, "run", "--format", "json", "--title", getattr(self, "run_label", role),
                   "--agent", "build" if writable or role == "validator" else "plan", "--model", opencode_model,
                   "--dir", str(self.workspace_root)]
            if session:
                # Exact stored session only; never "continue the last session".
                cmd += ["--session", session]
            cmd.append(f"Read {prompt_file} and perform its assigned {role} step.")
            input_text = None
        else:
            cmd = [part.format(prompt_file=str(prompt_file), prompt=prompt, role=role, log_file=str(log),
                               workspace=str(self.workspace_root)) for part in provider.get("command_template", [])]
            input_text = None
        cmd += [str(a) for a in provider.get("extra_args", [])]
        if role == 'communicator' and kind in {'agy_cli', 'claude_cli'}:
            reserved = {'--mode', '--permission-mode', '--dangerously-skip-permissions',
                        '--allowedTools', '--tools', '--settings', '--append-system-prompt',
                        '--permission-prompt-tool', '--mcp-config'}
            if any(str(a).split('=', 1)[0] in reserved for a in provider.get('extra_args', [])):
                raise ValueError('Communicator extra_args cannot override role permissions')
            if kind == 'agy_cli':
                cmd += ['--settings', json.dumps({'permissions': {'deny':
                        ['command(*)', 'write_file(*)', 'mcp(*)', 'execute_url(*)']}})]
        env = dict(os.environ)
        env["WORKFLOW_WORKSPACE_ROOT"] = str(self.workspace_root)
        env["WORKFLOW_ROLE"] = role
        env["WORKFLOW_CONTROLLER_ROOT"] = str(self.instructions_root)
        env["WORKFLOW_RUN_ID"] = uuid.uuid4().hex
        env["WORKFLOW_RUN_LABEL"] = getattr(self, "run_label", role)
        env.update(getattr(self,'control_environment',{}))
        if kind == "codex_cli":
            # Apply after control_environment so the checked policy home cannot
            # diverge from the home actually loaded by the child CLI.
            env["CODEX_HOME"] = str(codex_home)
            if not env.get("HOME"):
                env["HOME"] = str(profile_home)
        if kind == "opencode_cli":
            if profile:
                from core.opencode_config import check_credential, isolated_env, runtime_config
                check_credential(profile, env)
                home = self.workspace_root / ".workflow" / "opencode-home" / role
                env = isolated_env(env, home, runtime_config(profile, model))
            env["PWD"] = str(self.workspace_root)
            # Pinned OpenCode 1.19.5 understands only the V1 permission schema.
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
        for stale in (stdout_file, stderr_file):
            # Never parse a previous invocation's stream as this run's output.
            stale.unlink(missing_ok=True)
        supervision = {}
        options = self.config.get('monitoring', {})
        if (options.get('enabled', True) and role in ACTION_ROLES
                and env.get('WORKFLOW_DB_PATH') and env.get('WORKFLOW_TASK_ID')):
            from core.supervision import extension_for_progress
            hard = float(options.get('max_agent_seconds',21600))
            if self.deadline:
                hard = min(hard,max(1,self.deadline-time.time()))
            supervision = {'hard_timeout':max(timeout,hard), 'timeout_callback':lambda:
                extension_for_progress(self.workspace_root,env['WORKFLOW_DB_PATH'],int(env['WORKFLOW_TASK_ID']))}
        result = run_process(cmd, self.workspace_root, timeout=timeout, env=env, input_text=input_text,
                             cancel_event=self.cancel_event, stdout_path=stdout_file, stderr_path=stderr_file, **supervision)
        if kind == "codex_cli":
            for _ in range(2):
                combined_initial = f"{result['stdout']}\n{result['stderr']}".lower()
                if result["exit_code"] and "selected model is at capacity" in combined_initial and '"type":"command_execution"' not in result["stdout"] and '"type":"file_change"' not in result["stdout"]:
                    time.sleep(3)
                    env["WORKFLOW_RUN_ID"] = uuid.uuid4().hex
                    if native_hooks:
                        run_process([sys.executable, str(self.instructions_root / "scripts/agent_hooks.py"),
                            "--provider", "codex", "--event", "RoleStart", "--workspace", str(self.workspace_root)],
                            self.workspace_root, timeout=10, env=env, input_text="{}")
                    result = run_process(cmd, self.workspace_root, timeout=timeout, env=env, input_text=input_text,
                                         cancel_event=self.cancel_event, stdout_path=stdout_file, stderr_path=stderr_file)
                else:
                    break
        # Custom test runners may return output without writing the optional streams.
        for path, key in ((stdout_file, "stdout"), (stderr_file, "stderr")):
            if not path.exists():
                path.write_text(result[key], encoding="utf-8")
        out = final_file.read_text(encoding="utf-8") if final_file.exists() else result["stdout"]
        evidence = {"run_id": env["WORKFLOW_RUN_ID"], "executable": resolved,
                    "skills": manifest["skills"] if manifest else [], "native_hook_events": [], "native_tools": []}
        evidence["integration_mode"] = "native_hooks" if native_hooks else "controller_observed"
        from core.configuration import role_turn_budget
        evidence['turn_budget'] = role_turn_budget(self.config, role)
        evidence['turn_enforcement'] = 'soft: prompt guidance and over-budget signal; wall-clock timeout is the hard stop'
        if kind in {'agy_cli', 'claude_cli'}:
            try:
                if kind == 'claude_cli':
                    with stdout_file.open(encoding='utf-8') as stream:
                        native = claude_stream_result(stream)
                    if native is None:
                        result['exit_code'] = result['exit_code'] or 1
                        result['stderr'] = result['stderr'] or 'Claude stream ended without a final result'
                        out = ''
                else:
                    native = json.loads(result['stdout'])
                if isinstance(native, dict):
                    evidence['usage']=native.get('usage',{})
                    if kind == 'claude_cli':
                        evidence.update(num_turns=native.get('num_turns', 0),
                                        total_cost_usd=native.get('total_cost_usd', 0),
                                        model_usage=native.get('modelUsage', {}),
                                        stop_reason=native.get('subtype', ''))
                        if (native.get('num_turns') or 0) > evidence['turn_budget']:
                            evidence['turn_budget_exceeded'] = True
                        if native.get('subtype') == 'error_max_turns':
                            evidence['budget_exhausted'] = True
                            out = str(native.get('result') or 'Turn budget reached; reconcile saved artifacts before continuing.')
                            result['exit_code'] = 0
                            result['stderr'] = ''
                    evidence['session_id'] = native.get('session_id') or native.get('conversation_id') or native.get('conversationId') or native.get('sessionId') or ''
                    conversation=native.get('conversation')
                    if not evidence['session_id'] and isinstance(conversation,dict):
                        evidence['session_id']=conversation.get('id','')
                    message = native.get('result') or native.get('response') or native.get('text')
                    if isinstance(message,str):
                        out = message
                    elif kind == 'agy_cli':
                        out = ''  # Native metadata is not a final response.
                    if native.get('denied_actions') or native.get('permission_denials'):
                        result['exit_code'] = result['exit_code'] or 1
                        result['stderr'] = 'Native CLI denied an action; explicit scoped permission is required.'
                    if native.get('is_error') and not evidence.get('budget_exhausted'):
                        result['exit_code'] = result['exit_code'] or 1
            except ValueError:
                pass  # Older CLIs retain portable memory even without native IDs.
            if kind == "agy_cli" and not evidence.get("session_id") and log.exists():
                log_text = log.read_text(encoding="utf-8", errors="replace")
                match = re.search(r"(?:conversation=|Created conversation )([0-9a-fA-F-]{36})\b", log_text)
                if match:
                    evidence["session_id"] = match.group(1)
        codex_errors = []
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
                    elif event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
                        evidence["usage"] = event["usage"]
                    elif event.get('type') in {'error', 'turn.failed'}:
                        codex_errors.append(event)
        opencode_error = None
        opencode_limit = None
        if kind == "opencode_cli":
            parsed = parse_opencode_stream(stdout_file.read_text(encoding="utf-8").splitlines())
            if parsed["session_id"]:
                evidence["session_id"] = parsed["session_id"]
            evidence.update(usage=parsed["usage"], total_cost_usd=parsed["cost_usd"], num_turns=parsed["steps"],
                            native_tools=parsed["tools"], stop_reason=parsed["finish_reason"],
                            error_class=parsed["error_class"])
            if profile:
                evidence["api_vendor"] = profile["vendor"]
            out = parsed["text"]
            final_file.write_text(out, encoding="utf-8")
            if parsed["error"]:
                opencode_error = "OpenCode error: " + parsed["error"]
            elif not parsed["completed"]:
                # Exit 0 with text but no final stop step is not a completed answer.
                opencode_error = "OpenCode stream ended without a final stop step; inspect the saved transcript"
            if parsed["error_class"] in {"RATE_LIMIT", "QUOTA_EXHAUSTED"}:
                retry = parsed["retry_after"]
                opencode_limit = {"exhausted": True, "matched": parsed["error"][:80],
                                  "resets_at": time.time() + (retry if retry is not None else
                                  float(self.config.get("quota_routing", {}).get("default_cooldown_seconds", 900))),
                                  "error_class": parsed["error_class"]}
                if profile and profile["auto_free"]:
                    # Only this free model cools down; the next call picks the next free model.
                    self._free_selector().cool_down(model, opencode_limit["resets_at"])
                    evidence["free_model_cooled_down"] = model
        hook_error = None
        if native_hooks:
            receipts = self.workspace_root / ".workflow/hook-events.jsonl"
            records = [json.loads(line) for line in receipts.read_text(encoding="utf-8").splitlines()]
            records = [record for record in records if record.get("run_id") == env["WORKFLOW_RUN_ID"]]
            evidence["native_hook_events"] = [record["event"] for record in records if record["event"] != "RoleStart"]
            evidence["native_tools"] = [record["tool"] for record in records if record["event"] == "PreToolUse"]
            evidence["native_mcp_tools"] = [record["mcp_tool"] for record in records if record["event"] == "PreToolUse" and record.get("mcp_tool")]
            if "SessionStart" not in evidence["native_hook_events"] and not (kind == "agy_cli" and session and evidence["native_hook_events"]):
                hook_error = "CLI did not execute its required native SessionStart hook; inspect native config/trust and stderr log"
        cli_error = opencode_error
        if codex_errors:
            result['exit_code'] = result['exit_code'] or 1
            cli_error = json.dumps(codex_errors[-1], ensure_ascii=False)
        if result['exit_code']:
            if 'authentication required' in result['stderr'].lower() or 'authentication failed or timed out' in result['stderr'].lower():
                cli_error = 'CLI authentication required in this execution environment; defer until the user can sign in or approve using the authenticated environment.'
            else:
                cli_error = cli_error or result['stderr'] or f"CLI exited with code {result['exit_code']}"
        elif role in ACTION_ROLES:
            combined = f"{out}\n{result['stdout']}\n{result['stderr']}".lower()
            if (re.search(r"(?im)^\s*blocked(?:\s*:|\s*$|\s+-)", out)
                    or any(marker in combined for marker in (
                        "blocked before inspection by the local execution environment",
                        "helper_unknown_error: setup refresh had errors",
                        "windows sandbox: setup refresh had errors",
                    ))):
                cli_error = "Worker reported blocked execution or sandbox failure despite exit code 0"
        # A CLI which never authenticated cannot reach hooks. Report its actual
        # startup failure before secondary missing-hook symptoms.
        success = result["exit_code"] == 0 and bool(out.strip()) and not hook_error and not cli_error
        if evidence.get('budget_exhausted') and (hook_error or result['exit_code']):
            # Permission/hook failures take precedence over a bounded stop.
            evidence.pop('budget_exhausted', None)
        if evidence.get('budget_exhausted'):
            from core.role_memory import atomic_json
            handoff = self.workspace_root / '.workflow' / (log.stem + '.budget-handoff.json')
            atomic_json(handoff, {'reason': 'turn budget reached', 'run_id': evidence['run_id'],
                        'session_id': evidence.get('session_id', ''), 'output': out[-4000:],
                        'checkpoint_note': 'Reconcile current artifacts; this is not a completion verdict.'})
            evidence['handoff'] = str(handoff)
            success = False
            cli_error = 'Turn budget reached; checkpoint saved for reconciliation'
        from core.quota import LimitErrorDetector, account_for, snapshots_from_stream
        evidence['model'], evidence['quota_account'] = model, account_for(provider, model) or ''
        if kind == 'opencode_cli' and profile and profile['auto_free']:
            # Free models are limited per model; one model's 429 must not block the whole account.
            evidence['quota_account'] = f"{profile['account']}:{model}"
        observed = snapshots_from_stream(result['stdout'], kind)
        if kind == 'claude_cli':
            # run_process returns a bounded stdout tail; quota can arrive near
            # initialization, long before that tail. Parse the complete saved stream.
            with stdout_file.open(encoding='utf-8') as stream:
                for line in stream:
                    observed = snapshots_from_stream(line, kind) or observed
        if observed:
            evidence['quota_snapshots'] = [snap.to_dict() for snap in observed]
            if self.config.get('database_path'):
                try:
                    from core.database import WorkspaceDatabase
                    from core.quota import QuotaStore
                    database = Path(self.config['database_path'])
                    if not database.is_absolute():
                        database = self.instructions_root / database
                    QuotaStore(WorkspaceDatabase(database)).put(observed)
                except Exception as exc:
                    # Preserve successful work and the replayable snapshots in its
                    # receipt when the controller DB is temporarily unavailable.
                    evidence['quota_capture_error'] = type(exc).__name__
        if not success:
            # Failed runs only: successful output may legitimately discuss limits.
            tail = out if len(out) < 1500 else ''
            limit = opencode_limit or next((q for q in (LimitErrorDetector.detect(json.dumps(e)) for e in reversed(codex_errors)) if q), None)
            limit = limit or LimitErrorDetector.detect("\n".join([result['stderr'], cli_error or '', tail]))
            if limit:
                evidence['quota'] = {**limit, 'account': evidence['quota_account'], 'provider': name, 'model': model}
                cli_error = f"Provider quota exhausted ({limit['matched']}); " + (cli_error or 'no output')
        return AgentRunResult(role, name, success, out.strip(),
            exit_code=result["exit_code"], error=cli_error or hook_error, structured_data=evidence)

    @staticmethod
    def _post(url, payload, headers, timeout):
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", **headers}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=max(1, timeout)) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raise ModelHTTPError.from_http_error(exc) from None

    def _run_http(self, role, name, provider, cfg, instructions, prompt, timeout, log, model_override=None):
        from core.tools import AgentTools
        key_env = provider.get("api_key_env", "OPENROUTER_API_KEY")
        key = os.environ.get(key_env, "")
        if provider.get("require_api_key", True) and not key:
            raise ValueError(f"Missing {key_env}")
        model = self.resolve_model(role, provider, model_override)
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
        from core.configuration import role_turn_budget
        budget = role_turn_budget(self.config, role)
        nudged = False
        for turn in range(int(provider.get("max_tool_rounds", 24))):
            if schemas and turn >= budget and not nudged:
                # Soft budget: ask once for a wrap-up; round/token/time limits stay hard.
                messages.append({"role": "user", "content": "Soft turn budget reached. Finish the current step, "
                                 "save a checkpoint/handoff (completed actions, evidence, remaining work) and answer."})
                nudged = True
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
            except ModelHTTPError as exc:
                if exc.status not in (402, 429):
                    raise  # A later failure can follow side effects; do not automatically replay it.
                from core.quota import account_for
                account = account_for(provider, model) or ''
                delay = exc.retry_after if exc.retry_after is not None else 60.0
                data = {'tokens': token_total, 'tool_trace': trace, 'model': model, 'quota_account': account,
                        'http_status': exc.status, 'error_class': exc.error_class}
                if exc.error_class == 'QUOTA_EXHAUSTED':
                    data['quota'] = {'exhausted': True, 'matched': exc.safe_message[:80], 'account': account,
                                     'resets_at': time.time() + delay, 'provider': name, 'model': model}
                if not trace:
                    # Nothing ran yet: replay is safe after the advertised delay.
                    data.update(retry_safe=True, retry_after=max(15, min(3600, int(delay))))
                    return AgentRunResult(role, name, False, '', error='Provider rate limited before any tool call'
                                          if exc.status == 429 else 'Provider quota exhausted before any tool call',
                                          structured_data=data)
                # Tools already ran: checkpoint for reconciliation; never replay automatically.
                from core.role_memory import atomic_json
                checkpoint = self.workspace_root / '.workflow' / f'{role}-http-limit-handoff.json'
                atomic_json(checkpoint, {'reason': f'HTTP {exc.status} after tool calls', 'tool_trace': trace[-5:],
                                         'next_safe_action': 'Reconcile tool effects before continuing this step.'})
                data.update(handoff=str(checkpoint), reconcile_required=True)
                return AgentRunResult(role, name, False, '', error=f'Provider HTTP {exc.status} after tool calls; '
                                      'checkpoint saved, reconcile before retry', structured_data=data)
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
                return AgentRunResult(role, name, True, out, structured_data={"tokens": token_total, "tool_trace": trace,
                                      "turn_budget": budget, "turn_budget_exceeded": nudged})
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
        from core.role_memory import atomic_json
        checkpoint = self.workspace_root / '.workflow' / f'{role}-budget-handoff.json'
        atomic_json(checkpoint, {'reason': 'turn budget reached', 'tool_trace': trace[-5:]})
        return AgentRunResult(role, name, False, 'Turn budget reached; tool checkpoint saved.',
            structured_data={'budget_exhausted': True, 'handoff': str(checkpoint), 'tokens': token_total})
