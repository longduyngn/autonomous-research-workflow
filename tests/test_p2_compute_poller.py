"""P2.5-06: model-free poller with deduplicated event wakes."""
import tempfile
import unittest
from pathlib import Path

from core.compute_jobs import JobController, JobRegistry, JobSpec
from core.compute_poller import LOST_AFTER, ComputePoller
from core.control import ControlPlane
from core.database import WorkspaceDatabase
from fakes.compute_adapter import FakeJobAdapter


class ScriptedAdapter(FakeJobAdapter):
    def __init__(self, observations):
        super().__init__()
        self.observations = list(observations)

    def status(self, handle):
        self.calls.append(('status', handle.external_id))
        item = self.observations.pop(0) if len(self.observations) > 1 else self.observations[0]
        if isinstance(item, Exception):
            raise item
        return dict(item)


class PollerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = WorkspaceDatabase(Path(self.tmp.name) / 'db')
        self.task = self.db.create_task('train')
        self.now = [1000.0]

    def tearDown(self):
        self.tmp.cleanup()

    def setup_job(self, observations):
        adapter = ScriptedAdapter(observations)
        registry = JobRegistry()
        registry.register('kaggle', adapter)
        jobs = JobController(self.db, registry, clock=lambda: self.now[0])
        handle, _ = jobs.submit(JobSpec(task_id=self.task.id, backend='kaggle', entry='nb.ipynb',
                                        checkpoint_uri='rclone:gdrive:x', total=0), 'p1')
        return adapter, jobs, handle

    def poller(self, jobs, **kw):
        return ComputePoller(self.db, jobs, control=ControlPlane(self.db), clock=lambda: self.now[0], interval=1, **kw)

    def run_ticks(self, poller, count):
        emitted = []
        for _ in range(count):
            self.now[0] += 2
            emitted += poller.tick()
        return emitted

    def test_thousand_normal_ticks_no_wakes_no_effects(self):
        adapter, jobs, _ = self.setup_job([{'state': 'RUNNING', 'metrics': {'loss': 1.0}}])
        emitted = self.run_ticks(self.poller(jobs), 1000)
        self.assertEqual(emitted, [])
        self.assertEqual(adapter.effects, 1)  # the original submission only
        self.assertFalse([c for c in adapter.calls if c[0] in ('submit', 'cancel')][1:])

    def test_finish_wakes_once_across_pollers_and_restart(self):
        adapter, jobs, handle = self.setup_job([{'state': 'RUNNING'}, {'state': 'COMPLETED', 'completed': 0}])
        first, second = self.poller(jobs, owner='a'), self.poller(jobs, owner='b')
        control = ControlPlane(self.db)
        control.wait(self.task.id, 'REMOTE', 'OPERATING', 'job running', handle.job_id, delay=10 ** 9, timeout=None)
        emitted = self.run_ticks(first, 3) + self.run_ticks(second, 3) + self.run_ticks(self.poller(jobs, owner='c'), 3)
        self.assertEqual([e.split(':')[1] for e in emitted], ['job_finished'])
        self.assertEqual(self.db.get_task(self.task.id).status, 'OPERATING')  # woken, no agent was run
        self.assertEqual(len(first.pending_wakes(self.task.id)), 1)

    def test_lost_observation_is_not_failure(self):
        adapter, jobs, handle = self.setup_job([ConnectionError()])
        emitted = self.run_ticks(self.poller(jobs), LOST_AFTER + 5)
        self.assertEqual([e.split(':')[1] for e in emitted], ['observation_lost'])
        self.assertNotEqual(jobs.store.get(handle.job_id)['state'], 'FAILED')

    def test_metric_anomaly(self):
        adapter, jobs, _ = self.setup_job([{'state': 'RUNNING', 'metrics': {'loss': 1.0}},
                                           {'state': 'RUNNING', 'metrics': {'loss': float('nan')}}])
        emitted = self.run_ticks(self.poller(jobs), 5)
        self.assertEqual([e.split(':')[1] for e in emitted], ['metric_anomaly'])

    def test_poller_does_not_touch_agents(self):
        import core.compute_poller as module
        source = Path(module.__file__).read_text(encoding='utf-8')
        self.assertNotIn('EphemeralAgentRunner', source)
        self.assertNotIn('run_role', source)


if __name__ == '__main__':
    unittest.main()
