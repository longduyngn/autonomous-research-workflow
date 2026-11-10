"""P2.6-03: bounded structured handoffs, delegation policy in role prompts, context benchmark."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core.handoff import FIELDS, MAX_BYTES, build_handoff, write_handoff

ROOT = Path(__file__).resolve().parents[1]


class HandoffTests(unittest.TestCase):
    def test_schema_and_required_fields(self):
        handoff = build_handoff(task_id=3, role='operator', next_safe_action='rerun the failing check',
                                active_job_ids=['job-1'], acceptance_results=['FID check pending'])
        self.assertEqual(set(handoff), set(FIELDS))
        self.assertEqual(handoff['active_job_ids'], ['job-1'])  # job identity preserved
        with self.assertRaises(ValueError):
            build_handoff(task_id=3, role='operator')
        with self.assertRaises(ValueError):
            build_handoff(task_id=3, role='operator', next_safe_action='x', vibes='compressed')

    def test_bounded_size_keeps_identity(self):
        handoff = build_handoff(task_id=3, role='operator', next_safe_action='continue', plan_hash='abc',
                                completed_actions=['x' * 400] * 100, extra={'blob': 'y' * 20000},
                                active_job_ids=['job-9'])
        self.assertLessEqual(len(json.dumps(handoff, ensure_ascii=False).encode('utf-8')), MAX_BYTES)
        self.assertEqual((handoff['plan_hash'], handoff['active_job_ids']), ('abc', ['job-9']))
        with tempfile.TemporaryDirectory() as tmp:
            written = write_handoff(Path(tmp) / 'h.json', task_id=1, role='planner', next_safe_action='plan')
            self.assertEqual(json.loads((Path(tmp) / 'h.json').read_text(encoding='utf-8')), written)

    def test_role_prompts_carry_delegation_policy(self):
        for role in ('planner', 'operator', 'validator'):
            text = (ROOT / f'roles/{role}.md').read_text(encoding='utf-8')
            self.assertIn('Subagents only for read-only', text, role)
            self.assertIn('no compressed shorthand', text, role)

    def test_benchmark_script_reports_every_role(self):
        done = subprocess.run([sys.executable, str(ROOT / 'scripts/benchmark_context.py'), '--json'],
                              cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        rows = {row['role']: row for row in json.loads(done.stdout)}
        self.assertTrue({'planner', 'operator', 'validator', 'communicator'} <= set(rows))
        self.assertTrue(all(row['approx_tokens'] > 0 for row in rows.values()))


if __name__ == '__main__':
    unittest.main()
