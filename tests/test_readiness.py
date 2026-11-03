import asyncio
import io
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from core.configuration import validate_config
from core.dashboard import make_server
from core.database import WorkspaceDatabase
from core.integrations import prepare_integrations
from core.logger import ContextLogManager
from core.models import AgentRunResult
from core.observability import task_detail, run_log
from core.orchestrator import HybridOrchestrator
from core.processes import run_process
from core.providers import EphemeralAgentRunner
from gateways.discord_bot import save_attachments
from test_workflow import FakeRunner

ROOT = Path(__file__).resolve().parent.parent


class ReadinessTests(unittest.TestCase):
    def test_slow_delivery_does_not_block_task_dispatch(self):
        from core.scheduler import TaskScheduler
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = WorkspaceDatabase(root / "events.db")
            orch = HybridOrchestrator(root, {"isolation": {"enabled": False}}, db,
                                      ContextLogManager(root / "logs"), FakeRunner())
            task = orch.submit_task("Local work")
            entered, release = threading.Event(), threading.Event()
            def deliver():
                entered.set()
                release.wait(5)
            orch.delivery_tick = deliver
            scheduler = TaskScheduler(orch)
            try:
                scheduler.tick()
                self.assertTrue(entered.wait(1))
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline and db.get_task(task.id).status != "COMPLETED":
                    time.sleep(0.02)
                self.assertEqual(db.get_task(task.id).status, "COMPLETED")
                self.assertFalse(release.is_set())
            finally:
                release.set()
                scheduler.stop()
    def test_api_researcher_can_read_pdf_pages_and_continue_long_text(self):
        from pypdf import PdfWriter
        from core.tools import AgentTools
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            writer = PdfWriter()
            for _ in range(3):
                writer.add_blank_page(width=100, height=100)
            writer.write(root / "paper.pdf")
            tools = AgentTools(root, "researcher")
            first = tools.call("read_document", {"path": "paper.pdf", "page_count": 2})
            self.assertEqual((len(first["pages"]), first["total_pages"], first["next_start_page"]), (2, 3, 2))
            (root / "long.txt").write_text("a" * 24000 + "end")
            first = tools.call("read_file", {"path": "long.txt"})
            self.assertEqual(first["next_start_char"], 24000)
            self.assertEqual(tools.call("read_file", {"path": "long.txt", "start_char": 24000})["text"], "end")
    def test_interrupted_input_preparation_is_never_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = WorkspaceDatabase(Path(tmp) / "events.db")
            task = db.create_task("File-dependent task", ready=False)
            with db._connect() as connection:
                connection.execute("UPDATE tasks SET updated_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", task.id))
            self.assertIsNone(db.claim_task("worker"))
            self.assertEqual(db.get_task(task.id).status, "ESCALATED")
            self.assertIn("preparation", db.get_task(task.id).validation_feedback)
    def test_queued_provider_choice_survives_a_different_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = {"providers": {"one": {}, "two": {}}, "isolation": {"enabled": False}}
            db = WorkspaceDatabase(root / "events.db")
            submitter = HybridOrchestrator(root, cfg, db, ContextLogManager(root / "logs"), None, provider_override="one")
            task = submitter.submit_task("Use the chosen provider")
            worker = HybridOrchestrator(root, cfg, db, ContextLogManager(root / "logs"), FakeRunner(), provider_override="two")
            self.assertEqual(worker.run_task_to_completion(task.id).status, "COMPLETED")
            with db._connect() as c:
                self.assertEqual({r[0] for r in c.execute("SELECT provider_name FROM agent_runs")}, {"one"})

    def test_replanning_is_bounded_and_preserves_checks(self):
        class Runner(FakeRunner):
            def run_role(self, role, **kwargs):
                self.calls.append(role)
                if role == "researcher":
                    return AgentRunResult(role, "fake", True, '```research_plan\n{"mode":"direct","checks":[]}\n```')
                if role == "validator":
                    return AgentRunResult(role, "fake", True, "VERDICT: FAIL\nEvidence: implementation incorrect")
                return AgentRunResult(role, "fake", True, "done")
        with tempfile.TemporaryDirectory() as tmp:
            root, runner = Path(tmp), Runner()
            db = WorkspaceDatabase(root / "events.db")
            orch = HybridOrchestrator(root, {"isolation": {"enabled": False}}, db, ContextLogManager(root / "logs"), runner)
            task = orch.run_task_to_completion(orch.submit_task("Hard task").id)
            self.assertEqual((task.status, task.replan_count, task.retry_count), ("ESCALATED", 1, 3))
            self.assertEqual(runner.calls.count("researcher"), 2)
            self.assertEqual(runner.calls.count("operator"), 3)
            self.assertTrue((root / ".workflow/plans/revision_01.json").exists())

    def test_replan_cannot_drop_verified_prefix_or_acceptance_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = WorkspaceDatabase(root / "events.db")
            old = {"mode": "steps", "backend": "local", "steps": [
                {"instructions": "verified"}, {"instructions": "remaining", "checks": [{"argv": ["python", "old.py"]}]}],
                "checks": [{"argv": ["python", "final.py"]}]}
            new = {"mode": "steps", "steps": [{"instructions": "verified"}, {"instructions": "new strategy"}]}
            runner = FakeRunner(plan="```research_plan\n" + json.dumps(new) + "\n```")
            orch = HybridOrchestrator(root, {}, db, ContextLogManager(root / "logs"), runner)
            task = orch.submit_task("Research")
            task = db.update_task(task.id, replan_count=1, retry_count=2, step_index=1, plan_json=json.dumps(old))
            orch._execute_researcher(task)
            actual = json.loads(db.get_task(task.id).plan_json)
            self.assertEqual(actual["checks"], old["checks"])
            self.assertEqual(actual["steps"][1]["checks"], old["steps"][1]["checks"])
            new["steps"][0]["instructions"] = "changed verified work"
            runner.plan = "```research_plan\n" + json.dumps(new) + "\n```"
            orch._execute_researcher(task)
            self.assertIn("preserve", db.get_task(task.id).validation_feedback)

    def test_full_stream_is_visible_before_process_finishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "live.log"
            result = []
            thread = threading.Thread(target=lambda: result.append(run_process(
                [sys.executable, "-u", "-c", "import time; print('START'); time.sleep(1); print('x'*40000)"],
                root, stdout_path=output, stderr_path=root / "err.log")))
            thread.start()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and (not output.exists() or "START" not in output.read_text()):
                time.sleep(0.02)
            self.assertTrue(thread.is_alive())
            self.assertIn("START", output.read_text())
            thread.join(10)
            self.assertEqual(result[0]["exit_code"], 0)
            self.assertGreater(output.stat().st_size, 40000)
            self.assertLessEqual(len(result[0]["stdout"]), 24000)

    def test_input_snapshot_and_retry_preserve_supplied_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supplied = root / "given.json"
            supplied.write_text('{"value": 42}')
            db = WorkspaceDatabase(root / "storage/events.db")
            orch = HybridOrchestrator(root, {}, db, ContextLogManager(root / "storage/logs"), FakeRunner())
            task = orch.submit_task("Use inputs/given.json", input_files=[supplied])
            supplied.write_text("changed original")
            final = orch.run_task_to_completion(task.id)
            self.assertEqual((Path(final.workspace_path) / "inputs/given.json").read_text(), '{"value": 42}')
            retry = orch.submit_task(f"retry task {task.id}")
            self.assertEqual(json.loads(retry.inputs_json)[0]["sha256"], json.loads(task.inputs_json)[0]["sha256"])

    def test_source_snapshot_excludes_archive_and_keeps_controller_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            (project / "train.py").write_text("experiment")
            (project / "archive").mkdir()
            (project / "archive/old.txt").write_text("old")
            db = WorkspaceDatabase(root / "storage/events.db")
            orch = HybridOrchestrator(root, {}, db, ContextLogManager(root / "storage/logs"), FakeRunner())
            final = orch.run_task_to_completion(orch.submit_task("Inspect project", source_path=str(project)).id)
            workspace = Path(final.workspace_path)
            self.assertTrue((workspace / "train.py").exists())
            self.assertFalse((workspace / "archive").exists())
            self.assertTrue((workspace / ".workflow/integration.json").exists())

    def test_attachment_rejects_credential_name_without_reading_it(self):
        class Attachment:
            filename, size = ".env", 10
            async def read(self):
                raise AssertionError("must not read credentials")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                asyncio.run(save_attachments([Attachment()], tmp))

    def test_dashboard_reads_named_runs_and_blocks_mutations_and_foreign_hosts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT / "ui", root / "ui")
            db = WorkspaceDatabase(root / "storage/events.db")
            task = db.create_task("<script>unsafe HTML</script>")
            log = root / "storage/test.cli.log"
            log.with_suffix(".stdout.log").write_text("real output")
            run = db.record_agent_spawn("operator", "test", task.id, str(log), 0, 2, "task-00001/step-01/operator/attempt-02")
            server = make_server(db, root, 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}"
            try:
                with urllib.request.urlopen(url + f"/api/tasks/{task.id}") as r:
                    data = json.load(r)
                self.assertEqual(data["runs"][0]["attempt"], 2)
                self.assertEqual(data["runs"][0]["run_label"], "task-00001/step-01/operator/attempt-02")
                with urllib.request.urlopen(url + f"/api/runs/{run}/log?kind=stdout") as r:
                    self.assertEqual(json.load(r)["text"], "real output")
                for request, code in [
                    (urllib.request.Request(url + "/api/tasks", data=b"{}"), 405),
                    (urllib.request.Request(url + "/api/tasks", headers={"Host": "evil.example"}), 403),
                    (urllib.request.Request(url + "/api/tasks", headers={"Origin": "https://evil.example"}), 403),
                    (urllib.request.Request(url + f"/api/runs/{run}/log?kind=../../.env"), 404),
                ]:
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(request)
                    self.assertEqual(error.exception.code, code)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)

    def test_dashboard_exposes_waits_approvals_revisions_and_ui_assets(self):
        from core.control import ControlPlane
        from core.conversation import Conversation
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT / "ui", root / "ui")
            db = WorkspaceDatabase(root / "storage/events.db")
            cp = ControlPlane(db)
            chat = Conversation(db, lambda *a, **kw: db.create_task("queued"))
            task = db.create_task("Investigate long-horizon convergence")
            db.update_task(
                task.id,
                plan_revision=2,
                feedback_revision=0,
                plan_json=json.dumps({"mode": "steps", "steps": [{"title": "Step 1", "instructions": "smoke"}, {"title": "Step 2", "instructions": "full"}]}),
                step_index=1,
            )
            with db._connect() as c:
                c.execute("INSERT OR IGNORE INTO task_actors VALUES(?,?,?)", (task.id, "owner", "chan"))
            chat.receive(f"!feedback {task.id} check learning rate warmup", "chan", "owner", "ev-1")
            chat.apply_feedback(task.id)
            approval_id = cp.request_approval(task.id, "owner", {"tool": "Bash", "command": "python run.py"})
            cp.wait(task.id, "APPROVAL", "OPERATING", "Exact action needs owner decision", approval_id, timeout=3600)
            server = make_server(db, root, 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}"
            try:
                with urllib.request.urlopen(url + "/") as r:
                    html = r.read().decode("utf-8")
                self.assertIn('<script src="/app.js" defer></script>', html)
                with urllib.request.urlopen(url + "/app.js") as r:
                    js = r.read().decode("utf-8")
                self.assertIn("Wait dependency and deadline", js)
                self.assertIn("Exact action approvals (read only)", js)
                self.assertIn("User feedback revisions", js)
                with urllib.request.urlopen(url + "/style.css") as r:
                    css = r.read().decode("utf-8")
                self.assertIn("@media (max-width: 850px)", css)
                self.assertIn("overflow-wrap: anywhere", css)
                with urllib.request.urlopen(url + f"/api/tasks/{task.id}") as r:
                    detail = json.load(r)
                self.assertEqual(detail["status"], "WAITING_APPROVAL")
                self.assertEqual(detail["plan_revision"], 2)
                self.assertEqual(detail["feedback_revision"], 1)
                self.assertEqual(detail["wait"]["dependency"], approval_id)
                self.assertEqual(detail["approvals"][0]["id"], approval_id)
                self.assertEqual(detail["user_feedback"][0]["message"], "check learning rate warmup")
                self.assertEqual(detail["user_feedback"][0]["applied"], 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)

    def test_opencode_json_output_and_role_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_integrations(ROOT, root, root)
            config = {"providers": {"oc": {"type": "opencode_cli", "models": {"worker": "openrouter/test:free"}}},
                      "roles": {"validator": {"provider": "oc", "tier": "worker"}}}
            runner = EphemeralAgentRunner(root, config)
            runner.instructions_root = ROOT
            runner.run_label = "task-00001/step-01/validator/attempt-01"
            events = [{"type": "text", "sessionID": "session-test", "part": {"text": "VERDICT: PASS\nEvidence: inspected"}}]
            output = "\n".join(json.dumps(e) for e in events)
            with patch("core.providers.find_executable", return_value="opencode.exe"), patch("core.providers.run_process", return_value={"exit_code": 0, "stdout": output, "stderr": ""}) as process:
                result = runner.run_role("validator", "Check it")
            self.assertTrue(result.success, result.error)
            self.assertEqual(result.structured_data["session_id"], "session-test")
            self.assertEqual(result.structured_data["integration_mode"], "controller_observed")
            permissions = json.loads(process.call_args.kwargs["env"]["OPENCODE_PERMISSION"])
            self.assertEqual((permissions["bash"], permissions["edit"], permissions["task"]), ("allow", "deny", "deny"))
            self.assertIn(runner.run_label, process.call_args.args[0])

    def test_config_rejects_invalid_concurrency_and_text_only_workers(self):
        for value in (0, 4, True, 1.5):
            with self.assertRaises(ValueError):
                validate_config({"orchestrator": {"max_concurrent_tasks": value}})
        with self.assertRaises(ValueError):
            validate_config({"roles": {"operator": {"provider": "plain"}}, "providers": {"plain": {"type": "gemini_http"}}})
