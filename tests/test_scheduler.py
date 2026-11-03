import concurrent.futures
import tempfile
import threading
import time
import unittest
from pathlib import Path
from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.orchestrator import HybridOrchestrator
from core.scheduler import TaskScheduler
from core.workspaces import WorkspaceManager
from test_workflow import FakeRunner


class SchedulerTests(unittest.TestCase):
    def test_cross_connection_claims_and_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "db.sqlite"
            db = WorkspaceDatabase(path)
            for i in range(5):
                db.create_task(f"task {i}")
            def claim(i):
                task = WorkspaceDatabase(path).claim_task(str(i), capacity=2)
                return task.id if task else None
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                ids = [x for x in pool.map(claim, range(5)) if x]
            self.assertEqual(len(ids), 2)
            self.assertEqual(len(set(ids)), 2)
            self.assertEqual(sum(not t.lease_owner for t in db.get_all_tasks()), 3)

    def test_expired_work_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = WorkspaceDatabase(Path(tmp) / "db.sqlite")
            t = db.create_task("remote job")
            db.claim_task("dead", lease_seconds=-1)
            self.assertIsNone(db.claim_task("new"))
            self.assertEqual(db.get_task(t.id).status, "ESCALATED")

    def test_queue_drains_without_unbounded_workers(self):
        class SlowRunner(FakeRunner):
            def __init__(self):
                super().__init__()
                self.lock = threading.Lock()
                self.active = self.peak = 0
                self.first_calls=0
                self.started_together=threading.Barrier(2)
            def run_role(self, role, **kwargs):
                with self.lock:
                    self.active += 1
                    self.peak = max(self.peak, self.active)
                    self.first_calls+=1
                    initial=self.first_calls<=2
                try:
                    if initial:self.started_together.wait(timeout=5)
                    time.sleep(0.025)
                    return super().run_role(role, **kwargs)
                finally:
                    with self.lock:
                        self.active -= 1
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = {"logs_dir": "logs", "isolation": {"enabled": False}, "orchestrator": {"max_concurrent_tasks": 2}}
            db, runner = WorkspaceDatabase(root / "db.sqlite"), SlowRunner()
            orch = HybridOrchestrator(root, cfg, db, ContextLogManager(root / "logs"), runner)
            tasks = [orch.submit_task(f"task {i}") for i in range(5)]
            scheduler = TaskScheduler(orch)
            scheduler.start()
            try:
                deadline = time.time() + 10
                while time.time() < deadline and any(db.get_task(t.id).status != "COMPLETED" for t in tasks):
                    time.sleep(0.025)
                self.assertTrue(all(db.get_task(t.id).status == "COMPLETED" for t in tasks))
                self.assertEqual(runner.peak, 2)
            finally:
                scheduler.stop()

    def test_snapshots_include_dirty_files_exclude_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "experiment.py").write_text("new uncommitted experiment")
            (root / ".env").write_text("SECRET=not-for-agent")
            (root / ".workflow").mkdir()
            (root / ".workflow/stale-job.json").write_text('{"state":"submitted"}')
            db = WorkspaceDatabase(root / "storage/events.db")
            manager = WorkspaceManager(root, {})
            one = manager.prepare(db.create_task("one"))
            two = manager.prepare(db.create_task("two"))
            self.assertFalse((one / ".env").exists())
            self.assertFalse((one / "storage").exists())
            self.assertFalse((one / ".workflow/stale-job.json").exists())
            (one / "experiment.py").write_text("changed")
            self.assertEqual((two / "experiment.py").read_text(), "new uncommitted experiment")
            self.assertEqual((root / "experiment.py").read_text(), "new uncommitted experiment")
