import shlex
import shutil
import subprocess
import unittest

from core.integrations import hook_command


class ClaudeHookCommandTests(unittest.TestCase):
    def test_claude_command_survives_posix_shell_parsing(self):
        # Claude Code executes hooks through bash on Windows; backslashes would be eaten.
        command = hook_command("D:\\ctl dir", "D:\\ws\\task_00001", "claude", "SessionStart")
        self.assertNotIn("\\", command)
        argv = shlex.split(command)
        self.assertEqual(argv[argv.index("--workspace") + 1], "D:/ws/task_00001")
        self.assertTrue(argv[argv.index("--provider") + 1] == "claude")
        self.assertTrue(any(part.endswith("scripts/agent_hooks.py") for part in argv))

    def test_claude_command_is_executed_by_bash_unchanged(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash unavailable")
        command = hook_command("C:\\a b", "C:\\w", "claude", "Stop")
        out = subprocess.run([bash, "-c", f"set -- {command}; printf '%s\\n' \"$@\""],
                             capture_output=True, text=True).stdout.splitlines()
        self.assertIn("C:/w", out)

    def test_other_providers_keep_cmd_quoting(self):
        for provider in ("codex", "agy"):
            self.assertIn("--provider " + provider, hook_command("C:\\x", "C:\\w", provider, "Stop"))


if __name__ == "__main__":
    unittest.main()
