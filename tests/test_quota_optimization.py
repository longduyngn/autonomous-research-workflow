import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from core.artifacts import register, verify
from core.campaign_tools import status_digest, update_status, write_checkpoint
from core.cloud import CloudTools
from core.colab_snippet import run_snippet
from core.configuration import role_turn_budget, validate_config
from core.database import WorkspaceDatabase
from core.log_tools import tail_log, search_log
from core.models import AgentRunResult
from core.providers import EphemeralAgentRunner
from core.quota import QuotaStore, persist_claude_run, record_result
from core.repo_fetch import fetch_repo
from core.tools import AgentTools


class ArtifactAndLogTests(unittest.TestCase):
    def test_receipt_schema_old_new_missing_and_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'x').write_text('42')
            row = register(root, 'answer', 'x', {'source': 'test'})
            self.assertEqual(row['bytes'], 2)
            self.assertTrue(verify(root)['ok'])
            receipt = root / '.workflow/artifact-receipts.jsonl'
            receipt.write_text(json.dumps({k: row[k] for k in ('path','bytes','sha256')}) + '\n')
            self.assertTrue(verify(root)['ok'])
            (root / 'x').write_text('43')
            self.assertEqual(verify(root)['results'][0]['status'], 'hash-mismatch')
            (root / 'x').unlink()
            self.assertEqual(verify(root)['results'][0]['status'], 'missing')

    def test_paths_secret_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            (Path(outside) / 'secret').write_text('x')
            for path in ('../x', str(Path(outside) / 'secret'), '.env', '.AWS/credentials'):
                with self.subTest(path=path), self.assertRaises(PermissionError):
                    register(root, 'x', path)
            try:
                (root / 'link').symlink_to(outside, target_is_directory=True)
            except OSError:
                return
            with self.assertRaises(PermissionError):
                register(root, 'x', 'link/secret')

    def test_large_logs_bounded_tail_search_missing_invalid_regex(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'run.log'
            with path.open('w') as stream:
                for i in range(100000):
                    stream.write(f'line {i}\n')
            result = tail_log(path, 3)
            self.assertEqual(result['lines'], ['line 99997','line 99998','line 99999'])
            self.assertLessEqual(result['bytes_read'], 262144)
            result = search_log(path, r'^line 4$', 2)
            self.assertEqual(result['matches'], [{'line_no':5,'text':'line 4'}])
            with self.assertRaises(FileNotFoundError):
                tail_log(Path(tmp) / 'missing')
            with self.assertRaises(ValueError):
                search_log(path, '[')
            path.write_text('x' * 400000 + '\nneedle\n')
            self.assertLessEqual(tail_log(path)['bytes_read'], 262144)
            self.assertEqual(search_log(path,'needle')['matches'][0]['line_no'], 2)

    def test_communicator_has_only_read_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = AgentTools(tmp, 'communicator')
            self.assertEqual({t['function']['name'] for t in tools.schemas()},
                             {'read_file','list_files','tail_log','search_log'})
            with self.assertRaises(PermissionError):
                tools.call('register_artifact', {'path':'x','name':'x'})


class TelemetryTests(unittest.TestCase):
    def test_idempotent_but_same_session_new_invocation_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'events.db'
            usage = {'input_tokens':10,'cache_creation_input_tokens':20,
                     'cache_read_input_tokens':1000,'output_tokens':30}
            result = AgentRunResult('guardian','claude_cli',True,'done',structured_data={
                'run_id':'one','session_id':'same','model':'sonnet','usage':usage,'num_turns':2,'total_cost_usd':.05})
            persist_claude_run(path, result)
            persist_claude_run(path, result)
            db = WorkspaceDatabase(path)
            record_result(QuotaStore(db), {}, 'guardian', result)
            ledger = QuotaStore(db).ledger('claude')
            self.assertEqual(len(ledger), 1)
            self.assertEqual(ledger[0]['tokens'], 60)
            self.assertEqual(json.loads(ledger[0]['usage_json']), usage)
            with db._connect() as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM agent_runs').fetchone()[0], 1)
                self.assertEqual(c.execute('SELECT num_turns,total_cost_usd FROM agent_runs').fetchone()[:], (2,.05))
            result.structured_data['run_id'] = 'two'
            persist_claude_run(path, result)
            self.assertEqual(len(QuotaStore(db).ledger('claude')), 2)

    def test_existing_controller_run_is_updated(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = WorkspaceDatabase(Path(tmp) / 'db')
            rid = db.record_agent_spawn('operator','claude_cli')
            result = AgentRunResult('operator','claude_cli',False,'limit',structured_data={
                'run_id':'x','budget_exhausted':True,'usage':{'output_tokens':4},'num_turns':35})
            persist_claude_run(db.db_path, result, agent_run_id=rid)
            with db._connect() as c:
                rows = c.execute('SELECT status,num_turns FROM agent_runs').fetchall()
                self.assertEqual([tuple(r) for r in rows], [('BOUNDED',35)])

    def test_runner_automatically_records_claude_without_router(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = {'database_path':str(root/'events.db'),'providers':{'c':{'type':'claude_cli'}},
                   'roles':{'communicator':{'provider':'c'}}}
            raw = {'type':'result','subtype':'success','result':'OK','session_id':'abc',
                   'usage':{'input_tokens':1,'output_tokens':3},'num_turns':1,'total_cost_usd':.01}
            with patch('core.providers.find_executable',return_value='claude'), patch('core.providers.run_process',
                return_value={'exit_code':0,'stdout':json.dumps(raw),'stderr':''}):
                result = EphemeralAgentRunner(root,cfg).run_role('communicator','test')
            self.assertTrue(result.success, result.error)
            self.assertNotIn('telemetry_error', result.structured_data)
            self.assertEqual(QuotaStore(WorkspaceDatabase(root/'events.db')).ledger()[0]['tokens'], 4)


class ProviderBudgetTests(unittest.TestCase):
    def call(self, root, role, kind='claude_cli', native=None, provider=None):
        observed = {}
        def process(argv, cwd, **kwargs):
            observed['argv'] = argv
            return {'exit_code':0,'stderr':'','stdout':json.dumps(native or {
                'type':'result','result':'OK','session_id':'abc','num_turns':36,'usage':{}})}
        runner = EphemeralAgentRunner(root,{})
        with patch('core.providers.ACTION_ROLES',set()), patch('core.providers.find_executable',return_value='cli'), \
             patch('core.providers.run_process',side_effect=process):
            result = runner._run_cli(role,'test',{'type':kind,**(provider or {})},{},'test',10,Path(root)/f'{role}.log')
        return result, observed['argv']

    def test_soft_budget_permissions_all_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            for role, budget in [('communicator',5),('operator',35),('planner',25),('validator',15),('guardian',20)]:
                result, argv = self.call(tmp,role)
                self.assertNotIn('--max-turns', argv)
                self.assertEqual(result.structured_data['turn_budget'], budget)
                self.assertTrue(result.structured_data['turn_budget_exceeded'])
                allowed = argv[argv.index('--allowedTools')+1]
                if role == 'communicator':
                    self.assertEqual(allowed,'Read,Glob,Grep')
                    self.assertEqual(argv[argv.index('--permission-mode')+1], 'plan')
                if role == 'operator':
                    self.assertIn('Write', allowed)
                if role == 'validator':
                    self.assertIn('Bash', allowed)
                    self.assertNotIn('Write', allowed)

    def test_agy_plan_ignores_skip_and_rejects_extra_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, argv = self.call(tmp,'communicator','agy_cli',provider={'mode':'auto','skip_permissions':True})
            self.assertEqual(argv[argv.index('--mode')+1],'plan')
            self.assertNotIn('--dangerously-skip-permissions',argv)
            with self.assertRaises(ValueError):
                self.call(tmp,'communicator',provider={'extra_args':['--tools','Bash']})

    def test_permission_failure_not_hidden_by_max_turn_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            native = {'type':'result','subtype':'error_max_turns','is_error':True,
                      'permission_denials':[{'tool_name':'Bash'}],'session_id':'abc'}
            result,_ = self.call(tmp,'guardian',native=native)
            self.assertFalse(result.success)
            self.assertFalse(result.structured_data.get('budget_exhausted'))
            self.assertIn('denied', result.error)

    def test_guardian_never_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = EphemeralAgentRunner(tmp, {'providers':{'c':{'type':'mock'}},'roles':{'guardian':{'provider':'c'}}})
            def fake(*args):
                self.assertFalse(runner.role_session.get('session_id'))
                return AgentRunResult('guardian','c',True,'done',structured_data={'session_id':'native'})
            with patch.object(runner,'_run_role',side_effect=fake):
                runner.run_role('guardian','tick1')
                result = runner.run_role('guardian','tick2')
            self.assertFalse(result.structured_data['resume_requested'])


class CampaignTests(unittest.TestCase):
    def test_digest_byte_bound_unicode_missing_and_structured_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.assertLessEqual(len(status_digest(root).encode()),2048)
            (root/'claude_roles').mkdir()
            (root/'claude_roles/HANDOFF.md').write_text('Trạng thái mới ' * 5000,encoding='utf-8')
            (root/'resume_brief_20261108.md').write_text('Bàn giao ' * 5000,encoding='utf-8')
            (root/'campaign_status.json').write_text(json.dumps({'state':'COMPLETE','keep':42}))
            digest = status_digest(root)
            self.assertLessEqual(len(digest.encode()),2048)
            self.assertIn('COMPLETE',digest)
            update_status(root,'.',{'state':'PAUSED'})
            self.assertEqual(json.loads((root/'campaign_status.json').read_text())['keep'],42)
            row=write_checkpoint(root,'.',message_id='one',completed_action='checked',evidence=['campaign_status.json'])
            self.assertEqual(len(row['evidence'][0]['sha256']),64)
            with self.assertRaises(PermissionError):status_digest(root,'..')


class SnippetTests(unittest.TestCase):
    def test_snippet_http_endpoint_rejects_stale_fence(self):
        import threading
        import urllib.request
        import urllib.error
        import sys
        from core.colab_bridge import serve_colab_bridge
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'COLAB_BRIDGE_TOKEN':'fixture','COLAB_BROWSER':''}), \
             patch('core.colab_bridge.browser_action',return_value={'runtime_stopped':True,'active_session_count':0}), \
             patch('core.colab_snippet.run_snippet',return_value={'status':'completed'}) as snippet, \
             patch('core.colab_bridge.StdioMCPClient') as cls:
            server=serve_colab_bridge({'colab_command':[sys.executable]},port=0,start=False,controller_root=tmp)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            cls.return_value.proc.poll.return_value=None
            def post(endpoint,generation=-1):
                req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+endpoint,
                    data=json.dumps({'code':'print(42)'}).encode(),headers={'Authorization':'Bearer fixture',
                    'X-Workflow-Session':'a'*64,'X-Workflow-Generation':str(generation)})
                with urllib.request.urlopen(req) as response:return json.load(response)
            generation=post('/claim')['generation']
            with self.assertRaises(urllib.error.HTTPError):post('/snippet',generation-1)
            snippet.assert_not_called()
            self.assertEqual(post('/snippet',generation)['status'],'completed')
            snippet.assert_called_once()

    def client(self, fail=False):
        client = Mock()
        client.tools.return_value=[{'name':n} for n in ('get_cells','add_code_cell','run_code_cell','delete_cell')]
        def call(name,args,**kwargs):
            if name == 'get_cells':return {'structuredContent':{'cells':[]}}
            if name == 'add_code_cell':return {'structuredContent':{'newCellId':'temp'}}
            if name == 'run_code_cell':
                if fail:raise TimeoutError()
                return {'structuredContent':{'outputs':[{'name':'stdout','text':['42\n']}]}}
            return {}
        client.call.side_effect=call
        return client

    def test_bridge_success_cleanup_and_output_bound(self):
        client=self.client()
        result=run_snippet(client,lambda:{'connected':True,'idle':True},'print(42)')
        self.assertEqual((result['status'],result['stdout'],result['cleanup']),('completed','42','deleted'))
        self.assertEqual(client.call.call_args_list[-1].args[0],'delete_cell')

    def test_busy_unknown_or_disconnected_cannot_run(self):
        for state in ({},{'connected':False,'idle':True},{'connected':True,'idle':False}):
            client=self.client()
            with self.assertRaises(PermissionError):run_snippet(client,lambda:state,'x=1')
            client.call.assert_not_called()

    def test_timeout_retains_uncertain_cell_and_does_not_interrupt(self):
        client=self.client(True)
        result=run_snippet(client,lambda:{'connected':True,'idle':True},'x=1')
        self.assertEqual(result['status'],'unknown')
        self.assertEqual(len(client.call.call_args_list),3)

    def test_transport_failure_retains_cell_identity(self):
        client=self.client()
        normal=client.call.side_effect
        def call(name,args,**kwargs):
            if name=='run_code_cell':raise OSError('transport lost')
            return normal(name,args,**kwargs)
        client.call.side_effect=call
        result=run_snippet(client,lambda:{'connected':True,'idle':True},'x=1')
        self.assertEqual((result['status'],result['cell_id']),('unknown','temp'))
        self.assertNotIn('delete_cell',[c.args[0] for c in client.call.call_args_list])

    def test_missing_final_output_does_not_claim_cleanup(self):
        client=self.client()
        normal=client.call.side_effect
        def call(name,args,**kwargs):
            if name=='run_code_cell':return {'isError':True}
            return normal(name,args,**kwargs)
        client.call.side_effect=call
        result=run_snippet(client,lambda:{'connected':True,'idle':True},'x=1')
        self.assertEqual(result['status'],'unknown')
        self.assertNotEqual(result['cleanup'],'deleted')

    def test_cloud_requires_existing_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools=CloudTools(tmp,{})
            with patch.object(tools,'_colab') as call:
                with self.assertRaises(PermissionError):tools.colab_run_snippet({'code':'x=1'})
                call.assert_not_called()


class RepoTests(unittest.TestCase):
    def test_shallow_fetch_and_no_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            def process(argv,cwd,**kwargs):
                self.assertIn('--depth',argv)
                target=Path(argv[-1]);target.mkdir()
                (target/'setup.py').write_text('raise RuntimeError("never run")')
                return {'exit_code':0}
            with patch('core.repo_fetch.public_url',side_effect=lambda u:u), \
                 patch('core.repo_fetch.shutil.which',return_value='git'),patch('core.repo_fetch.run_process',side_effect=process):
                result=fetch_repo(tmp,'https://github.com/owner/repo','source')
            self.assertEqual(result['method'],'git-depth-1')
            self.assertIn('setup.py',Path(result['tree']).read_text())
            with self.assertRaises(ValueError):fetch_repo(tmp,'file:///private','source')
            with patch('core.repo_fetch.public_url',side_effect=lambda u:u),self.assertRaises(PermissionError):
                fetch_repo(tmp,'https://github.com/owner/repo','../escape')

    def test_tarball_fallback_and_path_traversal(self):
        for name,valid in [('prefix/file.txt',True),('prefix/../../escape',False)]:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as tmp:
                data=io.BytesIO()
                with tarfile.open(fileobj=data,mode='w:gz') as tar:
                    info=tarfile.TarInfo(name);info.size=2;tar.addfile(info,io.BytesIO(b'42'))
                opener=Mock();opener.open.return_value=io.BytesIO(data.getvalue())
                with patch('core.repo_fetch.public_url',side_effect=lambda u:u), \
                     patch('core.repo_fetch.shutil.which',return_value=None),patch('core.repo_fetch.urllib.request.build_opener',return_value=opener):
                    if valid:
                        result=fetch_repo(tmp,'https://github.com/owner/repo','source')
                        self.assertEqual(result['method'],'tarball')
                        self.assertEqual((Path(tmp)/'source/file.txt').read_text(),'42')
                    else:
                        with self.assertRaises(ValueError):fetch_repo(tmp,'https://github.com/owner/repo','source')
                        self.assertFalse((Path(tmp)/'source').exists())


class AuditTests(unittest.TestCase):
    def test_progressive_messages_tool_dedup_and_explicit_exclusion(self):
        from scripts.claude_usage_audit import audit
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def event(output):
                return {'type':'assistant','entrypoint':'sdk-cli','message':{'id':'message','model':'sonnet',
                    'usage':{'input_tokens':1,'output_tokens':output,'cache_read_input_tokens':100},
                    'content':[{'type':'tool_use','id':'tool','name':'Read','input':{'file_path':'run.log'}}]}}
            (root/'one.jsonl').write_text(json.dumps(event(2))+'\n'+json.dumps(event(5))+'\n{partial')
            (root/'excluded.jsonl').write_text(json.dumps(event(50)))
            result=audit(root,['excluded'])
            self.assertEqual(result['G'],{'inp':1,'cc':0,'cr':100,'out':5})
            self.assertEqual(result['rows'][0]['turns'],1)
            self.assertEqual(result['tools'],[('Read',1)])
            self.assertEqual(result['scope']['malformed_lines'],1)
            self.assertEqual(result['scope']['exclude_sessions'],['excluded'])


if __name__=='__main__':unittest.main()
