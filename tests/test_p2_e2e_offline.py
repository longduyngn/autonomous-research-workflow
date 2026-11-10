"""P2.7-01: offline end-to-end (plan approval + quota failover + independent validator; job -> poller -> results)."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('verify_p2_e2e', ROOT / 'scripts' / 'verify_p2_e2e.py')
e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e)


class OfflineE2ETests(unittest.TestCase):
    def test_orchestrator_flow(self):
        # Regression: a consumed PLAN_APPROVAL must not trip the exact-action "approved but uncertain" gate
        # when the next operator attempt hits a quota limit.
        with tempfile.TemporaryDirectory() as tmp:
            report = e2e.orchestrator_flow(Path(tmp))
        self.assertTrue(report['passed'], report)
        self.assertEqual(report['operator_providers'], ['codex_cli', 'claude_cli'])
        self.assertTrue(report['validator_independent'])  # even inside an ordered worker chain

    def test_compute_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = e2e.compute_flow(Path(tmp))
        self.assertTrue(report['passed'], report)


if __name__ == '__main__':
    unittest.main()
