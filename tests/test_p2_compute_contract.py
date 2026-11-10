"""P2.5-01: job adapter contract, durable submission journal and separation from VM provisioning."""
import tempfile
import unittest
from pathlib import Path

from core.compute import ComputeAdapter, ComputeRegistry, ProvisioningAdapter
from core.compute_jobs import JobController, JobRegistry, JobSpec, OutputDecl
from core.database import WorkspaceDatabase
from fakes.compute_adapter import FakeJobAdapter


def spec(task_id, **kw):
    base = dict(task_id=task_id, backend='kaggle', entry='train.ipynb', checkpoint_uri='gdrive:workflow-runs/t/ckpt',
                outputs=(OutputDecl('metrics.json', 'metrics', True), OutputDecl('model.pt', 'checkpoint', False)))
    base.update(kw)
    return JobSpec(**base)


class SpecTests(unittest.TestCase):
    def test_validation(self):
        with self.assertRaises(ValueError):
            JobSpec(task_id=1, backend='vast', entry='x')
        with self.assertRaises(ValueError):
            JobSpec(task_id=1, backend='colab', entry='x')  # remote without checkpoint_uri
        with self.assertRaises(ValueError):
            spec(1, allowed_backends=('colab',))         # not allowed by the plan
        self.assertEqual(spec(1).budget_seconds, 0)       # D6: no default budget
        self.assertNotEqual(spec(1).fingerprint(), spec(1, hardware='T4').fingerprint())

    def test_vm_provisioning_stays_separate_and_disabled(self):
        self.assertIs(ComputeAdapter, ProvisioningAdapter)
        with self.assertRaises(RuntimeError):
            ComputeRegistry({}).require('vastai')


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = WorkspaceDatabase(Path(self.tmp.name) / 'db')
        self.task = self.db.create_task('train')

    def tearDown(self):
        self.tmp.cleanup()

    def controller(self, adapter):
        registry = JobRegistry()
        registry.register('kaggle', adapter)
        return JobController(self.db, registry)

    def test_registry_requires_complete_lifecycle(self):
        class Partial:
            def submit(self, *a): ...
        with self.assertRaises(TypeError):
            JobRegistry().register('kaggle', Partial())

    def test_submit_is_idempotent_and_key_reuse_with_changed_spec_is_rejected(self):
        adapter = FakeJobAdapter()
        jobs = self.controller(adapter)
        handle, outcome = jobs.submit(spec(self.task.id), 'run-1')
        self.assertEqual((outcome, handle.external_id), ('submitted', 'ext-1'))
        again, outcome = jobs.submit(spec(self.task.id), 'run-1')
        self.assertEqual((outcome, again.job_id), ('existing', handle.job_id))
        self.assertEqual(adapter.effects, 1)
        with self.assertRaises(ValueError):
            jobs.submit(spec(self.task.id, hardware='T4'), 'run-1')

    def test_submit_timeout_is_unknown_and_never_resubmitted(self):
        adapter = FakeJobAdapter(submit_error=TimeoutError())
        jobs = self.controller(adapter)
        handle, outcome = jobs.submit(spec(self.task.id), 'run-2')
        self.assertEqual(outcome, 'unknown')
        self.assertEqual(jobs.store.get(handle.job_id)['state'], 'SUBMISSION_UNKNOWN')
        _, outcome = jobs.submit(spec(self.task.id), 'run-2')
        self.assertEqual((outcome, adapter.effects), ('unknown', 1))

    def test_status_observes_without_effects_and_transport_error_is_unknown(self):
        adapter = FakeJobAdapter(states=['QUEUED', 'RUNNING', 'COMPLETED'])
        jobs = self.controller(adapter)
        handle, _ = jobs.submit(spec(self.task.id, total=1), 'run-3')
        self.assertEqual(jobs.status(handle)['state'], 'QUEUED')
        self.assertEqual(jobs.store.get(handle.job_id)['state'], 'RUNNING')
        jobs.status(handle)
        self.assertEqual(jobs.status(handle)['state'], 'COMPLETED')
        self.assertEqual(jobs.store.get(handle.job_id)['state'], 'COMPLETED')
        self.assertEqual(adapter.effects, 1)  # only the submission
        broken = FakeJobAdapter()
        broken.status = lambda handle: (_ for _ in ()).throw(ConnectionError())
        jobs2 = self.controller(broken)
        other = self.db.create_task('other')  # one active job per task
        handle2, _ = jobs2.submit(spec(other.id), 'run-4')
        self.assertEqual(jobs2.status(handle2)['state'], 'UNKNOWN')  # not FAILED

    def test_cancel_only_verified_stop_counts(self):
        for result, expected in [({'state': 'STOPPED', 'verified': True}, 'FAILED'),
                                 ({'state': 'STOPPED', 'verified': False}, 'RUNNING'),
                                 ({'state': 'CANCEL_REQUESTED'}, 'RUNNING'),
                                 (TimeoutError(), 'RUNNING')]:
            with self.subTest(result=result):
                db = WorkspaceDatabase(Path(self.tmp.name) / f'db{id(result)}')
                task = db.create_task('t')
                registry = JobRegistry()
                registry.register('kaggle', FakeJobAdapter(states=['RUNNING'], cancel_result=result))
                jobs = JobController(db, registry)
                handle, _ = jobs.submit(spec(task.id), 'run-c')
                jobs.status(handle)
                receipt = jobs.cancel(handle, 'user asked')
                self.assertEqual(jobs.store.get(handle.job_id)['state'], expected)
                if expected == 'RUNNING':
                    self.assertIn(receipt['state'], ('CANCEL_REQUESTED', 'UNKNOWN'))

    def test_fetch_honours_light_output_policy(self):
        adapter = FakeJobAdapter()
        jobs = self.controller(adapter)
        handle, _ = jobs.submit(spec(self.task.id), 'run-5')
        manifest = {'files': [{'uri': 'gdrive:x/model.pt', 'local_download': False},
                              {'uri': 'gdrive:x/metrics.json', 'local_download': True}]}
        fetched = jobs.fetch_outputs(handle, manifest)
        self.assertEqual([f['uri'] for f in fetched['fetched']], ['gdrive:x/metrics.json'])
        self.assertTrue(adapter.calls[-1][1]['local_only_declared_light'])


if __name__ == '__main__':
    unittest.main()
