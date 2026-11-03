import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from core.role_memory import RoleMemory
from core.providers import EphemeralAgentRunner
from core.database import WorkspaceDatabase
from core.control import ControlPlane


class ResumableTests(unittest.TestCase):
    def test_successful_resume_preserves_id_and_context_rotation_writes_handover(self):
        from core.models import AgentRunResult
        with tempfile.TemporaryDirectory() as tmp:
            memory=RoleMemory(tmp,'researcher');identity={'provider':'fixture'}
            state=memory.load(identity);state['session_id']='stable-session'
            memory.save(state,AgentRunResult('researcher','fixture',True,'continued',structured_data={'usage':{'input_tokens':65000}}))
            self.assertEqual(json.loads(memory.path.read_text())['session_id'],'stable-session')
            next_state=memory.load(identity)
            self.assertEqual(next_state['session_id'],'')
            self.assertEqual(next_state['generation'],2)
            self.assertTrue((memory.directory/'handover.json').exists())
    def test_four_roles_keep_separate_portable_chats(self):
        with tempfile.TemporaryDirectory() as tmp:
            roles=('communicator','operator','validator','researcher')
            cfg={'providers':{'m':{'type':'mock'}},'roles':{r:{'provider':'m'} for r in roles}}
            runner=EphemeralAgentRunner(tmp,cfg)
            for role in roles:
                runner.run_role(role,'turn1')
                runner.run_role(role,'turn2')
            for role in roles:
                memory=RoleMemory(tmp,role)
                self.assertEqual(json.loads(memory.path.read_text())['passes'],2)
                self.assertEqual(len(memory.context().splitlines()),2)

    def test_role_lock_prevents_concurrent_resume_and_releases_after_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            one=RoleMemory(tmp,'operator');two=RoleMemory(tmp,'operator')
            with one.locked():
                with self.assertRaises(OSError):
                    with two.locked():pass
            with two.locked():pass

    def test_failed_role_invalidates_native_session_without_replaying(self):
        from core.models import AgentRunResult
        with tempfile.TemporaryDirectory() as tmp:
            memory=RoleMemory(tmp,'operator');state=memory.load({'provider':'one'})
            state['session_id']='old'
            memory.save(state,AgentRunResult('operator','one',False,'',error='lost session'))
            self.assertEqual(memory.load({'provider':'one'})['session_id'],'')
            self.assertIn('lost session',memory.context())

    def test_role_context_and_native_claude_resume_are_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            from core.integrations import prepare_integrations
            controller=Path(__file__).resolve().parents[1]
            (root/'.agents').mkdir()
            prepare_integrations(controller,root,root)
            cfg={'providers':{'c':{'type':'claude_cli'}},'roles':{r:{'provider':'c'} for r in ('researcher','validator')}}
            runner=EphemeralAgentRunner(root,cfg)
            runner.instructions_root=controller
            seen=[]
            def process(cmd,*a,**kw):
                import sys
                if cmd[0] == sys.executable:
                    return {'exit_code':0,'stdout':'','stderr':''}
                seen.append(cmd)
                with (root/'.workflow/hook-events.jsonl').open('a',encoding='utf8') as f:
                    f.write(json.dumps({'run_id':kw['env']['WORKFLOW_RUN_ID'],'event':'SessionStart'})+'\n')
                return {'exit_code':0,'stdout':json.dumps({'session_id':'session-'+str(len(seen)),'result':'VERDICT: PASS\nEvidence: checked'}),'stderr':''}
            with patch('core.providers.find_executable',return_value='claude'),patch('core.providers.run_process',side_effect=process):
                for n,role in enumerate(('researcher','researcher','validator')):
                    self.assertTrue(runner.run_role(role,'fresh state',root/f'run{n}.log').success)
            self.assertNotIn('--resume',seen[0])
            self.assertIn('session-1',seen[1])
            self.assertNotIn('--resume',seen[2])
            memory=RoleMemory(root,'researcher')
            self.assertIn('checked',memory.context())
            self.assertEqual(memory.load({'changed_provider':True})['session_id'],'')

    def test_resource_survives_restart_and_fences_stale_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=WorkspaceDatabase(Path(tmp)/'db');cp=ControlPlane(db)
            generation=cp.claim_resource('colab','one')
            other=ControlPlane(WorkspaceDatabase(Path(tmp)/'db'))
            self.assertIsNone(other.claim_resource('colab','two'))
            other.observe_resource('colab',{'runtime_stopped':False},'one',generation)
            self.assertIsNone(cp.claim_resource('colab','two'))
            cp.observe_resource('colab',{'runtime_stopped':True,'active_session_count':0},'one',generation)
            self.assertIsNotNone(cp.claim_resource('colab','two'))
            with self.assertRaises(PermissionError):cp.check_resource('colab','one',generation)

    def test_approval_exact_action_expiry_and_one_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            cp=ControlPlane(WorkspaceDatabase(Path(tmp)/'db'))
            action={'argv':['python','train.py'],'revision':'abc'}
            key=cp.request_approval(1,'alice',action)
            self.assertFalse(cp.decide(key,'bob','APPROVED'))
            self.assertTrue(cp.decide(key,'alice','APPROVED'))
            self.assertFalse(cp.consume_approval(key,1,{'argv':['other']}))
            self.assertTrue(cp.consume_approval(key,1,action))
            self.assertFalse(cp.consume_approval(key,1,action))

    def test_wait_survives_restart_without_worker_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=WorkspaceDatabase(Path(tmp)/'db');cp=ControlPlane(db)
            task=db.create_task('one')
            cp.wait(task.id,'RESOURCE','OPERATING','busy','colab',0)
            self.assertIsNone(db.claim_task('worker',2))
            self.assertTrue(ControlPlane(db).wake(task.id))
            self.assertEqual(db.claim_task('worker',2).status,'OPERATING')
