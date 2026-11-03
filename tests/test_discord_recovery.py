import tempfile
import unittest
import asyncio
import os
from unittest.mock import patch
from pathlib import Path

from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.orchestrator import HybridOrchestrator
from gateways.discord_bot import is_approval_reply


class DiscordRecoveryTests(unittest.TestCase):
    def test_production_handlers_can_be_built_without_connecting(self):
        from gateways.discord_bot import DiscordBotRunner
        with patch.dict(os.environ, {"DISCORD_ALLOWED_USER_IDS": "123"}):
            bot = DiscordBotRunner("not-a-real-token", None, None).create_bot()
            try:
                self.assertEqual({"task", "status", "retry", "help"}, {c.name for c in bot.commands})
            finally:
                asyncio.run(bot.close())
    def test_provider_choice_survives_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = WorkspaceDatabase(root / "events.db")
            orch = HybridOrchestrator(root, {"providers": {"codex_cli": {}}}, db, ContextLogManager(root / "logs"), None)
            task = orch.submit_task("use provider codex, print the time")
            self.assertEqual(task.provider_override, "codex_cli")
            retry = orch.submit_task(f"retry task {task.id}")
            self.assertEqual(retry.provider_override, "codex_cli")

    def test_approval_tokens_never_become_tasks(self):
        for content in ("approve 7f17669e89f1", "!approve 7f17669e89f1", "deny", "yes"):
            self.assertTrue(is_approval_reply(content))
        self.assertFalse(is_approval_reply("approve the experiment design"))

    def test_retry_resolves_request_preserves_history_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = WorkspaceDatabase(root / "events.db")
            orch = HybridOrchestrator(root, {}, db, ContextLogManager(root / "logs"), None)
            original = orch.submit_task("Print current time on Colab T4", source_channel="discord", source_user="channel1")
            db.update_task(original.id, status="ESCALATED", validation_feedback="old hook failure")
            retry = orch.submit_task(f"reopen task{original.id}.", source_channel="discord", source_user="channel1", event_key="discord:123")
            self.assertEqual(retry.parent_task_id, original.id)
            self.assertIn(original.user_prompt, retry.user_prompt)
            self.assertIn("old hook failure", retry.user_prompt)
            self.assertEqual(db.get_task(original.id).status, "ESCALATED")
            duplicate = orch.submit_task(f"retry task {original.id}", source_channel="discord", source_user="channel1", event_key="discord:123")
            self.assertEqual(duplicate.id, retry.id)
            with self.assertRaisesRegex(ValueError, "channel"):
                orch.submit_task(f"retry task {original.id}", source_channel="discord", source_user="other")
