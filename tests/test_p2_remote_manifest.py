"""P2.5-02/03: off-machine checkpoint manifest, immutable publish, resume selection, retention, rclone store."""
import json
import tempfile
import unittest
from pathlib import Path

from core.artifact_store import (LocalDirStore, RcloneStore, compatibility, latest_resumable, prune,
                                 publish_checkpoint, published_manifests, safe_relative, validate_manifest)

FIELDS = dict(task_id=7, logical_run_id='ddpm-1', job_id='job-a', backend='kaggle', code_hash='c1', data_hash='d1',
              config_hash='k1', environment_hash='e1', checkpoint_schema='torch-v1')


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = LocalDirStore(self.root / 'remote')
        self.local = self.root / 'local'
        self.local.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def files(self, step):
        model = self.local / 'model.pt'
        model.write_bytes(b'weights-%d' % step * 100)
        metrics = self.local / 'metrics.json'
        metrics.write_text(json.dumps({'step': step}), encoding='utf-8')
        return [model, metrics]

    def publish(self, cid, step, **extra):
        return publish_checkpoint(self.store, 'workflow-runs/7/ddpm-1', cid, self.files(step),
                                  {**FIELDS, 'global_step': step, **extra}, kinds={'metrics.json': 'metrics'},
                                  light=('metrics.json',))

    def test_publish_writes_verified_manifest_last_and_is_immutable(self):
        manifest = self.publish('ck-0100', 100)
        self.assertEqual(manifest['publication_state'], 'PUBLISHED')
        self.assertEqual({f['kind'] for f in manifest['files']}, {'checkpoint', 'metrics'})
        self.assertEqual([f['local_download'] for f in manifest['files'] if f['kind'] == 'metrics'], [True])
        validate_manifest(manifest)
        with self.assertRaises(ValueError):
            self.publish('ck-0100', 100)

    def test_interrupted_upload_never_becomes_resumable(self):
        self.publish('ck-0100', 100)
        broken = LocalDirStore(self.root / 'remote')
        real_put = broken.put
        calls = []

        def failing_put(local, rel):
            calls.append(rel)
            if len(calls) == 2:
                raise OSError('network dropped')
            return real_put(local, rel)
        broken.put = failing_put
        with self.assertRaises(OSError):
            publish_checkpoint(broken, 'workflow-runs/7/ddpm-1', 'ck-0200', self.files(200), {**FIELDS, 'global_step': 200})
        self.assertEqual(latest_resumable(self.store, 'workflow-runs/7/ddpm-1')['checkpoint_id'], 'ck-0100')

    def test_resume_requires_matching_identity_and_present_files(self):
        self.publish('ck-0100', 100)
        self.publish('ck-0200', 200)
        self.assertEqual(latest_resumable(self.store, 'workflow-runs/7/ddpm-1', code_hash='c1')['checkpoint_id'], 'ck-0200')
        self.assertIsNone(latest_resumable(self.store, 'workflow-runs/7/ddpm-1', code_hash='other'))
        # A file of the newest checkpoint went missing -> fall back to the previous verified one.
        (self.root / 'remote/workflow-runs/7/ddpm-1/checkpoints/ck-0200/model.pt').unlink()
        self.assertEqual(latest_resumable(self.store, 'workflow-runs/7/ddpm-1')['checkpoint_id'], 'ck-0100')
        self.assertEqual(compatibility({'data_hash': 'd1'}, data_hash='d2'), ['data_hash'])

    def test_retention_keeps_newest_and_protected(self):
        for step in (100, 200, 300, 400, 500):
            self.publish(f'ck-{step:04d}', step)
        removed = prune(self.store, 'workflow-runs/7/ddpm-1', keep=3, protect={'ck-0100'})
        self.assertTrue(all('/ck-0200/' in r for r in removed))
        self.assertEqual(len(published_manifests(self.store, 'workflow-runs/7/ddpm-1')), 5)  # manifests stay

    def test_path_escape_and_bad_manifest_rejected(self):
        for bad in ('../x', '/abs/x', 'a/../b', 'C:/x', ''):
            with self.assertRaises(ValueError):
                safe_relative(bad)
        with self.assertRaises(ValueError):
            validate_manifest({'manifest_version': 1, 'task_id': 1, 'logical_run_id': 'r', 'checkpoint_id': 'c',
                               'publication_state': 'PUBLISHED', 'files': [{'uri': 'x'}]})


class RcloneStoreTests(unittest.TestCase):
    def test_commands_folder_id_and_upload_verification(self):
        calls = []
        sizes = {}

        def run(argv, timeout=3600):
            calls.append(argv)
            if argv[1] == 'copyto' and Path(argv[2]).exists():
                sizes[argv[3]] = Path(argv[2]).stat().st_size
                return ''
            if argv[1] == 'lsjson':
                target = argv[-3] if '--drive-root-folder-id' in argv else argv[-1]
                return json.dumps([{'Path': 'model.pt', 'Size': sizes.get(target, 0), 'ID': 'drive-id-1',
                                    'Hashes': {'md5': ''}}]) if target in sizes else '[]'
            return ''
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / 'model.pt'
            local.write_bytes(b'0123456789')
            store = RcloneStore('gdrive', base='workflow-runs', root_folder_id='1MbxfEz_tOOUknq-M5A0ELe4hMWzOUv-_', run=run)
            info = store.put(local, '7/ddpm-1/checkpoints/ck/model.pt')
            self.assertEqual(info['bytes'], 10)
            self.assertEqual(info['remote_id'], 'drive-id-1')
            self.assertEqual(calls[0][:4], ['rclone', 'copyto', str(local), 'gdrive:workflow-runs/7/ddpm-1/checkpoints/ck/model.pt'])
            self.assertIn('--drive-root-folder-id', calls[0])
            with self.assertRaises(RuntimeError):  # the remote reports a short file after upload
                self._verify(store, local)
        with self.assertRaises(ValueError):
            RcloneStore('bad remote!')

    def _verify(self, store, local):
        store.run = lambda argv, timeout=3600: '' if argv[1] == 'copyto' else json.dumps([{'Path': 'm', 'Size': 3}])
        store.put(local, 'x/model.pt')


if __name__ == '__main__':
    unittest.main()
