"""P2.1-03: OpenCode adapter — standard parsing, isolation, free-model routing, real CLI."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.integrations import prepare_integrations
from core.openrouter_free import FreeModelSelector
from core.providers import EphemeralAgentRunner, parse_opencode_stream
from fakes.api_server import ScriptedAPIServer

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / 'tests/fixtures/opencode/1.19.5'
CATALOG = json.loads((ROOT / 'tests/fixtures/openrouter/models_20261111.json').read_text(encoding='utf-8'))
TOP_FREE = 'nvidia/nemotron-3-ultra-550b-a55b:free'


def lines(name):
    return (FIX / name).read_text(encoding='utf-8').splitlines()


class ParseTests(unittest.TestCase):
    def test_completed_tool_run(self):
        parsed = parse_opencode_stream(lines('read_tool_ok.jsonl'))
        self.assertTrue(parsed['completed'])
        self.assertEqual(parsed['text'], 'DONE fixture read')
        self.assertEqual(parsed['tools'], [{'tool': 'read', 'status': 'completed'}])
        self.assertEqual(parsed['steps'], 2)
        self.assertEqual(parsed['usage']['input_tokens'], 20)
        self.assertEqual(parsed['cost_usd'], 0.0)
        self.assertTrue(parsed['session_id'].startswith('ses_'))

    def test_rate_limit_is_classified_with_retry_after(self):
        parsed = parse_opencode_stream(lines('rate_limit_429.jsonl'))
        self.assertFalse(parsed['completed'])
        self.assertEqual(parsed['status_code'], 429)
        self.assertEqual(parsed['retry_after'], 3.0)
        self.assertEqual(parsed['error_class'], 'QUOTA_EXHAUSTED')  # "free-models-per-day"
        self.assertNotIn('responseBody', parsed['error'])

    def test_text_without_final_step_is_not_completed(self):
        parsed = parse_opencode_stream([json.dumps({'type': 'text', 'sessionID': 's', 'part': {'text': 'partial'}})])
        self.assertFalse(parsed['completed'])
        self.assertIsNone(parsed['usage'])  # never reported -> None, not 0
        tool_calls_only = [json.dumps({'type': 'step_finish', 'part': {'reason': 'tool-calls'}})]
        self.assertFalse(parse_opencode_stream(tool_calls_only)['completed'])

    def test_error_classes(self):
        def cls(status, message):
            event = {'type': 'error', 'error': {'name': 'APIError', 'data': {'message': message, 'statusCode': status}}}
            return parse_opencode_stream([json.dumps(event)])['error_class']
        self.assertEqual(cls(429, 'slow down'), 'RATE_LIMIT')
        self.assertEqual(cls(402, 'Insufficient credits'), 'QUOTA_EXHAUSTED')
        self.assertEqual(cls(401, 'bad key'), 'AUTH_REQUIRED')
        self.assertEqual(cls(404, 'No endpoints found'), 'MODEL_UNAVAILABLE')
        self.assertEqual(cls(503, 'upstream'), 'TRANSPORT')


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        prepare_integrations(ROOT, self.root, self.root)
        self.state = self.root / 'or-state'
        self.selector = FreeModelSelector(self.state, fetch=lambda: CATALOG)

    def tearDown(self):
        self.tmp.cleanup()

    def runner(self, profile=None, model=''):
        provider = {'type': 'opencode_cli', 'models': {'worker': model},
                    'api_profile': profile or {'vendor': 'openrouter', 'free_only': True, 'auto_free': True}}
        config = {'providers': {'oc': provider}, 'roles': {'operator': {'provider': 'oc', 'tier': 'worker'}},
                  'openrouter_state_dir': str(self.state)}
        runner = EphemeralAgentRunner(self.root, config)
        runner.instructions_root = ROOT
        runner._free_selector_cache = self.selector
        return runner

    def run_with(self, runner, stdout, exit_code=0, env=None):
        with patch('core.providers.find_executable', return_value='opencode.exe'), \
                patch.dict('os.environ', env if env is not None else {'OPENROUTER_API_KEY': 'sk-or-test-value'}), \
                patch('core.providers.run_process', return_value={'exit_code': exit_code, 'stdout': stdout, 'stderr': ''}) as process:
            result = runner.run_role('operator', 'do it')
        return result, process

    def test_auto_free_command_and_isolated_env(self):
        result, process = self.run_with(self.runner(), '\n'.join(lines('read_tool_ok.jsonl')))
        self.assertTrue(result.success, result.error)
        cmd = process.call_args.args[0]
        self.assertEqual(cmd[cmd.index('--model') + 1], 'openrouter/' + TOP_FREE)
        self.assertEqual(Path(cmd[cmd.index('--dir') + 1]), self.root)
        env = process.call_args.kwargs['env']
        self.assertTrue(Path(env['XDG_CONFIG_HOME']).is_relative_to(self.root))
        self.assertIn('{env:OPENROUTER_API_KEY}', env['OPENCODE_CONFIG_CONTENT'])
        self.assertNotIn('sk-or-test-value', env['OPENCODE_CONFIG_CONTENT'])
        self.assertNotIn('sk-or-test-value', json.dumps(result.structured_data))
        data = result.structured_data
        self.assertEqual(data['quota_account'], 'api:openrouter:' + TOP_FREE)
        self.assertEqual(data['usage']['input_tokens'], 20)
        self.assertEqual(data['api_vendor'], 'openrouter')

    def test_missing_key_fails_before_launch(self):
        result, process = self.run_with(self.runner(), '', env={'OPENROUTER_API_KEY': ''})
        self.assertFalse(result.success)
        self.assertIn('Missing OPENROUTER_API_KEY', result.error)
        process.assert_not_called()

    def test_rate_limit_cools_model_and_next_run_uses_next_free_model(self):
        runner = self.runner()
        result, _ = self.run_with(runner, '\n'.join(lines('rate_limit_429.jsonl')), exit_code=1)
        self.assertFalse(result.success)
        quota = result.structured_data['quota']
        self.assertTrue(quota['exhausted'])
        self.assertEqual(quota['account'], 'api:openrouter:' + TOP_FREE)
        self.assertIn(TOP_FREE, self.selector.cooldowns())
        _, process = self.run_with(runner, '\n'.join(lines('read_tool_ok.jsonl')))
        cmd = process.call_args.args[0]
        self.assertNotEqual(cmd[cmd.index('--model') + 1], 'openrouter/' + TOP_FREE)

    def test_all_free_models_cooling_reports_account_wait(self):
        for model in self.selector.ranked('worker'):
            self.selector.cool_down(model['id'], 4_000_000_000)
        result, process = self.run_with(self.runner(), '')
        process.assert_not_called()
        self.assertFalse(result.success)
        self.assertEqual(result.structured_data['quota']['account'], 'api:openrouter')
        self.assertEqual(result.structured_data['quota']['resets_at'], 4_000_000_000)

    def test_free_only_refuses_priced_model(self):
        result, process = self.run_with(self.runner(model='google/gemini-4-carbon'), '')
        self.assertFalse(result.success)
        self.assertIn('free_only', result.error)
        process.assert_not_called()

    def test_text_without_stop_step_is_failure(self):
        stream = json.dumps({'type': 'text', 'sessionID': 's', 'part': {'text': 'half'}})
        result, _ = self.run_with(self.runner(), stream)
        self.assertFalse(result.success)
        self.assertIn('without a final stop step', result.error)


@unittest.skipUnless(shutil.which('opencode'), 'OpenCode CLI not installed')
class RealCliTests(unittest.TestCase):
    """Runs the installed OpenCode binary against the scripted local server (no account)."""

    def test_operator_writes_inside_workspace_and_completes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'ws'
            root.mkdir()
            prepare_integrations(ROOT, root, root)
            script = [{'need_tools': True, 'tool': 'write', 'arguments': {'filePath': 'result.txt', 'content': 'ok'}},
                      {'need_tools': True, 'text': 'Wrote result.txt'}]
            with ScriptedAPIServer(script, Path(tmp) / 'requests.jsonl') as server:
                provider = {'type': 'opencode_cli', 'models': {'worker': 'fake-model'},
                            'api_profile': {'vendor': 'local', 'base_url': server.base_url}}
                config = {'providers': {'oc': provider},
                          'roles': {'operator': {'provider': 'oc', 'tier': 'worker', 'timeout_seconds': 240}}}
                runner = EphemeralAgentRunner(root, config)
                runner.instructions_root = ROOT
                result = runner.run_role('operator', 'Write result.txt')
            self.assertTrue(result.success, result.error)
            self.assertEqual((root / 'result.txt').read_text(encoding='utf-8'), 'ok')
            self.assertEqual(result.structured_data['native_tools'], [{'tool': 'write', 'status': 'completed'}])
            self.assertEqual(result.structured_data['quota_account'], 'api:local')
            sent = [json.loads(l) for l in (Path(tmp) / 'requests.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertTrue(all(not r['had_authorization'] for r in sent))
            self.assertNotIn('task', sent[-1]['tools'])  # V1 permission denies subagents


if __name__ == '__main__':
    unittest.main()
