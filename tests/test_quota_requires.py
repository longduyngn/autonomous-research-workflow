import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.database import WorkspaceDatabase
from core.plans import parse_plan
from core.quota import QuotaRouter, QuotaStore, Snapshot
from core.remote_claude import ClaudeCloud
from tests.test_remote_claude import worker


class RequiredQuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = WorkspaceDatabase(self.root / 'events.db')
        self.store = QuotaStore(self.db)
        self.now = time.time()
        self.config = {'database_path': str(self.root / 'events.db'),
            'providers': {'local': {'type': 'claude_cli'}, 'codex': {'type': 'codex_cli'},
                          'remote': {'type': 'claude_cli', 'quota_account': 'remote_claude'}},
            'roles': {'operator': {'provider': 'local'}},
            'quota_routing': {'enabled': True, 'accounts': {'claude': {}, 'codex': {}, 'remote_claude': {}},
                'classes': {'worker': [{'provider': 'local'}, {'provider': 'codex'}]},
                'role_classes': {'operator': 'worker'}, 'requires': {'operator': ['codex']},
                'workarounds': {}}}

    def snapshot(self, account, used):
        self.store.put([Snapshot(account, 'five_hour', used, self.now + 1000, 'fixture', self.now)])

    def test_plan_requires_roundtrip_validation_and_task_precedence(self):
        plan = parse_plan('```research_plan\n' + json.dumps({
            'mode': 'direct', 'requires': {'operator': ['claude']}}) + '\n```')
        self.snapshot('claude', 95)
        task = SimpleNamespace(plan_json=json.dumps(plan), provider_override='')
        router = QuotaRouter(self.config, self.store)
        decision = router.select('operator', task)
        self.assertEqual((decision.account, decision.reserved_use, decision.source), ('claude', True, 'requires'))
        self.assertEqual(router.select('operator').account, 'codex')
        self.snapshot('claude', 100)
        self.assertEqual(router.select('operator', task).action, 'wait')
        task.provider_override = 'codex'
        self.assertEqual(router.select('operator', task).source, 'pinned')
        for bad in ('claude', {'operator': 'claude'}, {'operator': []}, {'oops': ['claude']}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_plan('```research_plan\n' + json.dumps({'mode': 'direct', 'requires': bad}) + '\n```')

    def test_remote_required_account_can_spend_its_own_reserve(self):
        self.config['quota_routing']['workarounds'] = {'claude': [{'provider': 'remote'}]}
        self.snapshot('remote_claude', 95)
        router = QuotaRouter(self.config, self.store)
        decision = router.select('operator', requires=['remote_claude'])
        self.assertEqual((decision.account, decision.reserved_use), ('remote_claude', True))
        self.snapshot('remote_claude', 100)
        decision = router.select('operator', requires=['remote_claude'])
        self.assertEqual((decision.action, decision.account), ('wait', 'remote_claude'))

    def cloud(self):
        cloud = ClaudeCloud.__new__(ClaudeCloud)
        cloud.controller, cloud.config, cloud.transport = self.root, self.config, Mock()
        return cloud

    def test_remote_launch_waits_before_rpc_when_exhausted_and_logs_reserve(self):
        self.snapshot('remote_claude', 100)
        cloud = self.cloud()
        result = cloud.start('example', 'pair')
        self.assertEqual(result['state'], 'WAITING_PROVIDER')
        cloud.transport.rpc.assert_not_called()
        self.snapshot('remote_claude', 95)
        cloud.transport.rpc.return_value = {'state': 'QUEUED'}
        self.assertEqual(cloud.start('example', 'pair')['state'], 'QUEUED')
        self.assertIn("'operator': ['remote_claude']", cloud.transport.rpc.call_args.args[0])
        routes = [e for e in self.db.get_recent_events(limit=20) if e['event_type'] == 'QUOTA_ROUTE']
        self.assertTrue(any(json.loads(e['payload_json'])['reserved_use'] for e in routes))

    def test_worker_status_import_preserves_timestamp_and_separates_host_quota(self):
        row = {}
        event = {'type': 'rate_limit_event', 'rate_limit_info': {'unifiedWindows': {
            'five_hour': {'utilization': .95, 'resetsAt': self.now + 1000},
            'seven_day': {'utilization': .4, 'resetsAt': self.now + 5000}}}}
        self.assertTrue(worker.capture_quota_event(row, event, self.now - 20))
        self.assertEqual([s['account'] for s in row['quota_snapshots']], ['remote_claude', 'remote_claude'])
        cloud = self.cloud()
        cloud.transport.rpc.return_value = {'state': 'RUNNING', **row}
        cloud.status('example')
        cloud.status('example')
        self.assertEqual(self.store.snapshots('claude'), [])
        self.assertEqual(len(self.store.snapshots('remote_claude')), 2)
        self.assertEqual(self.store.snapshots('remote_claude')[0].observed_at, self.now - 20)
        event['rate_limit_info']['unifiedWindows']['five_hour']['status'] = 'rejected'
        worker.capture_quota_event(row, event, self.now)
        self.assertEqual(row['quota_snapshots'][0]['used_percent'], 100)

    def test_worker_smoke_keeps_final_json_and_quota_stream_without_model_calls(self):
        event = {'type': 'rate_limit_event', 'rate_limit_info': {'unifiedWindows': {
            'five_hour': {'utilization': .2, 'resetsAt': self.now + 1000}}}}
        native = {'type': 'result', 'result': 'CLAUDE-COLAB-0123456789ab', 'session_id': 'remote-s', 'usage': {}}
        proc = Mock(returncode=0)
        proc.communicate.return_value = ('\n'.join(map(json.dumps, [event, native])).encode(), b'')
        auth = SimpleNamespace(stdout=json.dumps({'authMethod': 'oauth_token', 'loggedIn': True}))
        row = {'started_at': self.now}
        with patch.object(worker, 'find_claude', return_value='claude'), \
             patch.object(worker.subprocess, 'run', return_value=auth), \
             patch.object(worker.subprocess, 'Popen', return_value=proc) as spawn, \
             patch.object(worker.uuid, 'uuid4', return_value=SimpleNamespace(hex='0123456789abcdef')):
            worker.run_smoke(self.root, row, 'synthetic-test-token')
        self.assertEqual(row['state'], 'COMPLETED')
        self.assertEqual(row['quota_snapshots'][0]['account'], 'remote_claude')
        self.assertEqual(json.loads((self.root / 'native.json').read_text()), native)
        self.assertEqual(len((self.root / 'native.jsonl').read_text().splitlines()), 2)
        self.assertIn('stream-json', spawn.call_args.args[0])
