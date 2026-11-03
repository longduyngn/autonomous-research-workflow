import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from core.integrations import prepare_integrations, verify_bundle, toml_value
from scripts.agent_hooks import handle

ROOT = Path(__file__).resolve().parent.parent


class IntegrationTests(unittest.TestCase):
    def test_hook_cli_overrides_are_valid_toml(self):
        import tomllib
        value = [{"hooks": [{"type": "command", "command": '"C:\\Path With Spaces\\python.exe" hook.py', "timeout": 10}]}]
        self.assertEqual(tomllib.loads("hooks.SessionStart=" + toml_value(value))["hooks"]["SessionStart"], value)

    def prepare(self, root):
        prepare_integrations(ROOT, root, root)
        return verify_bundle(root, ROOT)

    def test_bundle_copies_skills_scripts_and_native_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".agents").mkdir()
            manifest = self.prepare(root)
            self.assertIn("colab-coccoc-playwright", manifest["skills"])
            self.assertIn("kaggle-playwright-cancel", manifest["skills"])
            self.assertIn("workflow-artifacts", manifest["skills"])
            script = root / ".agents/skills/colab-coccoc-playwright/scripts/colab_playwright.py"
            self.assertIn("WORKFLOW_CONTROLLER_ROOT", script.read_text(encoding="utf-8"))
            self.assertTrue((root / ".claude/skills/workflow-artifacts/SKILL.md").exists())
            hooks = json.loads((root / ".workflow/codex-hooks.json").read_text())
            self.assertIn("PreToolUse", hooks["hooks"])

    def test_modified_hook_definition_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".agents").mkdir()
            self.prepare(root)
            (root / ".workflow/codex-hooks.json").write_text('{}')
            with self.assertRaisesRegex(ValueError, "changed"):
                verify_bundle(root, ROOT)

    def test_native_protocol_and_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".agents").mkdir()
            self.prepare(root)
            result = handle("codex", "SessionStart", {}, root)
            self.assertEqual(result["hookSpecificOutput"]["hookEventName"], "SessionStart")
            denied = handle("codex", "PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "Remove-Item data -Recurse"}}, root)
            self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
            observed = handle("claude", "PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "python check_results.py"}}, root)
            self.assertEqual(observed, {})  # Do not grant permissions on the CLI's behalf.
            self.assertEqual(handle("agy", "PreToolUse", {"toolCall": {"name": "read_file", "args": {"path": "README.md"}}}, root), {"decision": "allow"})
            self.assertEqual(handle("agy", "PostToolUse", {"toolCall": {"name": "read_file", "args": {}}}, root), {})
            records = (root / ".workflow/hook-events.jsonl").read_text().splitlines()
            self.assertEqual(len(records), 5)

    def test_bundled_artifact_skill_runs(self):
        from core.processes import run_process
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".agents").mkdir()
            self.prepare(root)
            (root / "result.json").write_text('{"actual":42}')
            result = run_process([sys.executable, str(root / ".agents/skills/workflow-artifacts/verify.py"), "result.json"], root)
            self.assertEqual(result["exit_code"], 0, result["stderr"])
            self.assertEqual(json.loads(result["stdout"])["bytes"], 13)

    def test_cli_exit_zero_without_native_hooks_is_failure(self):
        from core.providers import EphemeralAgentRunner
        from core.processes import run_process as actual_process
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".agents").mkdir()
            self.prepare(root)
            cfg = {"providers": {"test": {"type": "codex_cli", "executable": "codex"}},
                   "roles": {"operator": {"provider": "test"}}}
            runner = EphemeralAgentRunner(root, cfg)
            runner.instructions_root = ROOT
            observed = []
            def process(argv, cwd, **kwargs):
                if argv[0] == sys.executable:
                    return actual_process(argv, cwd, **kwargs)
                observed.extend(argv)
                return {"exit_code": 0, "stdout": "Claimed success without native hooks", "stderr": ""}
            with patch("core.providers.find_executable", return_value="codex.exe"), patch("core.providers.run_process", side_effect=process), patch("pathlib.Path.home", return_value=root / "profile"):
                result = runner.run_role("operator", "task")
            self.assertFalse(result.success)
            self.assertIn("SessionStart", result.error)
            self.assertIn("--approve-for-me", observed)
            self.assertFalse(any("bypass-approvals-and-sandbox" in item for item in observed))
            self.assertTrue(any(item.startswith("hooks.SessionStart=") for item in observed))

    def test_cloud_hooks_entrypoint_works_from_repo_and_agents_cwd_without_side_effects(self):
        from core.processes import run_process
        import sys
        script = str(ROOT / "scripts/cloud_hooks.py")
        payload = json.dumps({"toolCall": {"name": "read_file", "args": {"path": "README.md"}}})
        for cwd in (ROOT, ROOT / ".agents"):
            proc = run_process(
                [sys.executable, "-c",
                 "import json, sys; sys.path.insert(0, sys.argv[1]); import scripts.cloud_hooks as ch; "
                 "print(json.dumps(ch.handle_pre_tool_use(json.loads(sys.argv[2]))))",
                 str(ROOT), payload],
                cwd,
                env={**os.environ, "WORKFLOW_NO_ENV": "1"},
            )
            self.assertEqual(proc["exit_code"], 0, proc["stderr"])
            self.assertEqual(json.loads(proc["stdout"]), {"decision": "allow"})
            self.assertTrue(Path(script).is_file())
