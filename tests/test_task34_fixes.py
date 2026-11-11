"""Defects found by live task #34: double job registration, stale communicator bundle, Drive bootstrap via tool."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.cloud import CloudTools
from core.database import WorkspaceDatabase
from core.integrations import bundle_is_current, prepare_integrations
from core.monitoring import ProgressStore

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = {'nbformat': 4, 'nbformat_minor': 5, 'metadata': {}, 'cells': [
    {'cell_type': 'code', 'id': 'train1', 'metadata': {}, 'source': ['print(1)\n'], 'outputs': [], 'execution_count': None}]}


class FakeKaggleCLI:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, timeout=120):
        self.calls.append(argv)
        if argv[0] == 'push':
            return 'Kernel version 1 successfully pushed. Please check progress at https://www.kaggle.com/code/u/run-one'
        raise AssertionError(argv)


class SingleTrackedJob(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = WorkspaceDatabase(self.root / 'db')
        self.task = self.db.create_task('train', logs_dir=self.root / 'logs')
        self.store = ProgressStore(self.db)

    def test_bind_external_reuses_registered_job_and_never_rebinds(self):
        job, _ = self.store.register(self.task.id, 'run1', 'kaggle', total=60)
        self.assertEqual(self.store.unbound(self.task.id, 'kaggle'), job['id'])
        self.assertEqual(self.store.bind_external(job['id'], self.task.id, 'kaggle', 'cloud-a')['external_id'], 'cloud-a')
        self.assertIsNone(self.store.unbound(self.task.id, 'kaggle'))
        self.store.bind_external(job['id'], self.task.id, 'kaggle', 'cloud-a')  # idempotent
        with self.assertRaises(ValueError):
            self.store.bind_external(job['id'], self.task.id, 'kaggle', 'cloud-b')
        with self.assertRaises(ValueError):
            self.store.bind_external(job['id'], self.task.id, 'colab', 'cloud-a')
        other = self.db.create_task('other', logs_dir=self.root / 'logs')
        with self.assertRaises(ValueError):
            self.store.bind_external(job['id'], other.id, 'kaggle', 'cloud-a')

    def test_kaggle_tool_binds_to_job_registered_before_launch(self):
        ws = self.root / 'ws'; ws.mkdir()
        (ws / 'train.ipynb').write_text(json.dumps(NOTEBOOK), encoding='utf-8')
        job, _ = self.store.register(self.task.id, 'run1', 'kaggle', total=60)
        tools = CloudTools(ws, {'storage': {'remote': 'gdrive', 'root_folder_id': 'FOLDER'}})
        tools._kaggle = FakeKaggleCLI()
        with patch.object(CloudTools, '_progress_context', return_value=(self.store, self.task.id)):
            out = tools.kaggle_execute_notebook({'notebook_path': 'train.ipynb', 'kaggle_kernel_slug': 'u/run-one',
                                                 'checkpoint_uri': 'rclone:gdrive:workflow-runs/1/r'})
        self.assertEqual(out['tracking_id'], job['id'])
        self.assertEqual(len(self.store.jobs(self.task.id)), 1)
        self.assertEqual(self.store.get(job['id'])['external_id'], out['id'])
        staged = json.loads(next((ws / '.workflow/cloud_jobs').glob('*/stage/*.ipynb')).read_text(encoding='utf-8'))
        boot = ''.join(''.join(c['source']) for c in staged['cells'])
        self.assertIn("get_secret('RCLONE_GDRIVE_CONF')", boot)
        self.assertIn("RCLONE_CONFIG_GDRIVE_ROOT_FOLDER_ID'] = 'FOLDER'", boot)
        self.assertIn('rclone:gdrive:workflow-runs/1/r', boot)


class StaleConversationBundle(unittest.TestCase):
    def test_stale_hook_digest_is_detected_and_refreshed(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            prepare_integrations(ROOT, ws, ws)
            self.assertTrue(bundle_is_current(ws, ROOT))
            manifest = json.loads((ws / '.workflow/integration.json').read_text(encoding='utf-8'))
            manifest['hook_script_sha256'] = 'old'
            (ws / '.workflow/integration.json').write_text(json.dumps(manifest), encoding='utf-8')
            self.assertFalse(bundle_is_current(ws, ROOT))
            prepare_integrations(ROOT, ws, ws)
            self.assertTrue(bundle_is_current(ws, ROOT))


if __name__ == '__main__':
    unittest.main()


class FailedKaggleLog(unittest.TestCase):
    def test_error_tail_extracts_traceback_from_kernel_log(self):
        from core.cloud import kaggle_log_error_tail
        with tempfile.TemporaryDirectory() as tmp:
            log = [{'stream_name': 'stderr', 'data': 'noise\n'},
                   {'stream_name': 'stderr', 'data': "----> 8     raise RuntimeError('x')\n"},
                   {'stream_name': 'stderr', 'data': 'RuntimeError: Kaggle secret RCLONE_GDRIVE_CONF is missing\n'}]
            (Path(tmp) / 'run.log').write_text(json.dumps(log), encoding='utf-8')
            tail = kaggle_log_error_tail(tmp)
        self.assertTrue(any('RCLONE_GDRIVE_CONF is missing' in l for l in tail))
        self.assertNotIn('noise', tail)
