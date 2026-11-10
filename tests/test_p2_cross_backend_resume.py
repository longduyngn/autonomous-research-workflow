"""P2.5-08: resume across backends from a verified checkpoint; continuation equals an uninterrupted run."""
import json
import pickle
import random
import tempfile
import unittest
from pathlib import Path

from core.artifact_store import LocalDirStore, latest_resumable, publish_checkpoint
from core.compute_jobs import plan_successor, verify_continuation
from core.database import WorkspaceDatabase
from core.monitoring import ProgressStore

DATA = [(x / 10.0, 3.0 * x / 10.0 + 1.0) for x in range(40)]


class TinyTrainer:
    """Deterministic SGD with momentum, shuffling RNG and a data cursor: everything a checkpoint must hold."""

    def __init__(self, seed=7):
        self.w, self.b, self.vw, self.vb = 0.0, 0.0, 0.0, 0.0
        self.rng = random.Random(seed)
        self.order, self.cursor, self.step = [], 0, 0

    def _next(self):
        if self.cursor >= len(self.order):
            self.order = list(range(len(DATA)))
            self.rng.shuffle(self.order)
            self.cursor = 0
        item = DATA[self.order[self.cursor]]
        self.cursor += 1
        return item

    def train(self, steps):
        for _ in range(steps):
            x, y = self._next()
            err = (self.w * x + self.b) - y
            self.vw = 0.9 * self.vw + err * x
            self.vb = 0.9 * self.vb + err
            self.w -= 0.01 * self.vw
            self.b -= 0.01 * self.vb
            self.step += 1

    def state(self):
        return {'w': self.w, 'b': self.b, 'vw': self.vw, 'vb': self.vb, 'rng': self.rng.getstate(),
                'order': self.order, 'cursor': self.cursor, 'step': self.step}

    @classmethod
    def restore(cls, state):
        trainer = cls()
        for key in ('w', 'b', 'vw', 'vb', 'order', 'cursor', 'step'):
            setattr(trainer, key, state[key])
        trainer.rng.setstate(state['rng'])
        return trainer


IDENTITY = dict(code_hash='code-1', data_hash='data-1', config_hash='cfg-1', environment_hash='env-1')


class CrossBackendResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = LocalDirStore(self.root / 'drive')
        self.db = WorkspaceDatabase(self.root / 'db')
        self.jobs = ProgressStore(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def checkpoint(self, trainer, cid, backend, job_id):
        path = self.root / f'{cid}.pkl'
        path.write_bytes(pickle.dumps(trainer.state()))
        return publish_checkpoint(self.store, 'runs/1/tiny', cid, [path],
                                  {'task_id': 1, 'logical_run_id': 'tiny', 'job_id': job_id, 'backend': backend,
                                   'global_step': trainer.step, 'data_cursor': trainer.cursor, **IDENTITY})

    def finished_job(self, key, backend, state='FAILED'):
        task = self.db.create_task(key)
        job, _ = self.jobs.register(task.id, key, backend)
        self.jobs.observe(job['id'], {'state': state, 'completed': 0})
        return job['id']

    def test_colab_to_kaggle_and_back_matches_uninterrupted_run(self):
        straight = TinyTrainer()
        straight.train(90)

        first = TinyTrainer()
        first.train(30)
        colab_job = self.finished_job('colab-1', 'colab')  # Colab session dropped after a checkpoint
        before = self.checkpoint(first, 'ck-030', 'colab', colab_job)

        successor = plan_successor(self.jobs, colab_job, self.store, 'runs/1/tiny', 'kaggle', ('colab', 'kaggle'), **IDENTITY)
        self.assertEqual((successor['parent_job_id'], successor['config']['resume_global_step']), (colab_job, 30))
        restored_path = self.root / 'restored.pkl'
        self.store.get(successor['config']['resume_files'][0], restored_path)
        second = TinyTrainer.restore(pickle.loads(restored_path.read_bytes()))
        second.train(30)
        kaggle_job = self.finished_job('kaggle-1', 'kaggle')
        middle = self.checkpoint(second, 'ck-060', 'kaggle', kaggle_job)
        self.assertTrue(verify_continuation(before, middle))

        back = plan_successor(self.jobs, kaggle_job, self.store, 'runs/1/tiny', 'colab', ('colab', 'kaggle'), **IDENTITY)
        self.store.get(back['config']['resume_files'][0], restored_path)
        third = TinyTrainer.restore(pickle.loads(restored_path.read_bytes()))
        third.train(30)
        self.assertEqual((third.w, third.b, third.step), (straight.w, straight.b, straight.step))

    def test_unsafe_successors_are_refused(self):
        trainer = TinyTrainer()
        trainer.train(10)
        running_task = self.db.create_task('running')
        running, _ = self.jobs.register(running_task.id, 'run-unknown', 'colab')
        self.jobs.observe(running['id'], {'state': 'UNKNOWN', 'completed': 0})
        self.checkpoint(trainer, 'ck-010', 'colab', running['id'])
        with self.assertRaises(ValueError):  # previous job UNKNOWN: two writers risk
            plan_successor(self.jobs, running['id'], self.store, 'runs/1/tiny', 'kaggle', ('colab', 'kaggle'), **IDENTITY)
        done = self.finished_job('done', 'colab')
        with self.assertRaises(ValueError):  # plan only allows Colab
            plan_successor(self.jobs, done, self.store, 'runs/1/tiny', 'kaggle', ('colab',), **IDENTITY)
        with self.assertRaises(ValueError):  # code changed since the checkpoint
            plan_successor(self.jobs, done, self.store, 'runs/1/tiny', 'kaggle', (), **{**IDENTITY, 'code_hash': 'code-2'})

    def test_continuation_must_make_new_progress(self):
        trainer = TinyTrainer()
        trainer.train(5)
        job = self.finished_job('j', 'colab')
        before = self.checkpoint(trainer, 'ck-a', 'colab', job)
        same = dict(before, checkpoint_id='ck-b')
        self.assertFalse(verify_continuation(before, same))


if __name__ == '__main__':
    unittest.main()
