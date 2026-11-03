import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from core.checkpoints import CheckpointStore,AutoCheckpoint
from core.cloud import CloudTools

META={'step':40,'code_sha256':'a'*64,'data_sha256':'b'*64,'config_sha256':'c'*64}


class CheckpointTests(unittest.TestCase):
    def test_real_process_loss_then_restore_rng_and_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();vm=root/'vm';vm.mkdir();durable=root/'durable'
            program='''import json,random,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from core.checkpoints import CheckpointStore,AutoCheckpoint
r=random.Random(42);total=0
for i in range(40): total+=r.random()
Path('state.json').write_text(json.dumps({'step':40,'total':total,'rng':r.getstate()}))
AutoCheckpoint(CheckpointStore(sys.argv[2])).save('.', ['state.json'],json.loads(sys.argv[3]))
os._exit(17)
'''
            run=subprocess.run([sys.executable,'-c',program,str(Path(__file__).resolve().parents[1]),str(durable),json.dumps(META)],cwd=vm,capture_output=True)
            self.assertEqual(run.returncode,17,run.stderr.decode())
            self.assertTrue(vm.is_relative_to(root));shutil.rmtree(vm)
            store=CheckpointStore(durable);checkpoint=store.list()[0]
            restored=root/'new-vm';store.restore(checkpoint['id'],restored,META)
            resume='''import json,random
from pathlib import Path
s=json.loads(Path('state.json').read_text());r=random.Random()
r.setstate((s['rng'][0],tuple(s['rng'][1]),s['rng'][2]))
for i in range(s['step'],100):s['total']+=r.random()
print(json.dumps({'step':100,'total':s['total']}))
'''
            actual=subprocess.run([sys.executable,'-c',resume],cwd=restored,capture_output=True,check=True)
            r=random.Random(42);expected=sum(r.random() for _ in range(100))
            self.assertEqual(json.loads(actual.stdout),{'step':100,'total':expected})

    def test_corruption_incomplete_upload_and_compatibility_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'vm';source.mkdir();(source/'state').write_bytes(b'actual state')
            store=CheckpointStore(root/'store');m=store.publish(source,['state'],META)
            (store.root/'.incoming-incomplete').mkdir()
            self.assertEqual(len(store.list()),1)
            with self.assertRaisesRegex(ValueError,'Incompatible'):
                store.restore(m['id'],root/'new',{'data_sha256':'d'*64})
            (store.root/m['id']/'files/state').write_bytes(b'corrupted')
            with self.assertRaisesRegex(ValueError,'Corrupt'):store.verify(m['id'])
            self.assertEqual(store.list(),[])

    def test_timer_and_explicit_milestone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'state').write_text('state')
            auto=AutoCheckpoint(CheckpointStore(root/'durable'),60)
            with patch('core.checkpoints.time.monotonic',side_effect=[0,10,11]):
                self.assertIsNotNone(auto.save(root,['state'],META))
                self.assertIsNone(auto.save(root,['state'],META))
                self.assertIsNotNone(auto.save(root,['state'],META,force=True))

    def test_cloud_tools_retrieve_from_controller_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);vm=root/'task';vm.mkdir();(vm/'state.json').write_text('{}')
            tools=CloudTools(vm,{'cloud':{'checkpoint_directory':str(root/'durable')}})
            m=tools.call('checkpoint_publish',{'files':['state.json'],'metadata':META})['checkpoint']
            self.assertEqual(len(tools.call('checkpoint_list',{})['checkpoints']),1)
            tools.call('checkpoint_restore',{'checkpoint_id':m['id'],'destination':'recovered','expected':META})
            self.assertEqual((vm/'recovered/state.json').read_text(),'{}')
            with self.assertRaises(ValueError):tools.checkpoint_restore({'checkpoint_id':m['id'],'destination':'other','expected':{}})
            with self.assertRaises(PermissionError):tools.checkpoint_publish({'files':['../secret'],'metadata':META})
