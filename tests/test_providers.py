import json
import os
import tempfile
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

    def test_agy_log_conversation_id_and_accept_edits_mode(self):
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
            self.assertIn("accept-edits", observed["argv"])
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
