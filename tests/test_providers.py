import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from core.providers import EphemeralAgentRunner
from core.tools import AgentTools


class ProviderTests(unittest.TestCase):
    def test_codex_home_is_shared_by_policy_check_and_child(self):
        for resumed in (False, True):
            with self.subTest(resumed=resumed), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                runner = EphemeralAgentRunner(root, {})
                runner.role_session = {"session_id": "existing-session"} if resumed else {}
                observed = {}

                def process(argv, cwd, **kwargs):
                    observed.update(argv=argv, env=kwargs["env"])
                    return {"exit_code": 0, "stdout": "probe", "stderr": ""}

                with patch.dict(os.environ, {"CODEX_SESSION_ID": "parent-session", "HOME": ""}), \
                     patch("pathlib.Path.home", return_value=root / "profile"), \
                     patch("core.providers.find_executable", return_value="codex.exe"), \
                     patch("core.providers.run_process", side_effect=process):
                    runner._run_cli("communicator", "codex", {"type": "codex_cli", "codex_home": "selected-home"},
                                    {}, "probe", 10, root / "probe.log")
                self.assertEqual(observed["env"]["CODEX_HOME"], str((root / "selected-home").resolve()))
                self.assertNotIn("CODEX_SESSION_ID", observed["env"])
                self.assertEqual(observed["env"]["HOME"], str(root / "profile"))
                self.assertEqual("resume" in observed["argv"], resumed)
                self.assertFalse(any("bypass-approvals-and-sandbox" in arg for arg in observed["argv"]))

    def test_codex_selected_home_hooks_are_not_bypassed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".workflow").mkdir()
            (root / ".workflow/integration.json").write_text("{}")
            selected = root / "selected-home"
            selected.mkdir()
            (selected / "hooks.json").write_text("{}")
            runner = EphemeralAgentRunner(root, {})
            with patch("core.providers.find_executable", return_value="codex.exe"), \
                 patch("core.integrations.verify_bundle", return_value={"skills": []}), \
                 patch("core.providers.run_process") as process:
                with self.assertRaisesRegex(ValueError, "Global Codex hooks"):
                    runner._run_cli("operator", "codex", {"type": "codex_cli", "codex_home": str(selected)},
                                    {}, "probe", 10, root / "probe.log")
                process.assert_not_called()

    def test_agy_log_conversation_id_and_communicator_plan_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_path = root / "agy.log"
            observed = {}

            def process(argv, cwd, **kwargs):
                observed["argv"] = argv
                log_path.write_text("I1104 00:09:02.803878 1 session.go:201] Print mode: conversation=48e70cb9-f244-485b-b74d-3fdf80d978e2, sending message\n", encoding="utf-8")
                return {"exit_code": 0, "stdout": "completed work", "stderr": ""}

            runner = EphemeralAgentRunner(root, {})
            with patch("core.providers.find_executable", return_value="agy.exe"), \
                 patch("core.providers.run_process", side_effect=process):
                result = runner._run_cli("communicator", "agy", {"type": "agy_cli", "mode": "auto"}, {}, "probe", 10, log_path)
            self.assertTrue(result.success)
            self.assertEqual(observed['argv'][observed['argv'].index('--mode') + 1], 'plan')
            self.assertEqual(result.structured_data.get("session_id"), "48e70cb9-f244-485b-b74d-3fdf80d978e2")

    def config(self):
        return {"providers": {"api": {"type": "http_llm", "base_url": "https://example.invalid/chat",
            "supports_tools": True, "require_api_key": False, "models": {"worker": "test-model"}}},
            "roles": {"operator": {"provider": "api", "tier": "worker"}}}

    def test_real_tool_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = EphemeralAgentRunner(tmp, self.config())
            calls = [{"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "write_file", "arguments": json.dumps({"path": "result.txt", "content": "42"})}}]}}]},
                {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "saved"}}]}]
            with patch.object(runner, "_post", side_effect=calls) as post:
                result = runner.run_role("operator", "save result")
                self.assertTrue(result.success, result.error)
                self.assertEqual((Path(tmp) / "result.txt").read_text(), "42")
                payload = post.call_args_list[1].args[1]
                self.assertEqual(payload["messages"][-1]["tool_call_id"], "a")
                self.assertIn("Task workspace", payload["messages"][1]["content"])
                self.assertIn("Run name", payload["messages"][1]["content"])

    def test_execution_rejects_text_only_api(self):
        cfg = self.config()
        cfg["providers"]["api"]["supports_tools"] = False
        with tempfile.TemporaryDirectory() as tmp:
            result = EphemeralAgentRunner(tmp, cfg).run_role("operator", "task")
            self.assertFalse(result.success)
            self.assertIn("supports_tools", result.error)

    def test_permissions_and_path_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            for role in ["researcher", "validator"]:
                with self.assertRaises(PermissionError):
                    AgentTools(tmp, role).call("write_file", {"path": "a", "content": "x"})
            for path in ["../outside", ".env", ".git/config"]:
                with self.assertRaises(PermissionError):
                    AgentTools(tmp, "operator").path(path)

    def test_missing_cli_is_actionable(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {"providers": {"missing": {"type": "codex_cli", "executable": "nonexistent-workflow-agent"}},
                   "roles": {"researcher": {"provider": "missing"}}}
            result = EphemeralAgentRunner(tmp, cfg).run_role("researcher", "task")
            self.assertFalse(result.success)
            self.assertIn("not installed", result.error)


class QuotaProviderTests(unittest.TestCase):
    def run_cli(self, kind, process, model_override=None, role_cfg=None, provider=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {"roles": {"communicator": {"model": "role-pin", "tier": "fast", **(role_cfg or {})}}}
            runner = EphemeralAgentRunner(root, config)
            provider = {"type": kind, **(provider or {})}
            with patch("core.providers.find_executable", return_value="cli.exe"), \
                 patch("core.providers.run_process", side_effect=process):
                return runner._run_cli("communicator", "p", provider, config["roles"]["communicator"], "probe", 10,
                                       root / "probe.log", model_override)

    def test_model_override_beats_role_pin_and_empty_means_tier_default(self):
        runner = EphemeralAgentRunner(".", {"roles": {"operator": {"model": "pinned", "tier": "worker"}}})
        provider = {"type": "claude_cli", "models": {"worker": "sonnet"}}
        self.assertEqual(runner.resolve_model("operator", provider), "pinned")
        self.assertEqual(runner.resolve_model("operator", provider, "opus"), "opus")
        self.assertEqual(runner.resolve_model("operator", provider, ""), "sonnet")

    def test_override_reaches_cli_command_and_run_role_identity(self):
        seen = {}

        def process(argv, cwd, **kwargs):
            seen["argv"] = argv
            return {"exit_code": 0, "stdout": json.dumps({"result": "done", "usage": {"input_tokens": 1}}), "stderr": ""}
        result = self.run_cli("claude_cli", process, "opus")
        self.assertTrue(result.success)
        self.assertEqual(seen["argv"][seen["argv"].index("--model") + 1], "opus")
        self.assertEqual((result.structured_data["model"], result.structured_data["quota_account"]), ("opus", "claude"))
        self.assertNotIn("quota", result.structured_data)
        self.run_cli("claude_cli", process, None)
        self.assertEqual(seen["argv"][seen["argv"].index("--model") + 1], "role-pin")

    def test_limit_failure_is_detected_with_reset_and_account(self):
        def process(argv, cwd, **kwargs):
            return {"exit_code": 1, "stdout": "", "stderr": "ERROR: You've hit your usage limit. Try again in 3 hours 10 minutes."}
        result = self.run_cli("codex_cli", process)
        self.assertFalse(result.success)
        quota = result.structured_data["quota"]
        self.assertEqual((quota["exhausted"], quota["account"], quota["provider"]), (True, "codex", "p"))
        self.assertAlmostEqual(quota["resets_at"] - time.time(), 3 * 3600 + 600, delta=30)
        self.assertIn("quota exhausted", result.error)

    def test_claude_error_result_text_is_detected_but_successful_discussion_is_not(self):
        limited = json.dumps({"is_error": True, "result": "Claude AI usage limit reached|1794081000"})
        result = self.run_cli("claude_cli", lambda a, c, **k: {"exit_code": 0, "stdout": limited, "stderr": ""})
        self.assertFalse(result.success)
        self.assertEqual(result.structured_data["quota"]["resets_at"], 1794081000.0)
        ok = json.dumps({"result": "Documented the rate limit handling and usage limit policy."})
        result = self.run_cli("claude_cli", lambda a, c, **k: {"exit_code": 0, "stdout": ok, "stderr": ""})
        self.assertTrue(result.success)
        self.assertNotIn("quota", result.structured_data)
        unrelated = self.run_cli("claude_cli", lambda a, c, **k: {"exit_code": 2, "stdout": "", "stderr": "segfault"})
        self.assertNotIn("quota", unrelated.structured_data)

    def test_stream_usage_events_become_snapshots(self):
        event = {"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {
            "five_hour": {"utilization": 0.5, "resetsAt": 1794081000, "status": "allowed"}}}}
        result = self.run_cli("claude_cli", lambda a, c, **k: {"exit_code": 0, "stdout": json.dumps(event) + "\n" + json.dumps({"result": "x"}), "stderr": ""})
        self.assertEqual([(s["account"], s["used_percent"]) for s in result.structured_data["quota_snapshots"]], [("claude", 50.0)])
        codex = json.dumps({"type": "token_count", "rate_limits": {"limit_id": "codex", "primary": {"used_percent": 7.0, "window_minutes": 300, "resets_at": 1794081000}}})
        result = self.run_cli("codex_cli", lambda a, c, **k: {"exit_code": 0, "stdout": codex, "stderr": ""})
        self.assertEqual(result.structured_data["quota_snapshots"][0]["used_percent"], 7.0)

    def test_codex_structured_limit_after_large_stdout_is_detected(self):
        error = {'type': 'turn.failed', 'error': {'type': 'usage_limit_reached',
                 'message': 'Usage limit reached', 'resets_at': 1800000900}}
        stdout = json.dumps({'type': 'item.completed', 'item': {'text': 'x' * 3000}}) + '\n' + json.dumps(error)
        result = self.run_cli('codex_cli', lambda a, c, **k: {'exit_code': 0, 'stdout': stdout, 'stderr': ''})
        self.assertFalse(result.success)
        self.assertEqual(result.structured_data['quota']['resets_at'], 1800000900)

    def test_claude_stream_result_usage_identity_and_permission_denials(self):
        def process(argv, cwd, **kwargs):
            self.assertEqual(argv[argv.index('--output-format') + 1], 'stream-json')
            self.assertIn('--verbose', argv)
            events = [{'type': 'system', 'subtype': 'init', 'session_id': 'native-id'},
                      {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'intermediate'}]}},
                      {'type': 'result', 'result': 'done', 'usage': {'input_tokens': 12},
                       'permission_denials': [{'tool_name': 'Bash'}]}]
            return {'exit_code': 0, 'stdout': '\n'.join(map(json.dumps, events)), 'stderr': ''}
        result = self.run_cli('claude_cli', process)
        self.assertFalse(result.success)
        self.assertEqual(result.output, 'done')
        self.assertEqual(result.structured_data['session_id'], 'native-id')
        self.assertEqual(result.structured_data['usage'], {'input_tokens': 12})
        self.assertIn('denied', result.error)

    def test_claude_passive_quota_before_tail_is_saved_without_routing(self):
        from core.database import WorkspaceDatabase
        from core.quota import QuotaStore, ClaudeCliProbe
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = {'database_path': 'quota.sqlite', 'quota_routing': {'enabled': False}}
            runner = EphemeralAgentRunner(root, cfg)
            event = {'type': 'rate_limit_event', 'rate_limit_info': {'unifiedWindows': {
                'five_hour': {'utilization': 0.31}, 'seven_day': {'utilization': 0.42}}}}
            final = {'type': 'result', 'session_id': 's', 'result': 'completed', 'usage': {'output_tokens': 2}}
            def process(argv, cwd, **kwargs):
                kwargs['stdout_path'].write_text(json.dumps(event) + '\n' +
                    json.dumps({'type': 'assistant', 'text': 'x' * 30000}) + '\n' + json.dumps(final), encoding='utf-8')
                return {'exit_code': 0, 'stdout': json.dumps(final), 'stderr': ''}
            with patch('core.providers.find_executable', return_value='claude.exe'), \
                 patch('core.providers.run_process', side_effect=process):
                result = runner._run_cli('communicator', 'c', {'type': 'claude_cli'}, {}, 'test', 10, root / 'test.log')
            self.assertTrue(result.success)
            self.assertEqual(result.output, 'completed')
            store = QuotaStore(WorkspaceDatabase(root / 'quota.sqlite'))
            self.assertEqual([s.used_percent for s in store.snapshots('claude')], [31, 42])
            probe = ClaudeCliProbe(root / 'cache.json', store=store)
            with patch('core.processes.run_process') as paid:
                self.assertEqual(len(probe.probe()), 2)
                paid.assert_not_called()

    def test_claude_metadata_without_final_result_cannot_succeed(self):
        result = self.run_cli('claude_cli', lambda a, c, **k: {
            'exit_code': 0, 'stdout': json.dumps({'type': 'system', 'session_id': 's'}), 'stderr': ''})
        self.assertFalse(result.success)

    def test_claude_public_runner_finishes_without_optional_telemetry_dependency(self):
        from core.models import AgentRunResult
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {'database_path': 'events.db', 'providers': {'c': {'type': 'claude_cli'}},
                   'roles': {'communicator': {'provider': 'c'}}}
            runner = EphemeralAgentRunner(tmp, cfg)
            with patch.object(runner, '_run_cli', return_value=AgentRunResult('communicator', 'c', True, 'ok')):
                result = runner.run_role('communicator', 'fixture only')
            self.assertTrue(result.success)
            self.assertEqual(result.output, 'ok')

    def test_run_role_passes_override_to_adapter_and_records_it_in_identity(self):
        from core.models import AgentRunResult
        with tempfile.TemporaryDirectory() as tmp:
            config = {"providers": {"c": {"type": "claude_cli", "models": {"worker": "sonnet"}}}, "roles": {"operator": {"provider": "c", "model": "pin"}}}
            runner = EphemeralAgentRunner(tmp, config)
            with patch.object(runner, "_run_role", return_value=AgentRunResult("operator", "c", True, "ok")) as inner:
                runner.run_role("operator", "task", provider_override="c", model_override="opus")
                self.assertEqual(inner.call_args.args[-1], "opus")
            first = runner.role_session["identity"]
            with patch.object(runner, "_run_role", return_value=AgentRunResult("operator", "c", True, "ok")):
                runner.run_role("operator", "task", provider_override="c", model_override="sonnet")
            self.assertNotEqual(runner.role_session["identity"], first)  # reroute rotates memory
