"""Pluggable Ephemeral Agent Provider Registry.

Decouples *Role Definitions* (Researcher, Operator, Validator, Communicator,
Workspace Watcher, World Watcher) from the *Agent Runtime* executing them.

Currently defaults to headless Antigravity CLI (`agy --print`) which requires
NO API key, and can be switched per-role in `config/workflow_config.json` to:
- `agy_cli`: Headless Antigravity CLI (`agy -p`)
- `http_llm`: Any OpenAI-compatible endpoint (OpenAI, Ollama, OpenRouter, Groq, vLLM)
- `gemini_http`: Google Gemini REST API
- `custom_cli`: Any external CLI tool or custom script
- `mock`: Fast local simulation for testing state transitions and webhooks
"""

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional
from core.models import AgentRunResult


class EphemeralAgentRunner:
    """Spawns an agent for a single task step, captures its output, and guarantees shutdown."""

    def __init__(self, workspace_root: Path, config: Dict[str, Any]) -> None:
        self.workspace_root = Path(workspace_root)
        self.config = config
        self.providers_cfg: Dict[str, Dict[str, Any]] = config.get("providers", {})
        self.roles_cfg: Dict[str, Dict[str, Any]] = config.get("roles", {})

    def run_role(
        self,
        role: str,
        task_prompt: str,
        cli_log_path: Optional[Path] = None,
        provider_override: Optional[str] = None,
    ) -> AgentRunResult:
        """Spawn the configured agent for `role`, execute `task_prompt`, and shut down."""
        role_cfg = self.roles_cfg.get(role, {})
        provider_name = provider_override or role_cfg.get("provider", "agy_headless_safe")
        provider_cfg = self.providers_cfg.get(provider_name)
        if not provider_cfg:
            return AgentRunResult(
                role=role,
                provider_name=provider_name,
                success=False,
                output="",
                exit_code=1,
                error=f"Unknown provider '{provider_name}' configured for role '{role}'.",
            )

        # Load role system instructions from `roles/<role>.md`
        prompt_file_rel = role_cfg.get("prompt_file", f"roles/{role}.md")
        prompt_file = self.workspace_root / prompt_file_rel
        role_instructions = ""
        if prompt_file.exists():
            role_instructions = prompt_file.read_text(encoding="utf-8").strip()

        full_prompt = (
            f"{role_instructions}\n\n"
            f"====================================================================\n"
            f"ASSIGNED EPHEMERAL TASK PAYLOAD\n"
            f"====================================================================\n"
            f"{task_prompt}\n"
        )

        timeout_seconds = int(role_cfg.get("timeout_seconds", 300))
        provider_type = provider_cfg.get("type", "agy_cli")

        start_time = time.monotonic()
        try:
            if provider_type == "agy_cli":
                res = self._run_agy_cli(
                    role=role,
                    provider_name=provider_name,
                    provider_cfg=provider_cfg,
                    role_cfg=role_cfg,
                    full_prompt=full_prompt,
                    timeout_seconds=timeout_seconds,
                    cli_log_path=cli_log_path,
                )
            elif provider_type == "http_llm":
                res = self._run_http_llm(
                    role=role,
                    provider_name=provider_name,
                    provider_cfg=provider_cfg,
                    role_cfg=role_cfg,
                    role_instructions=role_instructions,
                    task_prompt=task_prompt,
                    timeout_seconds=timeout_seconds,
                )
            elif provider_type == "gemini_http":
                res = self._run_gemini_http(
                    role=role,
                    provider_name=provider_name,
                    provider_cfg=provider_cfg,
                    role_cfg=role_cfg,
                    full_prompt=full_prompt,
                    timeout_seconds=timeout_seconds,
                )
            elif provider_type == "custom_cli":
                res = self._run_custom_cli(
                    role=role,
                    provider_name=provider_name,
                    provider_cfg=provider_cfg,
                    full_prompt=full_prompt,
                    timeout_seconds=timeout_seconds,
                    cli_log_path=cli_log_path,
                )
            elif provider_type == "mock":
                res = self._run_mock(role=role, provider_name=provider_name, task_prompt=task_prompt)
            else:
                res = AgentRunResult(
                    role=role,
                    provider_name=provider_name,
                    success=False,
                    output="",
                    exit_code=1,
                    error=f"Unsupported provider type '{provider_type}'.",
                )
        except Exception as exc:
            res = AgentRunResult(
                role=role,
                provider_name=provider_name,
                success=False,
                output="",
                exit_code=1,
                error=f"Unhandled provider exception: {exc}",
            )

        res.duration_seconds = time.monotonic() - start_time
        if cli_log_path:
            res.log_file = str(cli_log_path)
        return res

    def _run_agy_cli(
        self,
        role: str,
        provider_name: str,
        provider_cfg: Dict[str, Any],
        role_cfg: Dict[str, Any],
        full_prompt: str,
        timeout_seconds: int,
        cli_log_path: Optional[Path],
    ) -> AgentRunResult:
        """Execute headless Antigravity CLI (`agy --print`) and shut down on completion."""
        exe_name = provider_cfg.get("executable", "agy")
        exe_path = shutil.which(exe_name) or exe_name

        cmd = [exe_path]

        mode = provider_cfg.get("mode", "plan")
        if mode:
            cmd.extend(["--mode", mode])

        output_format = provider_cfg.get("output_format", "text")
        if output_format:
            cmd.extend(["--output-format", output_format])

        if provider_cfg.get("skip_permissions", False):
            cmd.append("--dangerously-skip-permissions")

        model = role_cfg.get("model") or provider_cfg.get("model")
        if model:
            cmd.extend(["--model", str(model)])

        effort = role_cfg.get("effort") or provider_cfg.get("effort")
        if effort:
            cmd.extend(["--effort", str(effort)])

        if cli_log_path:
            cli_log_path.parent.mkdir(parents=True, exist_ok=True)
            cmd.extend(["--log-file", str(cli_log_path)])

        for extra in provider_cfg.get("extra_args", []):
            cmd.append(str(extra))

        # Windows command-line length limit is 32,767 chars; keep CLI flag short and pipe via stdin if needed,
        # or pass directly via -p when within safe limits.
        if len(full_prompt) < 12000:
            cmd.extend(["-p", full_prompt])
            stdin_payload = None
        else:
            prompt_file = (cli_log_path or (self.workspace_root / "storage/logs/temp")).with_suffix(".prompt.md")
            prompt_file.parent.mkdir(parents=True, exist_ok=True)
            prompt_file.write_text(full_prompt, encoding="utf-8")
            short_pointer = (
                f"Read the complete task instructions and context from `{prompt_file}` "
                f"and execute your `{role}` role."
            )
            cmd.extend(["-p", short_pointer])
            stdin_payload = None

        proc = subprocess.Popen(
            cmd,
            cwd=str(self.workspace_root),
            stdin=subprocess.PIPE if stdin_payload else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            stdout, stderr = proc.communicate(input=stdin_payload, timeout=timeout_seconds)
            exit_code = proc.returncode or 0
            success = exit_code == 0 and bool(stdout.strip())
            return AgentRunResult(
                role=role,
                provider_name=provider_name,
                success=success,
                output=stdout.strip(),
                exit_code=exit_code,
                error=stderr.strip() if exit_code != 0 else None,
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return AgentRunResult(
                role=role,
                provider_name=provider_name,
                success=False,
                output="",
                exit_code=-1,
                error=f"Ephemeral agent '{role}' timed out after {timeout_seconds}s and was forcefully shut down.",
            )

    def _run_http_llm(
        self,
        role: str,
        provider_name: str,
        provider_cfg: Dict[str, Any],
        role_cfg: Dict[str, Any],
        role_instructions: str,
        task_prompt: str,
        timeout_seconds: int,
    ) -> AgentRunResult:
        """Execute a single call against an OpenAI-compatible Chat Completions endpoint."""
        base_url = provider_cfg.get("base_url", "https://api.openai.com/v1/chat/completions")
        api_key_env = provider_cfg.get("api_key_env", "OPENAI_API_KEY")
        api_key = os.environ.get(api_key_env, "")
        model = role_cfg.get("model") or provider_cfg.get("default_model", "gpt-4o-mini")

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": role_instructions},
                {"role": "user", "content": task_prompt},
            ],
        }
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        req = urllib.request.Request(
            base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"]["content"]
                return AgentRunResult(
                    role=role,
                    provider_name=provider_name,
                    success=True,
                    output=content,
                    exit_code=0,
                )
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", errors="replace")
            return AgentRunResult(
                role=role,
                provider_name=provider_name,
                success=False,
                output="",
                exit_code=err.code,
                error=f"HTTP {err.code}: {body}",
            )

    def _run_gemini_http(
        self,
        role: str,
        provider_name: str,
        provider_cfg: Dict[str, Any],
        role_cfg: Dict[str, Any],
        full_prompt: str,
        timeout_seconds: int,
    ) -> AgentRunResult:
        """Execute a single stateless request against the Gemini REST API."""
        api_key_env = provider_cfg.get("api_key_env", "GEMINI_API_KEY")
        api_key = os.environ.get(api_key_env, "")
        if not api_key:
            return AgentRunResult(
                role=role,
                provider_name=provider_name,
                success=False,
                output="",
                exit_code=1,
                error=f"Missing environment variable '{api_key_env}' for Gemini REST provider.",
            )
        model = role_cfg.get("model") or provider_cfg.get("default_model", "gemini-2.5-flash")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {"contents": [{"parts": [{"text": full_prompt}]}]}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            text = "\n".join(p.get("text", "") for p in parts)
            return AgentRunResult(
                role=role,
                provider_name=provider_name,
                success=bool(text.strip()),
                output=text.strip(),
                exit_code=0,
            )

    def _run_custom_cli(
        self,
        role: str,
        provider_name: str,
        provider_cfg: Dict[str, Any],
        full_prompt: str,
        timeout_seconds: int,
        cli_log_path: Optional[Path],
    ) -> AgentRunResult:
        """Run any custom CLI agent template and shut it down once done."""
        template = provider_cfg.get("command_template", [])
        cmd = [
            part.format(
                prompt=full_prompt,
                role=role,
                log_file=str(cli_log_path or ""),
            )
            for part in template
        ]
        proc = subprocess.Popen(
            cmd,
            cwd=str(self.workspace_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout_seconds)
            code = proc.returncode or 0
            return AgentRunResult(
                role=role,
                provider_name=provider_name,
                success=(code == 0),
                output=stdout.strip(),
                exit_code=code,
                error=stderr.strip() if code != 0 else None,
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return AgentRunResult(
                role=role,
                provider_name=provider_name,
                success=False,
                output="",
                exit_code=-1,
                error=f"Custom CLI agent timed out after {timeout_seconds}s.",
            )

    @staticmethod
    def _run_mock(role: str, provider_name: str, task_prompt: str) -> AgentRunResult:
        """Fast deterministic mock agent for testing the state-machine and logging loop."""
        first_line = task_prompt.strip().splitlines()[0] if task_prompt.strip() else "Task"
        if role == "validator":
            out = (
                "VERDICT: PASS\n"
                "SUMMARY: Validated operator execution against task requirements. All checks passed."
            )
        elif role == "communicator":
            out = (
                "✅ **Workspace Task Completed**\n"
                "The autonomous pipeline (`researcher` → `operator` → `validator` → `communicator`) "
                "has processed and verified your request. Full context is saved in the task log."
            )
        else:
            out = f"[Mock `{role}` output] Executed assigned step for context: {first_line[:120]}"
        return AgentRunResult(
            role=role,
            provider_name=provider_name,
            success=True,
            output=out,
            exit_code=0,
        )
