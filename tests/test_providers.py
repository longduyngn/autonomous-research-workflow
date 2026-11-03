import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from core.providers import EphemeralAgentRunner
from core.tools import AgentTools


class ProviderTests(unittest.TestCase):
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
