import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.orchestrator import HybridOrchestrator
from core.providers import EphemeralAgentRunner
from core.processes import run_process
from test_workflow import FakeRunner


class ExecutionTests(unittest.TestCase):
    def test_failing_check_cannot_be_overridden_by_model_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = WorkspaceDatabase(root / "storage/db.sqlite")
            plan = {"mode": "direct", "checks": [{"argv": ["{python}", "-c", "raise SystemExit(1)"]}]}
            runner = FakeRunner(plan="```research_plan\n" + json.dumps(plan) + "\n```")
            cfg = {"isolation": {"enabled": False}, "orchestrator": {"max_attempts": 2}}
            orch = HybridOrchestrator(root, cfg, db, ContextLogManager(root / "storage/logs"), runner)
            self.assertEqual(orch.run_task_to_completion(orch.submit_task("experiment").id).status, "ESCALATED")
            self.assertNotIn("validator", runner.calls)
            self.assertEqual(runner.calls.count("operator"), 2)

    def test_real_subprocess_pipeline_in_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = root / "fixture.py"
            agent.write_text('''import sys,json
from pathlib import Path
role=sys.argv[1]
assert Path(sys.argv[2]).exists()
if role=='researcher':
 print('```research_plan\\n'+json.dumps({'mode':'direct','checks':[{'argv':['{python}','-c',"from pathlib import Path; assert Path('result.txt').read_text() == '42'"]}]})+'\\n```')
elif role=='operator':
 Path('result.txt').write_text('42'); print('Created result.txt')
elif role=='validator':
 assert Path('result.txt').read_text()=='42'; print('VERDICT: PASS\\nEvidence: actual file contains 42')
else: print('Completed; result.txt contains 42')
''', encoding="utf-8")
            cfg = {"providers": {"fixture": {"type": "custom_cli", "command_template": [sys.executable, str(agent), "{role}", "{prompt_file}"]}},
                   "roles": {r: {"provider": "fixture"} for r in ["researcher", "operator", "validator", "communicator"]}}
            db = WorkspaceDatabase(root / "storage/db.sqlite")
            orch = HybridOrchestrator(root, cfg, db, ContextLogManager(root / "storage/logs"), EphemeralAgentRunner(root, cfg))
            final = orch.run_task_to_completion(orch.submit_task("Save and verify 42").id)
            self.assertEqual(final.status, "COMPLETED", final.validation_feedback)
            self.assertEqual((Path(final.workspace_path) / "result.txt").read_text(), "42")
            self.assertFalse((root / "result.txt").exists())
            self.assertTrue((Path(final.workspace_path) / ".workflow/progress.json").exists())

    def test_command_timeout_and_cancellation(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_process([sys.executable, "-c", "import time; time.sleep(20)"], tmp, timeout=0.2)
            self.assertEqual(result["exit_code"], -1)
            cancelled = threading.Event()
            timer = threading.Timer(0.2, cancelled.set)
            timer.start()
            try:
                with self.assertRaises(InterruptedError):
                    run_process([sys.executable, "-c", "import time; time.sleep(20)"], tmp, timeout=20, cancel_event=cancelled)
            finally:
                timer.cancel()

    def test_waiting_remote_completes_before_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = WorkspaceDatabase(root / "storage/db.sqlite")
            runner = FakeRunner()
            orch = HybridOrchestrator(root, {"isolation": {"enabled": False}}, db, ContextLogManager(root / "storage/logs"), runner)
            task = orch.submit_task("remote experiment")
            task = db.update_task(task.id, status="WAITING_REMOTE", remote_job_id="a" * 32, workspace_path=str(root))
            with patch("core.cloud.CloudTools.kaggle_job_status", return_value={"state": "complete"}), patch("core.cloud.CloudTools.kaggle_export_data", return_value={"status": "completed", "files": ["metrics.csv"]}):
                final = orch.run_task_to_completion(task.id)
            self.assertEqual(final.status, "COMPLETED")
            self.assertNotIn("operator", runner.calls)
            self.assertIn("validator", runner.calls)

    def test_blocked_exit_zero_worker_cannot_pass_delegation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = root / "blocked_fixture.py"
            agent.write_text('''import sys, json
role = sys.argv[1]
if role == 'researcher':
    print('```research_plan\\n' + json.dumps({'mode': 'direct', 'checks': [{'argv': ['{python}', '-c', 'print("ok")']}]}) + '\\n```')
elif role == 'operator':
    print('BLOCKED: Blocked before inspection by the local execution environment (windows sandbox: setup refresh had errors).')
else:
    print('VERDICT: PASS\\nEvidence: should never be reached')
''', encoding="utf-8")
            cfg = {
                "isolation": {"enabled": False},
                "orchestrator": {"max_attempts": 1},
                "providers": {"fixture": {"type": "custom_cli", "command_template": [sys.executable, str(agent), "{role}", "{prompt_file}"]}},
                "roles": {r: {"provider": "fixture"} for r in ["researcher", "operator", "validator", "communicator"]},
            }
            db = WorkspaceDatabase(root / "storage/db.sqlite")
            orch = HybridOrchestrator(root, cfg, db, ContextLogManager(root / "storage/logs"), EphemeralAgentRunner(root, cfg))
            task = orch.submit_task("Must fail closed when operator is blocked despite exit 0")
            final = orch.run_task_to_completion(task.id)
            self.assertEqual(final.status, "ESCALATED")
            self.assertIn("blocked", final.validation_feedback.lower())
