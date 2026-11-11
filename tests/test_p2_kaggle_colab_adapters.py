"""P2.5-04/05: Colab and Kaggle job adapters (fake CLI/bridge; no account, browser or GPU)."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.artifact_store import LocalDirStore, publish_checkpoint
from core.cloud import CloudTools
from core.compute_backends import ColabJobAdapter, KaggleJobAdapter
from core.compute_jobs import JobController, JobRegistry, JobSpec, OutputDecl
from core.database import WorkspaceDatabase

ROOT = Path(__file__).resolve().parents[1]
spec_cancel = importlib.util.spec_from_file_location(
    'kaggle_cancel', ROOT / '.agents/skills/kaggle-playwright-cancel/scripts/kaggle_cancel.py')
kaggle_cancel = importlib.util.module_from_spec(spec_cancel)
spec_cancel.loader.exec_module(kaggle_cancel)
NOTEBOOK = {'nbformat': 4, 'nbformat_minor': 5, 'metadata': {}, 'cells': [
    {'cell_type': 'code', 'id': 'train1', 'metadata': {}, 'source': ['print(1)\n'], 'outputs': [], 'execution_count': None}]}


class FakeKaggleCLI:
    def __init__(self, status='running', version_line='Kernel version 3 successfully pushed.'):
        self.calls, self.status_text, self.version_line = [], status, version_line

    def __call__(self, argv, timeout=120):
        self.calls.append(argv)
        if argv[0] == 'push':
            return f"{self.version_line} Please check progress at https://www.kaggle.com/code/user1/ddpm-mini-run"
        if argv[0] == 'status':
            return f'user1/ddpm-mini-run has status "{self.status_text}"'
        if argv[0] == 'output':
            return 'Output file downloaded'
        raise AssertionError(argv)


class KaggleAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        (self.ws / 'train.ipynb').write_text(json.dumps(NOTEBOOK), encoding='utf-8')
        self.db = WorkspaceDatabase(self.ws / 'db')
        self.task = self.db.create_task('train')
        self.cli = FakeKaggleCLI()
        self.tools = CloudTools(self.ws, {})
        self.tools._kaggle = self.cli
        self.adapter = KaggleJobAdapter(self.ws, {}, tools=self.tools)
        registry = JobRegistry()
        registry.register('kaggle', self.adapter)
        self.jobs = JobController(self.db, registry)

    def tearDown(self):
        self.tmp.cleanup()

    def spec(self, **kw):
        return JobSpec(task_id=self.task.id, backend='kaggle', entry='train.ipynb', hardware='T4',
                       checkpoint_uri='rclone:gdrive:workflow-runs/1/r/ckpt',
                       config={'kaggle_kernel_slug': 'user1/ddpm-mini-run'}, **kw)

    def test_submit_records_version_injects_identity_and_no_default_timeout(self):
        handle, outcome = self.jobs.submit(self.spec(), 'k1')
        self.assertEqual(outcome, 'submitted')
        self.assertEqual((handle.details['slug'], handle.details['version']), ('user1/ddpm-mini-run', 3))
        push = self.cli.calls[0]
        self.assertNotIn('--timeout', push)  # D6
        staged = json.loads(next((self.ws / '.workflow/cloud_jobs').glob('*/stage/*.ipynb')).read_text(encoding='utf-8'))
        boot = ''.join(staged['cells'][0]['source']) + ''.join(staged['cells'][1]['source'])
        self.assertIn('WORKFLOW_CHECKPOINT_URI', boot)
        self.assertIn('rclone:gdrive:workflow-runs/1/r/ckpt', boot)

    def test_rclone_secret_bootstrap_writes_config_without_printing_and_sets_root_folder(self):
        self.adapter.config = {'storage': {'remote': 'gdrive', 'root_folder_id': 'FOLDER123'}}
        self.jobs.submit(self.spec(), 'k9')
        staged = json.loads(next((self.ws / '.workflow/cloud_jobs').glob('*/stage/*.ipynb')).read_text(encoding='utf-8'))
        boot = ''.join(''.join(c['source']) for c in staged['cells'][:2])
        self.assertIn("get_secret('RCLONE_GDRIVE_CONF')", boot)
        self.assertIn("RCLONE_CONFIG_GDRIVE_ROOT_FOLDER_ID'] = 'FOLDER123'", boot)
        self.assertIn('rclone-v1.75.2-linux-amd64.zip', boot)
        self.assertNotIn('print(_conf', boot)
        compile(boot, 'boot', 'exec')

    def test_non_rclone_uri_has_no_secret_bootstrap(self):
        from core.compute_backends import rclone_bootstrap_lines
        self.assertEqual(rclone_bootstrap_lines('/content/drive/MyDrive/Checkpoint/x', {}), [])
        with self.assertRaises(ValueError):
            rclone_bootstrap_lines('rclone:bad remote:x', {})

    def test_user_budget_is_passed(self):
        self.jobs.submit(self.spec(budget_seconds=7200), 'k2')
        self.assertIn('--timeout', self.cli.calls[0])

    def test_status_is_pure_and_version_mismatch_is_unknown(self):
        handle, _ = self.jobs.submit(self.spec(), 'k3')
        self.assertEqual(self.jobs.status(handle)['state'], 'RUNNING')
        self.assertEqual([c[0] for c in self.cli.calls], ['push', 'status'])
        with self.assertRaises(ValueError):
            self.tools.kaggle_job_status({'job_id': handle.external_id, 'cancel': True})
        self.cli.status_text = 'complete'
        original = self.cli.__call__
        self.tools._kaggle = lambda argv, timeout=120: ('user1/ddpm-mini-run version 4 has status "complete"'
                                                        if argv[0] == 'status' else original(argv, timeout))
        self.assertEqual(self.jobs.status(handle)['state'], 'UNKNOWN')  # slug now points at another version

    def test_cancel_requires_version_and_verifies(self):
        handle, _ = self.jobs.submit(self.spec(), 'k4')
        with patch('core.cloud._is_port_open', return_value=True), \
                patch.object(kaggle_cancel, 'cancel_kaggle_kernel', return_value={'status': 'cancel_requested'}), \
                patch('importlib.util.spec_from_file_location', return_value=spec_cancel), \
                patch.object(spec_cancel.loader, 'exec_module', lambda module: module.__dict__.update(
                    cancel_kaggle_kernel=lambda slug, version=None: {'status': 'cancel_requested', 'version': version})):
            receipt = self.jobs.cancel(handle, 'user asked')
            self.assertEqual(receipt['state'], 'CANCEL_REQUESTED')  # CLI still says running
            self.cli.status_text = 'cancelled'
            receipt = self.jobs.cancel(handle, 'user asked')
            self.assertEqual((receipt['state'], receipt['verified']), ('STOPPED', True))

    def test_export_downloads_only_light_or_named_files(self):
        handle, _ = self.jobs.submit(self.spec(), 'k5')
        self.cli.status_text = 'complete'
        self.tools.kaggle_export_data({'job_id': handle.external_id})
        output = self.cli.calls[-1]
        self.assertEqual(output[:2], ['output', 'user1/ddpm-mini-run/3'])
        self.assertIn('--file-pattern', output)
        self.tools.kaggle_export_data({'job_id': handle.external_id, 'files': ['metrics.json']})
        self.assertIn('^(?:metrics\\.json)$', self.cli.calls[-1])


class CancelSkillTests(unittest.TestCase):
    def test_exact_version_row_selection(self):
        rows = ['Version 12 · Running', 'Version 1 · Complete', 'Version 2 · Error']
        self.assertEqual(kaggle_cancel.select_version_row(rows, 1), 1)
        self.assertEqual(kaggle_cancel.select_version_row(rows, 12), 0)
        self.assertIsNone(kaggle_cancel.select_version_row(rows, 3))
        self.assertIsNone(kaggle_cancel.select_version_row(['Version 1', 'Version 1 (copy)'], 1))  # ambiguous

    def test_cancel_without_version_clicks_nothing(self):
        result = kaggle_cancel.cancel_kaggle_kernel('user1/x', version=None)
        self.assertEqual(result['status'], 'refused')


class FakeColabTools:
    def __init__(self):
        self.started, self.released = 0, 0
        self.lines = []

    def colab_job_start(self, args):
        self.started += 1
        return {'status': 'submitted_unverified', 'job_id': args['job_id']}

    def _colab(self, route, payload, timeout=20):
        return {'structuredContent': {'cells': [{'id': 'cell-7', 'outputs': [{'text': '\n'.join(self.lines)}]}]}}

    def colab_release(self, args):
        self.released += 1
        return {'status': 'released', 'released': True}


class ColabAdapterTests(unittest.TestCase):
    def test_launch_once_observe_markers_fetch_light_from_store_and_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            db = WorkspaceDatabase(ws / 'db')
            task = db.create_task('colab train')
            tools = FakeColabTools()
            store = LocalDirStore(ws / 'drive')
            adapter = ColabJobAdapter(ws, {}, tools=tools, store=store)
            registry = JobRegistry()
            registry.register('colab', adapter)
            jobs = JobController(db, registry)
            spec = JobSpec(task_id=task.id, backend='colab', entry='nb.ipynb', checkpoint_uri='file:runs/x',
                           config={'register_external_id': 'cell-7'}, total=10)
            handle, _ = jobs.submit(spec, 'c1')
            jobs.submit(spec, 'c1')
            self.assertEqual(tools.started, 1)
            self.assertEqual(jobs.status(handle)['state'], 'UNKNOWN')  # no marker yet
            tools.lines = ['epoch 1', 'WORKFLOW_PROGRESS: ' + json.dumps({'job_id': handle.job_id, 'state': 'RUNNING', 'completed': 4})]
            self.assertEqual(jobs.status(handle)['state'], 'RUNNING')
            self.assertEqual(jobs.store.get(handle.job_id)['completed'], 4)
            local = ws / 'files'
            local.mkdir()
            (local / 'model.pt').write_bytes(b'x' * 1000)
            (local / 'metrics.json').write_text('{"fid": 30}', encoding='utf-8')
            manifest = publish_checkpoint(store, 'runs/x', 'ck1', [local / 'model.pt', local / 'metrics.json'],
                                          {'task_id': task.id, 'logical_run_id': 'x', 'job_id': handle.job_id, 'backend': 'colab'},
                                          light=('metrics.json',))
            fetched = jobs.fetch_outputs(handle, manifest, {'destination': str(ws / 'results')})
            self.assertEqual([Path(f['path']).name for f in fetched['fetched']], ['metrics.json'])
            self.assertFalse((ws / 'results' / 'model.pt').exists())
            receipt = jobs.cancel(handle, 'done')
            self.assertEqual((receipt['state'], tools.released), ('STOPPED', 1))


if __name__ == '__main__':
    unittest.main()
