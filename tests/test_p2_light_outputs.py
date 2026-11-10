"""P2.5-07: only light results come home; heavy artifacts are remote-verified evidence."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.artifact_store import LocalDirStore, publish_checkpoint
from core.cloud import CloudTools
from core.database import WorkspaceDatabase
from core.monitoring import ProgressStore
from core.results import collect_results


class LightOutputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = LocalDirStore(self.root / 'drive')
        files = self.root / 'out'
        files.mkdir()
        (files / 'model.pt').write_bytes(b'w' * 200_000)
        (files / 'metrics.json').write_text('{"fid": 28.4}', encoding='utf-8')
        (files / 'samples.png').write_bytes(b'\x89PNG' + b'0' * 100)
        self.manifest = publish_checkpoint(self.store, 'runs/9/ddpm', 'final', [files / 'model.pt', files / 'metrics.json',
                                           files / 'samples.png'], {'task_id': 9, 'logical_run_id': 'ddpm', 'job_id': 'job-9',
                                           'backend': 'kaggle'}, kinds={'metrics.json': 'metrics', 'samples.png': 'sample'},
                                           light=('metrics.json', 'samples.png'))

    def tearDown(self):
        self.tmp.cleanup()

    def test_heavy_stays_remote_light_comes_home(self):
        result = collect_results(self.store, self.manifest, self.root / 'results')
        states = {Path(a['uri']).name: a['state'] for a in result['artifacts']}
        self.assertEqual(states, {'model.pt': 'remote_verified', 'metrics.json': 'local_fetched', 'samples.png': 'local_fetched'})
        directory = Path(result['directory'])
        self.assertFalse((directory / 'model.pt').exists())
        self.assertTrue((directory / 'metrics.json').exists())
        self.assertTrue((directory / 'manifest.json').exists())
        self.assertTrue(result['complete'])

    def test_missing_or_corrupt_files_are_unvalidated(self):
        (self.root / 'drive/runs/9/ddpm/checkpoints/final/model.pt').unlink()
        (self.root / 'drive/runs/9/ddpm/checkpoints/final/metrics.json').write_text('tampered', encoding='utf-8')
        result = collect_results(self.store, self.manifest, self.root / 'results')
        states = {Path(a['uri']).name: a['state'] for a in result['artifacts']}
        self.assertEqual((states['model.pt'], states['metrics.json']), ('unvalidated', 'unvalidated'))
        self.assertFalse(result['complete'])

    def test_job_artifacts_accepts_verified_manifest_without_downloading_checkpoints(self):
        workspace = self.root / 'ws'
        workspace.mkdir()
        db = WorkspaceDatabase(self.root / 'db')
        task = db.create_task('t')
        job, _ = ProgressStore(db).register(task.id, 'r', 'kaggle')
        ProgressStore(db).observe(job['id'], {'state': 'COMPLETED', 'completed': 0})
        manifest = dict(self.manifest, job_id=job['id'])
        result = collect_results(self.store, manifest, workspace / 'results')
        relative = Path(result['directory']).relative_to(workspace) / 'manifest.json'
        env = {'WORKFLOW_DB_PATH': str(db.db_path), 'WORKFLOW_TASK_ID': str(task.id), 'WORKFLOW_ROLE': 'operator'}
        with patch.dict(os.environ, env):
            tools = CloudTools(workspace, {})
            with patch.object(CloudTools, '_progress_context', return_value=(ProgressStore(db), task.id)):
                recorded = tools.experiment_job_artifacts({'job_id': job['id'], 'manifest': str(relative)})
        self.assertEqual(recorded['status'], 'artifacts_downloaded_unvalidated')
        self.assertEqual({r['state'] for r in recorded['files']}, {'remote_verified', 'local_fetched'})
        self.assertEqual(ProgressStore(db).get(job['id'])['state'], 'ARTIFACTS_READY')


if __name__ == '__main__':
    unittest.main()
