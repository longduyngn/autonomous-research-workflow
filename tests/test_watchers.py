import tempfile
import unittest
from pathlib import Path
from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.models import AgentRunResult
from watchers.world_watcher import WorldWatcherDaemon
from watchers.workspace_watcher import WorkspaceWatcherDaemon


class WatcherTests(unittest.TestCase):
    def test_failed_world_item_retries_then_deduplicates(self):
        class Runner:
            calls = 0
            def run_role(self, role, **kwargs):
                self.calls += 1
                return AgentRunResult(role, "fake", self.calls > 1, "ACTION: IGNORE")
        with tempfile.TemporaryDirectory() as tmp:
            root, runner = Path(tmp), Runner()
            db = WorkspaceDatabase(root / "storage/db.sqlite")
            watcher = WorldWatcherDaemon(root, {}, db, ContextLogManager(root / "storage/logs"), runner)
            item = {"source": "test", "title": "one", "url": "https://example.invalid/one"}
            watcher.check_once(force_items=[dict(item)])
            watcher.check_once(force_items=[dict(item)])
            watcher.check_once(force_items=[dict(item)])
            self.assertEqual(runner.calls, 2)

    def test_world_task_creation_is_idempotent_when_enabled(self):
        class Runner:
            def run_role(self, role, **kwargs):
                return AgentRunResult(role, "fake", True, "ACTION: CREATE_TASK\nInvestigate the update")
        with tempfile.TemporaryDirectory() as tmp:
            root, created = Path(tmp), []
            cfg = {"watchers": {"world": {"allow_create_tasks": True}}}
            watcher = WorldWatcherDaemon(root, cfg, WorkspaceDatabase(root / "storage/db.sqlite"),
                ContextLogManager(root / "storage/logs"), Runner(), task_spawn_callback=lambda p,a: created.append((p,a)))
            item = {"source": "test", "title": "one", "url": "https://example.invalid/one"}
            watcher.check_once(force_items=[dict(item)])
            watcher.check_once(force_items=[dict(item)])
            self.assertEqual(len(created), 1)
            self.assertTrue(created[0][1].startswith("world:"))

    def test_snapshot_prunes_environment_and_healthy_heartbeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".conda").mkdir()
            (root / ".conda/ignored.py").write_text("ignore")
            (root / "experiment.py").write_text("experiment")
            db = WorkspaceDatabase(root / "storage/db.sqlite")
            watcher = WorkspaceWatcherDaemon(root, {}, db, ContextLogManager(root / "storage/logs"), None)
            self.assertIn("experiment.py", watcher._file_snapshot)
            self.assertFalse(any("ignored.py" in p for p in watcher._file_snapshot))
