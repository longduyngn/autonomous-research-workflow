import tempfile
import unittest
from core.resource_gate import ColabGate, ResourceBusy


class ResourceGateTests(unittest.TestCase):
    def test_http_gateway_requires_current_generation_for_browser_effects(self):
        import json
        import threading
        import urllib.request
        import urllib.error
        import sys
        from unittest.mock import patch
        from core.colab_bridge import serve_colab_bridge
        with tempfile.TemporaryDirectory() as tmp,patch.dict('os.environ',{'COLAB_BRIDGE_TOKEN':'fixture','COLAB_BROWSER':''}),patch('core.colab_bridge.browser_action',return_value={'runtime_stopped':True,'active_session_count':0}) as browser:
            server=serve_colab_bridge({'colab_command':[sys.executable]},port=0,start=False,controller_root=tmp)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
            def post(path,owner='a'*64,generation=-1,data=None):
                request=urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+path,
                    data=json.dumps(data or {}).encode(),headers={'Authorization':'Bearer fixture',
                    'X-Workflow-Session':owner,'X-Workflow-Generation':str(generation)})
                with urllib.request.urlopen(request) as response:return json.load(response)
            token=post('/claim')['generation']
            with self.assertRaises(urllib.error.HTTPError):post('/claim',owner='b'*64)
            browser.reset_mock()
            with self.assertRaises(urllib.error.HTTPError):post('/browser',generation=token-1,data={'name':'set_runtime','arguments':{'hardware':'T4'}})
            browser.assert_not_called()
            post('/browser',generation=token,data={'name':'set_runtime','arguments':{'hardware':'CPU'}})
            browser.assert_called_once_with('set_runtime',{'hardware':'CPU'})
            with patch('core.colab_bridge.StdioMCPClient') as mcp_cls:
                client = mcp_cls.return_value
                client.proc.poll.return_value = None
                client.tools.return_value = [{'name': 'run_code_cell'}]
                self.assertEqual(len(post('/tools', generation=token)['tools']), 1)
                with self.assertRaises(urllib.error.HTTPError):
                    post('/release', generation=token-1, data={'force': True})
                client.close.assert_not_called()
                rel = post('/release', generation=token, data={'force': True})
                self.assertEqual(rel['status'], 'released')
                client.close.assert_called_once()
                next_token = post('/claim', owner='b'*64)['generation']
                self.assertGreater(next_token, token)

    def test_forced_release_closes_transport_atomically_inside_gate_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            events=[]
            def observe():
                events.append('observe')
                return {'runtime_stopped':True,'active_session_count':0}
            gate=ColabGate(tmp,observe)
            contender=ColabGate(tmp,observe)
            gen=gate.claim('one')['generation']
            events.clear()
            with self.assertRaises(PermissionError):
                gate.release('one',gen-1,before_release=lambda:events.append('closed'))
            self.assertEqual(events,[])
            def close_while_locked():
                with self.assertRaises(ResourceBusy):
                    contender.claim('two')
                with self.assertRaises(ResourceBusy):
                    contender.execute('one',gen,lambda:1)
                events.append('closed')
            res=gate.release('one',gen,before_release=close_while_locked)
            self.assertEqual(res['status'],'released')
            self.assertEqual(events,['closed','observe'])
            second=contender.claim('two')
            self.assertEqual(second['status'],'claimed')
            self.assertGreater(second['generation'],gen)

    def test_restart_fencing_and_release_requires_real_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            observation={'runtime_stopped':True,'active_session_count':0}
            gate=ColabGate(tmp,lambda:observation)
            first=gate.claim('one')['generation']
            other=ColabGate(tmp,lambda:observation)
            with self.assertRaises(ResourceBusy):other.claim('two')
            self.assertEqual(other.claim('one')['generation'],first)
            observation={'runtime_stopped':False,'active_session_count':1}
            self.assertEqual(gate.release('one',first)['status'],'ownership_retained')
            with self.assertRaises(ResourceBusy):other.claim('two')
            self.assertEqual(other.execute('one',first,lambda:'cleanup'),'cleanup')
            observation={'runtime_stopped':True,'active_session_count':0}
            self.assertTrue(gate.release('one',first)['runtime_stopped'])
            second=other.claim('two')['generation']
            with self.assertRaises(PermissionError):gate.execute('one',first,lambda:self.fail('stale execution'))
            self.assertGreater(second,first)

    def test_unknown_runtime_is_never_free_and_cross_process_lock_covers_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            observation={}
            one=ColabGate(tmp,lambda:observation);two=ColabGate(tmp,lambda:observation)
            with self.assertRaises(ResourceBusy):one.claim('one')
            observation={'runtime_stopped':True,'active_session_count':0}
            generation=one.claim('one')['generation']
            with one.locked():
                with self.assertRaises(ResourceBusy):two.execute('one',generation,lambda:1)

    def test_busy_backend_waits_without_model_or_retry(self):
        from pathlib import Path
        from unittest.mock import patch
        from core.database import WorkspaceDatabase
        from core.orchestrator import HybridOrchestrator
        from core.logger import ContextLogManager
        from test_workflow import FakeRunner
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db=WorkspaceDatabase(root/'db');runner=FakeRunner()
            orch=HybridOrchestrator(root,{'isolation':{'enabled':False}},db,ContextLogManager(root/'logs'),runner)
            task=orch.submit_task('cloud')
            db.update_task(task.id,status='OPERATING',plan_json='{"backend":"colab"}')
            with patch('core.cloud.CloudTools.prepare_colab_bridge',side_effect=ResourceBusy('busy')), \
                 patch('core.cloud.CloudTools._ensure_colab_bridge',side_effect=AssertionError('must not start host bridge')), \
                 patch('core.cloud.CloudTools.colab_release',return_value={'status':'bridge_not_running','runtime_stopped':False}):
                result=orch.run_task_to_completion(task.id)
            self.assertEqual(result.status,'WAITING_RESOURCE')
            self.assertEqual(result.retry_count,0)
            self.assertFalse(runner.calls)
            self.assertFalse(db.get_task(task.id).lease_owner)
