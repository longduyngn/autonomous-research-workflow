import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.verify_verdict import check_artifact, check_continuity, check_review


class VerdictEvidenceTests(unittest.TestCase):
    def test_review_rejects_incomplete_failed_stale_and_nonlive_records(self):
        task = {'id': 'D05', 'live_gate': True}
        good = {'task_id': 'D05', 'review_passed': True, 'status': 'live_verified',
                'checks': [{'passed': True, 'exit_code': 0}]}
        ledger = {'status': 'live_verified', 'evidence_sha256': 'digest'}
        check_review(task, good, ledger, 'digest')
        for change in ({'task_id': 'D04'}, {'review_passed': False}, {'checks': []},
                       {'checks': [{'passed': True}]}, {'checks': [{'passed': True, 'exit_code': 1}]},
                       {'status': 'source_verified'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                check_review(task, {**good, **change}, ledger, 'digest')
        with self.assertRaisesRegex(ValueError, 'changed after review'):
            check_review(task, good, ledger, 'new digest')

    def test_artifact_rejects_same_size_corruption_and_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'metrics.json'
            path.write_bytes(b'1234')
            receipt = {'path': path.name, 'bytes': 4, 'sha256': hashlib.sha256(b'1234').hexdigest()}
            check_artifact(tmp, receipt)
            path.write_bytes(b'4321')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                check_artifact(tmp, receipt)
            with self.assertRaisesRegex(ValueError, 'escapes'):
                check_artifact(tmp, {**receipt, 'path': '../outside'})

    def test_continuity_rejects_new_session_disguised_as_resume_and_missing_rotation(self):
        good = {'p': {'wake1': {'success': True, 'session_id': 'a', 'generation': 1},
                      'wake2': {'success': True, 'session_id': 'a', 'generation': 1, 'resume_requested': True},
                      'wake3_rotated': {'success': True, 'session_id': 'b', 'generation': 2,
                                        'handover': {'previous_generation': 1}}}}
        check_continuity(good)
        for wake, key, value in [('wake2', 'session_id', 'different'), ('wake3_rotated', 'generation', 1),
                                  ('wake3_rotated', 'session_id', 'a')]:
            bad = copy.deepcopy(good)
            bad['p'][wake][key] = value
            with self.subTest(wake=wake, key=key), self.assertRaises(ValueError):
                check_continuity(bad)
