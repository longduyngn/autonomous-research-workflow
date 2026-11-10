"""P2.7-01: mini-DDPM example — checkpoint protocol (always) and a torch CPU resume smoke (when torch exists)."""
import importlib.util
import json
import os
import py_compile
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / 'examples' / 'p2_mini_ddpm'
spec = importlib.util.spec_from_file_location('ddpm_checkpointing', EXAMPLE / 'checkpointing.py')
checkpointing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checkpointing)


class ProtocolTests(unittest.TestCase):
    def test_train_script_compiles(self):
        py_compile.compile(str(EXAMPLE / 'train.py'), doraise=True)

    def test_publish_latest_restore_matches_controller_layout(self):
        from core.artifact_store import LocalDirStore, latest_resumable
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = checkpointing.SharedStore(f'file:{root / "drive" / "runs" / "r1"}')
            for step in (4, 8):
                path = checkpointing.save_local({'step': step, 'w': [step] * 3}, root / 'work' / 'state.pt')
                metrics = root / 'work' / 'metrics.json'
                metrics.write_text(json.dumps({'step': step}), encoding='utf-8')
                store.publish(f'ck-{step:07d}', [path, metrics], {'task_id': 1, 'logical_run_id': 'r1', 'job_id': 'j',
                              'backend': 'local', 'global_step': step, 'kinds': {'metrics.json': 'metrics'},
                              'light': ('metrics.json',)})
            latest = store.latest()
            self.assertEqual(latest['global_step'], 8)
            restored = store.restore(latest, 'state.pt', root / 'restored.pt')
            import pickle
            self.assertEqual(pickle.loads(restored.read_bytes())['step'], 8)
            # The controller reads the same layout.
            controller_view = latest_resumable(LocalDirStore(root / 'drive'), 'runs/r1')
            self.assertEqual(controller_view['checkpoint_id'], 'ck-0000008')
            self.assertEqual([f['local_download'] for f in latest['files']], [False, True])

    def test_progress_line_format(self):
        import io
        from contextlib import redirect_stdout
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            checkpointing.progress('job-1', 3, 10, loss=0.5)
        line = buffer.getvalue().strip()
        self.assertTrue(line.startswith('WORKFLOW_PROGRESS: '))
        self.assertEqual(json.loads(line.split(': ', 1)[1])['completed'], 3)


@unittest.skipUnless(importlib.util.find_spec('torch'), 'torch not installed locally (runs on Colab/Kaggle)')
class TorchSmokeTests(unittest.TestCase):
    def test_interrupted_and_resumed_run_finishes_with_published_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, 'WORKFLOW_CHECKPOINT_URI': f'file:{Path(tmp) / "store"}'}
            base = [sys.executable, str(EXAMPLE / 'train.py'), '--smoke', '--ckpt-every', '4', '--batch', '16', '--work', tmp]
            subprocess.run(base + ['--steps', '4'], env=env, check=True, capture_output=True, timeout=600)
            done = subprocess.run(base + ['--steps', '8'], env=env, check=True, capture_output=True, text=True, timeout=600)
            self.assertIn('resumed from ck-0000004 at step 4', done.stdout)
            self.assertIn('"state": "COMPLETED"', done.stdout)


if __name__ == '__main__':
    unittest.main()
