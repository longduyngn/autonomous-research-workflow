"""P2.4-01: every adapter returns the same standard result view (schema v1)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.models import AgentRunResult
from core.providers import EphemeralAgentRunner
from core.result_contract import STATUSES, classify_error, normalize_usage, standard_result

KEYS = {'schema_version', 'run_id', 'role', 'provider', 'model', 'quota_account', 'status', 'output_path',
        'handoff_path', 'quota_hit', 'reset_at', 'retry_after', 'usage', 'error_class', 'session_id', 'integration_mode'}

STREAMS = {
    'codex_cli': '\n'.join(json.dumps(e) for e in [
        {'type': 'thread.started', 'thread_id': 'th_1'},
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'done'}},
        {'type': 'turn.completed', 'usage': {'input_tokens': 100, 'cached_input_tokens': 40, 'output_tokens': 9}}]),
    'claude_cli': json.dumps({'type': 'result', 'subtype': 'success', 'result': 'done', 'session_id': 'cl_1',
                              'num_turns': 1, 'total_cost_usd': 0.02,
                              'usage': {'input_tokens': 50, 'output_tokens': 5, 'cache_read_input_tokens': 7,
                                        'cache_creation_input_tokens': 3}}),
    'agy_cli': json.dumps({'result': 'done', 'conversation_id': '11111111-2222-3333-4444-555555555555'}),
    'opencode_cli': '\n'.join(json.dumps(e) for e in [
        {'type': 'text', 'sessionID': 'ses_1', 'part': {'text': 'done'}},
        {'type': 'step_finish', 'sessionID': 'ses_1', 'part': {'reason': 'stop', 'cost': 0,
         'tokens': {'input': 30, 'output': 4, 'reasoning': 0, 'cache': {'read': 2, 'write': 0}}}}]),
}


class ContractTests(unittest.TestCase):
    def run_cli(self, kind, stdout, exit_code=0, stderr=''):
        with tempfile.TemporaryDirectory() as tmp:
            provider = {'type': kind, 'models': {'fast': 'm1'}}
            config = {'providers': {'p': provider}, 'roles': {'communicator': {'provider': 'p', 'tier': 'fast'}}}
            runner = EphemeralAgentRunner(tmp, config)
            log = Path(tmp) / 'logs' / 'communicator.log'
            with patch('core.providers.find_executable', return_value='cli.exe'), \
                    patch('core.providers.run_process', return_value={'exit_code': exit_code, 'stdout': stdout, 'stderr': stderr}):
                result = runner.run_role('communicator', 'hello', log)
            receipt = json.loads(log.with_suffix('.result.json').read_text(encoding='utf-8'))
        return result, receipt

    def test_every_cli_adapter_reports_the_standard_view(self):
        expected = {'codex_cli': (100, 9, 40, None), 'claude_cli': (50, 5, 7, 3),
                    'agy_cli': (None, None, None, None), 'opencode_cli': (30, 4, 2, 0)}
        for kind, stream in STREAMS.items():
            with self.subTest(kind=kind):
                result, receipt = self.run_cli(kind, stream)
                standard = result.structured_data['standard']
                self.assertEqual(set(standard), KEYS)
                self.assertTrue(result.success, result.error)
                self.assertEqual(standard['status'], 'COMPLETED')
                self.assertIsNone(standard['error_class'])
                usage = standard['usage']
                self.assertEqual((usage['input_tokens'], usage['output_tokens'], usage['cache_read_tokens'],
                                  usage['cache_write_tokens']), expected[kind])
                self.assertEqual(receipt['standard']['status'], 'COMPLETED')  # persisted with the receipt
                self.assertTrue(standard['run_id'])

    def test_failures_map_to_status_and_error_class(self):
        limit = "You've hit your usage limit. Try again in 2h"
        result, _ = self.run_cli('codex_cli', '', exit_code=1, stderr=limit)
        standard = result.structured_data['standard']
        self.assertEqual((standard['status'], standard['error_class'], standard['quota_hit']),
                         ('WAITING_PROVIDER', 'QUOTA_EXHAUSTED', True))
        self.assertIsNotNone(standard['reset_at'])
        result, _ = self.run_cli('claude_cli', '', exit_code=1, stderr='Authentication required for this account')
        self.assertEqual(result.structured_data['standard']['status'], 'WAITING_INPUT')
        self.assertEqual(result.structured_data['standard']['error_class'], 'AUTH_REQUIRED')

    def test_mock_http_and_extension_results(self):
        mock = AgentRunResult('operator', 'mock', True, 'ok')
        self.assertEqual(standard_result(mock)['usage']['source'], 'unavailable')
        bounded = AgentRunResult('operator', 'api', False, '', error='Turn budget reached',
                                 structured_data={'budget_exhausted': True, 'handoff': 'h.json'})
        standard = standard_result(bounded)
        self.assertEqual((standard['status'], standard['handoff_path']), ('INCOMPLETE', 'h.json'))
        pinned = AgentRunResult('communicator', 'agy', False, '', error='Provider quota exhausted',
                                structured_data={'pinned_exhausted': True, 'pinned_quota': {'exhausted': True, 'resets_at': 5}})
        standard = standard_result(pinned)
        self.assertEqual((standard['status'], standard['quota_hit'], standard['reset_at']), ('FAILED', True, 5))
        for value in (standard_result(mock)['status'], standard['status']):
            self.assertIn(value, STATUSES)

    def test_missing_usage_is_none_never_zero(self):
        usage = normalize_usage({'input_tokens': 0})
        self.assertEqual((usage['input_tokens'], usage['output_tokens']), (0, None))
        self.assertEqual(normalize_usage(None)['input_tokens'], None)

    def test_success_flag_and_status_agree(self):
        for success in (True, False):
            result = AgentRunResult('operator', 'p', success, 'x' if success else '', error=None if success else 'boom')
            self.assertEqual(standard_result(result)['status'] == 'COMPLETED', success)
            self.assertEqual(classify_error(result) is None, success)


if __name__ == '__main__':
    unittest.main()
