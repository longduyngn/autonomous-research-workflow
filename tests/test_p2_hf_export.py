"""P2.5-09: optional HF Hub export (default off, private, manifest allowlist, failure-isolated)."""
import json
import tempfile
import unittest
from pathlib import Path

from core.artifact_store import LocalDirStore, export_to_hf, publish_checkpoint

ROOT = Path(__file__).resolve().parents[1]


class FakeHf:
    def __init__(self, fail=False):
        self.created, self.uploads, self.fail = [], [], fail

    def create_repo(self, repo_id, private=True, exist_ok=True):
        self.created.append((repo_id, private))

    def upload_file(self, path_or_fileobj, path_in_repo, repo_id, commit_message):
        if self.fail:
            raise ConnectionError('hub down')
        self.uploads.append(path_in_repo)
        return {'oid': 'rev-' + str(len(self.uploads))}


class HfExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = LocalDirStore(root / 'drive')
        (root / 'model.pt').write_bytes(b'w' * 100)
        (root / 'metrics.json').write_text('{}', encoding='utf-8')
        self.manifest = publish_checkpoint(self.store, 'runs/1/r', 'final', [root / 'model.pt', root / 'metrics.json'],
                                           {'task_id': 1, 'logical_run_id': 'r', 'job_id': 'j', 'backend': 'kaggle'})

    def tearDown(self):
        self.tmp.cleanup()

    def test_disabled_by_default(self):
        config = json.loads((ROOT / 'config/workflow_config.example.json').read_text(encoding='utf-8'))
        self.assertFalse(config['storage']['hf_export']['enabled'])
        with self.assertRaises(RuntimeError):
            export_to_hf(self.store, self.manifest, config['storage']['hf_export'], api=FakeHf())

    def test_private_allowlisted_upload_with_revisions(self):
        api = FakeHf()
        receipt = export_to_hf(self.store, self.manifest, {'enabled': True, 'repo_id': 'me/ddpm-mini'}, api=api,
                               names=['model.pt'])
        self.assertEqual(api.created, [('me/ddpm-mini', True)])
        self.assertEqual(api.uploads, ['r/final/model.pt'])
        self.assertEqual(receipt['files'][0]['revision'], 'rev-1')

    def test_hub_failure_leaves_shared_store_intact(self):
        with self.assertRaises(ConnectionError):
            export_to_hf(self.store, self.manifest, {'enabled': True, 'repo_id': 'me/x'}, api=FakeHf(fail=True))
        self.assertIsNotNone(self.store.stat(self.manifest['files'][0]['rel']))
        with self.assertRaises(ValueError):
            export_to_hf(self.store, self.manifest, {'enabled': True, 'repo_id': 'not a repo'}, api=FakeHf())


if __name__ == '__main__':
    unittest.main()
