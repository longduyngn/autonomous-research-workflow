"""P2.1-06: the minimal http_llm loop through the real _post against the fake server."""
import json
import tempfile
import unittest
from pathlib import Path

from core.providers import EphemeralAgentRunner, ModelHTTPError
from fakes.api_server import ScriptedAPIServer


def runner_for(tmp, base_url, role='operator', rounds=6):
    config = {'providers': {'api': {'type': 'http_llm', 'base_url': base_url + '/chat/completions',
                                    'require_api_key': False, 'default_model': 'fake-model',
                                    'supports_tools': True, 'max_tool_rounds': rounds}},
              'roles': {role: {'provider': 'api', 'tier': 'worker'}}}
    return EphemeralAgentRunner(tmp, config)


class HttpLoopTests(unittest.TestCase):
    def test_429_before_tools_is_replay_safe_with_retry_after(self):
        with tempfile.TemporaryDirectory() as tmp, ScriptedAPIServer([
                {'status': 429, 'headers': {'Retry-After': '42'}, 'body': {'error': {'message': 'Rate limit exceeded'}}}]) as srv:
            result = runner_for(tmp, srv.base_url).run_role('operator', 'work')
        self.assertFalse(result.success)
        self.assertEqual((result.structured_data['retry_safe'], result.structured_data['retry_after']), (True, 42))
        self.assertEqual(result.structured_data['http_status'], 429)
        self.assertNotIn('quota', result.structured_data)  # plain rate limit, not exhausted quota

    def test_daily_free_limit_is_quota_exhausted(self):
        with tempfile.TemporaryDirectory() as tmp, ScriptedAPIServer([
                {'status': 429, 'headers': {'Retry-After': '600'},
                 'body': {'error': {'message': 'Rate limit exceeded: free-models-per-day'}}}]) as srv:
            result = runner_for(tmp, srv.base_url).run_role('operator', 'work')
        quota = result.structured_data['quota']
        self.assertTrue(quota['exhausted'])
        self.assertEqual(result.structured_data['error_class'], 'QUOTA_EXHAUSTED')

    def test_429_after_tool_call_checkpoints_and_never_replays(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'notes.txt').write_text('x', encoding='utf-8')
            script = [{'tool': 'list_files', 'arguments': {}},
                      {'status': 429, 'body': {'error': {'message': 'Rate limit exceeded'}}}]
            with ScriptedAPIServer(script) as srv:
                result = runner_for(tmp, srv.base_url).run_role('operator', 'work')
                calls = len(srv.requests)
            self.assertEqual(calls, 2)  # no automatic replay
            self.assertFalse(result.success)
            self.assertTrue(result.structured_data['reconcile_required'])
            handoff = json.loads(Path(result.structured_data['handoff']).read_text(encoding='utf-8'))
            self.assertEqual(handoff['tool_trace'][0]['name'], 'list_files')
            self.assertNotIn('retry_safe', result.structured_data)

    def test_other_http_errors_keep_only_allow_listed_fields(self):
        with tempfile.TemporaryDirectory() as tmp, ScriptedAPIServer([
                {'status': 401, 'body': {'error': {'message': 'invalid api key'}, 'echo': 'Authorization: Bearer sk-secret'}}]) as srv:
            result = runner_for(tmp, srv.base_url).run_role('operator', 'work')
        self.assertFalse(result.success)
        self.assertIn('HTTP 401', result.error)
        self.assertNotIn('sk-secret', result.error)

    def test_round_limit_returns_resumable_checkpoint_not_completion(self):
        script = [{'tool': 'list_files', 'arguments': {}}] * 3
        with tempfile.TemporaryDirectory() as tmp, ScriptedAPIServer(script) as srv:
            result = runner_for(tmp, srv.base_url, rounds=3).run_role('operator', 'work')
            self.assertFalse(result.success)
            self.assertTrue(result.structured_data['budget_exhausted'])
            self.assertTrue(Path(result.structured_data['handoff']).exists())

    def test_error_parsing_from_http_error(self):
        import io
        import urllib.error
        body = io.BytesIO(json.dumps({'error': {'message': 'Insufficient credits'}}).encode())
        exc = urllib.error.HTTPError('http://x', 402, 'Payment Required', {'Retry-After': 'oops'}, body)
        error = ModelHTTPError.from_http_error(exc)
        self.assertEqual((error.status, error.retry_after, error.error_class), (402, None, 'QUOTA_EXHAUSTED'))


if __name__ == '__main__':
    unittest.main()
