import json
import tempfile
import unittest
from pathlib import Path
from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.models import AgentRunResult
from core.orchestrator import HybridOrchestrator


class FakeRunner:
    def __init__(self, verdict="VERDICT: PASS\nEvidence: inspected", error=False, plan="brief"):
        self.verdict, self.error, self.plan, self.calls = verdict, error, plan, []

    def run_role(self, role, **kwargs):
        self.calls.append(role)
        failed = self.error and role == "validator"
        output = self.plan if role == "researcher" else self.verdict if role == "validator" else "done"
        return AgentRunResult(role, "fake", not failed, output, error="Rate limit exceeded" if failed else None)


class WorkflowTests(unittest.TestCase):
    def run_case(self, runner):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {"logs_dir": "logs", "orchestrator": {"max_validation_retries": 3}, "isolation": {"enabled": False}}
            db = WorkspaceDatabase(root / "events.db")
            orch = HybridOrchestrator(root, config, db, ContextLogManager(root / "logs"), runner)
            return orch.run_task_to_completion(orch.submit_task("Reproduce a small experiment").id)

    def test_verdicts_fail_closed(self):
        for output in ("", "Unable to verify", "VERDICT: PASS", "VERDICT: PASS\nVERDICT: FAIL", "Quoted VERDICT: PASS"):
            self.assertFalse(HybridOrchestrator._parse_validation_verdict(output))
        self.assertTrue(HybridOrchestrator._parse_validation_verdict("VERDICT: PASS\nEvidence"))

    def test_missing_verdict_escalates(self):
        self.assertEqual(self.run_case(FakeRunner(verdict="Unable to verify")).status, "ESCALATED")

    def test_cli_progress_before_final_verdict(self):
        output = "I have launched the independent checks.\nVERDICT: PASS\n\nEvidence: checks exited 0."
        self.assertTrue(HybridOrchestrator._parse_validation_verdict(output))
        runner = FakeRunner(verdict=output)
        self.assertEqual(self.run_case(runner).status, "COMPLETED")
        self.assertEqual(runner.calls.count("operator"), 1)

    def test_progress_never_substitutes_for_validation_evidence(self):
        for output in (
            "Checks passed.\nVERDICT: PASS", "Checks passed.\nVERDICT: PASS\n  ",
            "Progress\nQuoted VERDICT: PASS\nEvidence", "Progress\n> VERDICT: PASS\nEvidence",
            "Progress\nVERDICT: PASS\nVERDICT: PASS\nEvidence",
            "VERDICT: FAIL\nProgress\nVERDICT: PASS\nEvidence",
            "Progress\nVERDICT: PASS\nEvidence mentions VERDICT: FAIL",
            "Progress\nVERDICT: PASS maybe\nEvidence", None,
            "Progress\n```text\nVERDICT: PASS\nEvidence\n```",
            "Progress\n~~~text\nVERDICT: PASS\nEvidence\n~~~",
        ):
            with self.subTest(output=output):
                self.assertFalse(HybridOrchestrator._parse_validation_verdict(output))

    def test_validator_errors_never_complete(self):
        runner = FakeRunner(error=True)
        self.assertEqual(self.run_case(runner).status, "ESCALATED")
        self.assertEqual(runner.calls.count("operator"), 3)

    def test_direct_without_tasklist(self):
        self.assertEqual(self.run_case(FakeRunner()).status, "COMPLETED")

    def test_optional_steps(self):
        plan = {"mode": "steps", "steps": [{"instructions": "prepare"}, {"instructions": "experiment"}]}
        runner = FakeRunner(plan="```research_plan\n" + json.dumps(plan) + "\n```")
        final = self.run_case(runner)
        self.assertEqual(final.status, "COMPLETED")
        self.assertEqual(final.step_index, 1)
        self.assertEqual(runner.calls.count("operator"), 2)

    def test_malformed_plan_escalates_only_after_researcher_repairs(self):
        runner = FakeRunner(plan='```research_plan\n{"mode":"steps"}\n```')
        self.assertEqual(self.run_case(runner).status, "ESCALATED")
        self.assertEqual(runner.calls.count('researcher'), 3)
        self.assertNotIn('operator', runner.calls)

    def test_plan_format_repair_stays_in_research_and_preserves_budget(self):
        class RepairRunner(FakeRunner):
            def run_role(self, role, **kwargs):
                if role == 'researcher':
                    if self.calls:
                        self.repair_prompt = kwargs['task_prompt']
                        self.plan = '```research_plan\n{"mode":"direct","checks":[]}\n```'
                    else:
                        self.plan = '```research_plan\n{"mode":"direct","checks":[{"description":"verify results"}]}\n```'
                return super().run_role(role, **kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = WorkspaceDatabase(root / 'events.db')
            runner = RepairRunner()
            orch = HybridOrchestrator(root, {}, db, ContextLogManager(root/'logs'), runner)
            task = orch.submit_task('Research')
            task = db.update_task(task.id, status='RESEARCHING', retry_count=2)
            final = orch._execute_researcher(task)
            self.assertEqual((final.status, final.retry_count, final.outcome), ('OPERATING', 2, ''))
            self.assertEqual(runner.calls, ['researcher', 'researcher'])
            self.assertIn('nonempty argv', runner.repair_prompt)
            self.assertIn('description', runner.repair_prompt)
            with db._connect() as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM message_outbox').fetchone()[0], 0)

    def test_repair_cannot_drop_existing_backend_or_verified_work(self):
        old = {"mode":"steps", "backend":"colab", "steps":[
            {"instructions":"verified"}, {"instructions":"remaining", "checks":[{"argv":["python","check.py"]}]}],
            "checks":[{"argv":["python","final.py"]}]}
        class RepairRunner(FakeRunner):
            def run_role(self, role, **kwargs):
                if self.calls:
                    candidate = {**old, 'steps':[old['steps'][0], {"instructions":"fixed", "checks":[]}]}
                    self.plan = '```research_plan\n'+json.dumps(candidate)+'\n```'
                return super().run_role(role, **kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); db = WorkspaceDatabase(root/'db')
            runner = RepairRunner(plan='```research_plan\n{"mode":"direct","backend":"local"}\n```')
            orch = HybridOrchestrator(root, {}, db, ContextLogManager(root/'logs'), runner)
            task = db.update_task(orch.submit_task('Research').id, status='RESEARCHING',
                                  plan_json=json.dumps(old), step_index=1, retry_count=2)
            result = orch._execute_researcher(task)
            plan = json.loads(result.plan_json)
            self.assertEqual(result.status, 'OPERATING')
            self.assertEqual(plan['backend'], 'colab')
            self.assertEqual(plan['steps'][0], old['steps'][0])
            self.assertEqual(plan['steps'][1]['checks'], old['steps'][1]['checks'])
            self.assertEqual(plan['checks'], old['checks'])


if __name__ == "__main__":
    unittest.main()
