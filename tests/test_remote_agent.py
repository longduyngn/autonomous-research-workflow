import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core.remote_agent import Mailbox, RemoteManager, ColabAgentTransport
from core.role_memory import RoleMemory

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('remote_worker_test',ROOT/'scripts/remote_agy_worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class RemoteAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.transport = Mock()
        self.manager = RemoteManager(ROOT,self.directory,{},self.transport)

    def test_same_id_cannot_change_instruction(self):
        request = {'request_id':'abc','message':'original'}
        self.manager.mailbox.put(request)
        with self.assertRaises(ValueError):
            self.manager.mailbox.put(dict(request,message='changed'))
        self.assertEqual(self.manager.mailbox.get('abc')['payload'],request)

    def test_transport_failure_is_unknown_and_never_replayed(self):
        self.transport.send.side_effect = TimeoutError('reply lost')
        a = self.manager.send('work','abc')
        b = self.manager.send('work','abc')
        self.assertEqual((a['state'],b['state']),('UNKNOWN','UNKNOWN'))
        self.transport.send.assert_called_once()

    def test_poll_reconciles_lost_submission(self):
        self.transport.send.side_effect = TimeoutError()
        self.manager.send('work','abc')
        self.transport.poll.return_value = {'request_id':'abc','state':'COMPLETED','output':'evidence','boot_id':'vm1'}
        self.assertEqual(self.manager.poll('abc')['output'],'evidence')

    def test_missing_receipt_does_not_mean_safe_to_replay(self):
        self.transport.send.return_value = {'request_id':'abc','state':'RUNNING','boot_id':'vm1'}
        self.manager.send('work','abc')
        self.transport.poll.return_value = {'request_id':'abc','state':'MISSING','boot_id':'vm2'}
        self.assertEqual(self.manager.poll('abc')['state'],'UNKNOWN')
        self.manager.send('work','abc')
        self.transport.send.assert_called_once()

    def test_replaced_vm_cannot_complete_old_request(self):
        self.transport.send.return_value = {'request_id':'abc','state':'RUNNING','boot_id':'vm1'}
        self.manager.send('work','abc')
        self.transport.poll.return_value = {'request_id':'abc','state':'COMPLETED','boot_id':'vm2'}
        self.assertEqual(self.manager.poll('abc')['state'],'UNKNOWN')

    def test_id_rejects_path_traversal_before_transport(self):
        with self.assertRaises(ValueError): self.manager.send('work','../../bad')
        self.transport.send.assert_not_called()

    def test_manager_memory_is_distinct_from_communicator(self):
        manager = RoleMemory(self.directory,'remote_manager')
        other = RoleMemory(self.directory,'communicator')
        self.assertNotEqual(manager.path,other.path)
        with manager.locked():
            self.assertEqual(manager.load({'provider':'agy'})['session_id'],'')

    def test_auth_url_strips_terminal_hyperlink_terminator(self):
        result = ColabAgentTransport._auth_status({'authorization_url':'https://accounts.google.com/auth?state=abc\x07junk'})
        self.assertEqual(result['authorization_url'],'https://accounts.google.com/auth?state=abc')

    def test_remote_duplicate_launches_only_once(self):
        request = {'request_id':'abc','message':'work','model':'explicit-model'}
        fake = Mock(pid=1234)
        with patch.object(worker.subprocess,'Popen',return_value=fake) as spawn:
            worker.submit(self.directory,request)
            worker.submit(self.directory,request)
            spawn.assert_called_once()
        with self.assertRaises(ValueError): worker.submit(self.directory,dict(request,message='other'))

    def test_remote_rejects_path_and_unbounded_timeout(self):
        for request in ({'request_id':'../bad','message':'work','model':'m'},
                        {'request_id':'abc','message':'work','model':'m','timeout_seconds':0}):
            with self.assertRaises(ValueError): worker.submit(self.directory,request)

    def test_stopped_remote_worker_is_unknown_not_failed_retryable(self):
        p = worker.job_path(self.directory,'abc')
        worker.atomic(p,{'request_id':'abc','state':'RUNNING','created_at':time.time()-60,
                         'pid':123456789,'request':{'message':'do not export'}})
        with patch.object(worker.os,'kill',side_effect=ProcessLookupError()):
            result = worker.status(self.directory,'abc')
        self.assertEqual(result['state'],'UNKNOWN')
        self.assertNotIn('request',result)

    def test_terminal_receipt_is_stable_on_poll(self):
        worker.atomic(worker.job_path(self.directory,'abc'),{
            'request_id':'abc','state':'COMPLETED','output':'verified','created_at':0})
        with patch.object(worker.os,'kill') as probe:
            self.assertEqual(worker.status(self.directory,'abc')['state'],'COMPLETED')
            probe.assert_not_called()

    def test_cancel_is_a_flag_for_the_owner_not_a_stale_pid_kill(self):
        worker.atomic(worker.job_path(self.directory,'abc'),{'request_id':'abc',
            'state':'QUEUED','created_at':time.time(),'boot_id':worker.boot_id()})
        with patch.object(worker.os,'kill') as kill:
            self.assertEqual(worker.cancel(self.directory,'abc')['state'],'CANCEL_REQUESTED')
            kill.assert_not_called()
        self.assertTrue(worker.job_path(self.directory,'abc').with_suffix('.cancel.json').exists())

    def test_cancel_does_not_change_a_completed_result(self):
        worker.atomic(worker.job_path(self.directory,'abc'),{'request_id':'abc','state':'COMPLETED','output':'done'})
        self.assertEqual(worker.cancel(self.directory,'abc')['state'],'COMPLETED')
        self.assertFalse(worker.job_path(self.directory,'abc').with_suffix('.cancel.json').exists())

    def test_oauth_code_is_only_ciphertext_in_notebook_source(self):
        import base64
        from cryptography.hazmat.primitives import serialization,hashes
        from cryptography.hazmat.primitives.asymmetric import rsa,padding
        key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
        transport = ColabAgentTransport(ROOT,self.directory,{},cloud=Mock())
        transport.login_status = Mock(return_value={'public_key':key.public_key().public_bytes(
            serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()})
        transport.rpc = Mock(return_value={'state':'INPUT_SENT'})
        secret = 'synthetic-one-time-code-do-not-log'
        transport.login_code(secret)
        source = transport.rpc.call_args.args[0]
        self.assertNotIn(secret,source)
        ciphertext = source.split("submit('",1)[1].split("')",1)[0]
        decoded = key.decrypt(base64.b64decode(ciphertext),padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
        self.assertEqual(decoded.decode(),secret)

    def test_local_agy_inline_prompt_needs_no_file_read_tool(self):
        from core.providers import EphemeralAgentRunner
        runner = EphemeralAgentRunner(self.directory,{})
        observed = {}
        def process(argv,cwd,**kwargs):
            observed['argv'] = argv
            return {'exit_code':0,'stdout':json.dumps({'result':'ready','conversationId':'test-session'}),'stderr':''}
        with patch('core.providers.find_executable',return_value='agy.exe'), \
             patch('core.providers.run_process',side_effect=process):
            result = runner._run_cli('remote_manager','agy',{
                'type':'agy_cli','inline_prompt':True,'sandbox':True,'mode':'plan'},
                {},'Inline instruction only',10,self.directory/'inline.log')
        self.assertTrue(result.success)
        self.assertEqual(observed['argv'][observed['argv'].index('-p')+1],'Inline instruction only')
        self.assertNotIn('--dangerously-skip-permissions',observed['argv'])

    def test_denied_native_action_cannot_be_task_completion(self):
        native = {'status':'SUCCESS','response':'','denied_actions':[{'action':'command'}]}
        self.assertEqual(worker.parse_native(json.dumps(native),0)[1],'WAITING_APPROVAL')

    def test_native_metadata_without_response_cannot_complete(self):
        for native in ({'status':'SUCCESS','response':''},{'conversation_id':'id','usage':{'input_tokens':123}}):
            self.assertEqual(worker.parse_native(json.dumps(native),0)[1],'FAILED')

    def test_valid_native_response_and_explicit_error(self):
        self.assertEqual(worker.parse_native(json.dumps({'response':'actual response','status':'SUCCESS'}),0)[1],'COMPLETED')
        self.assertEqual(worker.parse_native(json.dumps({'response':'oops','is_error':True}),0)[1],'FAILED')
        self.assertEqual(worker.parse_native('not JSON',0)[1],'FAILED')

    def test_generic_agy_adapter_rejects_denied_actions(self):
        from core.providers import EphemeralAgentRunner
        native = {'status':'SUCCESS','response':'','denied_actions':[{'action':'command'}]}
        with patch('core.providers.find_executable',return_value='agy.exe'), \
             patch('core.providers.run_process',return_value={'exit_code':0,'stdout':json.dumps(native),'stderr':''}):
            result = EphemeralAgentRunner(self.directory,{})._run_cli('remote_manager','agy',{'type':'agy_cli'},
                {},'probe',10,self.directory/'denied.log')
        self.assertFalse(result.success)
        self.assertIn('denied',result.error)


if __name__ == '__main__': unittest.main()
