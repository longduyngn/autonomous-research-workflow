import importlib.util
import base64
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from core.remote_claude import seal_secret,ClaudeCloud

ROOT=Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('claude_worker_test',ROOT/'scripts/remote_claude_worker.py')
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)


class ClaudeCloudTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.patch=patch.object(worker,'ROOT',self.root);self.patch.start();self.addCleanup(self.patch.stop)

    def test_secret_capsule_has_no_plaintext_and_is_one_use(self):
        secret='synthetic-subscription-token-no-real-secret'
        challenge=worker.prepare_secret();sealed=seal_secret(secret,challenge)
        self.assertNotIn(secret,json.dumps(sealed))
        result=worker.receive_secret(sealed)
        self.assertEqual(result['state'],'CREDENTIAL_READY')
        self.assertEqual(worker._oauth_token,secret)
        self.assertEqual(list(self.root.iterdir()),[])
        with self.assertRaises(ValueError):worker.receive_secret(sealed)

    def test_expired_capsule_cannot_install_a_credential(self):
        challenge=worker.prepare_secret();sealed=seal_secret('synthetic-secret-12345',challenge)
        worker._transfer_at-=121
        with self.assertRaises(ValueError):worker.receive_secret(sealed)

    def test_tampered_ciphertext_is_rejected(self):
        challenge=worker.prepare_secret();sealed=seal_secret('synthetic-secret-12345',challenge)
        sealed['ciphertext']=sealed['ciphertext'][:-4]+'AAAA'
        with self.assertRaises(Exception):worker.receive_secret(sealed)

    def test_subscription_env_cannot_fall_back_to_an_api_key(self):
        with patch.dict(os.environ,{'ANTHROPIC_API_KEY':'synthetic-api-key','ANTHROPIC_AUTH_TOKEN':'other',
                'CLAUDE_CODE_USE_VERTEX':'1','OPENAI_API_KEY':'not-for-child'}):
            env=worker.native_env('synthetic-token',self.root/'config')
        self.assertEqual(env['CLAUDE_CODE_OAUTH_TOKEN'],'synthetic-token')
        for name in ('ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','CLAUDE_CODE_USE_VERTEX','OPENAI_API_KEY'):
            self.assertNotIn(name,env)

    def test_stdout_redaction_and_native_permission_denial(self):
        self.assertNotIn('synthetic-secret',worker.scrub('failure synthetic-secret','synthetic-secret'))
        self.assertEqual(worker.classify({'result':'I tried','permission_denials':[{}]},0)[0],'WAITING_APPROVAL')
        self.assertEqual(worker.classify({'result':'','is_error':False},0)[0],'FAILED')
        self.assertEqual(worker.classify({'result':'quota usage limit reached','is_error':True},1)[0],'WAITING_PROVIDER')
        self.assertEqual(worker.classify({'result':'OAuth token unauthorized','is_error':True},1)[0],'WAITING_AUTH')

    def test_job_id_cannot_escape_workspace(self):
        for job_id in ('../other','a/b','','a\n'):
            with self.assertRaises(ValueError):worker.job_dir(job_id)

    def test_snapshot_validation_rejects_changed_inputs(self):
        for role in ('operator','validator'):(self.root/role).mkdir()
        (self.root/'operator/metric.py').write_text('def sum_squares(n): return 0\n')
        (self.root/'operator/result.json').write_text('{"n":10000,"sum_squares":0}')
        hashes=worker.snapshot(self.root,'v1')
        worker.atomic(self.root/'validator/verdict.json',self.fail_receipt(hashes['metric.py']))
        worker.validate_saved(self.root,'v1',hashes,'FAIL')
        target=self.root/'validator/input/metric.py';target.chmod(0o600);target.write_text('changed')
        with self.assertRaises(worker.NativeFailure):worker.validate_saved(self.root,'v1',hashes,'FAIL')
        for p in (self.root/'validator/input').iterdir():p.chmod(0o600)

    def test_wrong_verdict_cannot_complete_expected_validation(self):
        for role in ('operator','validator'):(self.root/role).mkdir()
        (self.root/'operator/metric.py').write_text('x=1\n')
        (self.root/'operator/result.json').write_text('{}')
        hashes=worker.snapshot(self.root,'v1')
        worker.atomic(self.root/'validator/verdict.json',{'verdict':'PASS','source_sha256':hashes['metric.py']})
        with self.assertRaises(worker.NativeFailure):worker.validate_saved(self.root,'v1',hashes,'FAIL')
        for p in (self.root/'validator/input').iterdir():p.chmod(0o600)

    @staticmethod
    def fail_receipt(digest):
        return {'verdict':'FAIL','source_sha256':digest,'cases':[
            {'n':n,'actual':0,'expected':n*(n+1)*(2*n+1)//6,'pass':n==0} for n in [0,1,2,10,10000]],
            'published':{'n':10000,'sum_squares':0},'published_ok':False}

    def test_validator_cannot_drop_test_cases_or_invent_a_pass(self):
        for role in ('operator','validator'):(self.root/role).mkdir()
        (self.root/'operator/metric.py').write_text('def sum_squares(n): return 0\n')
        (self.root/'operator/result.json').write_text('{"n":10000,"sum_squares":0}')
        hashes=worker.snapshot(self.root,'v1')
        for wrong in ({**self.fail_receipt(hashes['metric.py']),'cases':[]},
                      {**self.fail_receipt(hashes['metric.py']),'published_ok':True}):
            worker.atomic(self.root/'validator/verdict.json',wrong)
            with self.assertRaises(worker.NativeFailure):worker.validate_saved(self.root,'v1',hashes,'FAIL')
        for p in (self.root/'validator/input').iterdir():p.chmod(0o600)

    def test_host_rejects_evidence_path_escape_and_corruption(self):
        cloud=ClaudeCloud.__new__(ClaudeCloud);cloud.controller=self.root;cloud.transport=Mock()
        data=b'{}'
        for name,digest in [('../outside',hashlib.sha256(data).hexdigest()),('state.json','bad-hash')]:
            cloud.transport.rpc.return_value={'boot_id':'vm','files':{name:{'data':base64.b64encode(data).decode(),'sha256':digest}}}
            with self.assertRaises(ValueError):cloud.collect('test-job')
        self.assertFalse((self.root/'storage/remote-claude/outside').exists())

    def test_remote_export_rejects_unfinished_work_and_credentials(self):
        d=worker.job_dir('test');d.mkdir(parents=True)
        worker.atomic(d/'state.json',{'state':'RUNNING','created_at':1})
        with patch.object(worker,'status',return_value={'state':'RUNNING'}):
            with self.assertRaises(RuntimeError):worker.collect('test')
        worker._oauth_token='synthetic-token-do-not-export'
        worker.atomic(d/'state.json',{'state':'COMPLETED','finished_at':1,'created_at':1})
        (d/'operator.stderr').write_text(worker._oauth_token)
        with patch.object(worker,'status',return_value={'state':'COMPLETED','finished_at':1}), \
             patch.object(worker,'boot_id',return_value='vm'):
            with self.assertRaises(ValueError):worker.collect('test')

    def test_retry_does_not_launch_a_second_supervisor(self):
        worker._oauth_token='synthetic-token'
        fake=Mock(pid=1234)
        with patch.object(worker,'boot_id',return_value='test-vm'), \
             patch.object(worker.subprocess,'Popen',return_value=fake) as spawn:
            worker.launch('one','smoke');worker.launch('one','smoke')
            spawn.assert_called_once()
        self.assertNotIn('synthetic-token',(self.root/'jobs/one/state.json').read_text())
        with self.assertRaises(ValueError):worker.launch('one','pair')


if __name__=='__main__':unittest.main()
