"""P2.1-04: OpenCode native session resume bound to task/role/model; handoff on change."""
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
CATALOG = json.loads((ROOT / 'tests/fixtures/openrouter/models_20261111.json').read_text(encoding='utf-8'))


def stream(session, text='done', input_tokens=100):
    return '\n'.join(json.dumps(e) for e in [
        {'type': 'text', 'sessionID': session, 'part': {'text': text}},
        {'type': 'step_finish', 'sessionID': session, 'part': {'reason': 'stop', 'cost': 0,
         'tokens': {'input': input_tokens, 'output': 5, 'reasoning': 0, 'cache': {'read': 10, 'write': 0}}}}])


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        prepare_integrations(ROOT, self.root, self.root)
        self.selector = FreeModelSelector(self.root / 'or', fetch=lambda: CATALOG)

    def tearDown(self):
        self.tmp.cleanup()

    def runner(self, roles=('operator',)):
        provider = {'type': 'opencode_cli', 'models': {'worker': ''},
                    'api_profile': {'vendor': 'openrouter', 'free_only': True, 'auto_free': True}}
        runner = EphemeralAgentRunner(self.root, {'providers': {'oc': provider},
                                                  'roles': {r: {'provider': 'oc', 'tier': 'worker'} for r in roles},
                                                  'openrouter_state_dir': str(self.root / 'or')})
        runner.instructions_root = ROOT
        runner._free_selector_cache = self.selector
        return runner

    def call(self, runner, role, out, code=0):
        with patch('core.providers.find_executable', return_value='opencode.exe'), \
                patch.dict('os.environ', {'OPENROUTER_API_KEY': 'k'}), \
                patch('core.providers.run_process', return_value={'exit_code': code, 'stdout': out, 'stderr': ''}) as process:
            result = runner.run_role(role, 'step')
        return result, process.call_args.args[0], process.call_args.kwargs['env']

    def state(self, role):
        return json.loads((self.root / '.workflow/roles' / role / 'state.json').read_text(encoding='utf-8'))

    def test_second_run_resumes_exact_session(self):
        runner = self.runner()
        _, first, _ = self.call(runner, 'operator', stream('ses_A'))
        self.assertNotIn('--session', first)
        _, second, _ = self.call(runner, 'operator', stream('ses_A'))
        self.assertEqual(second[second.index('--session') + 1], 'ses_A')
        self.assertNotIn('--continue', second)
        self.assertNotIn('-c', second)

    def test_model_change_starts_new_session(self):
        runner = self.runner()
        _, first, _ = self.call(runner, 'operator', stream('ses_A'))
        used = first[first.index('--model') + 1].split('/', 1)[1]
        self.selector.cool_down(used, 4_000_000_000)
        _, second, _ = self.call(runner, 'operator', stream('ses_B'))
        self.assertNotIn('--session', second)
        self.assertNotEqual(second[second.index('--model') + 1], first[first.index('--model') + 1])
        self.assertEqual(self.state('operator')['session_id'], 'ses_B')

    def test_failed_run_drops_session(self):
        runner = self.runner()
        self.call(runner, 'operator', stream('ses_A'))
        self.call(runner, 'operator', json.dumps({'type': 'text', 'sessionID': 'ses_A', 'part': {'text': 'half'}}))
        self.assertEqual(self.state('operator')['session_id'], '')
        _, third, _ = self.call(runner, 'operator', stream('ses_C'))
        self.assertNotIn('--session', third)

    def test_roles_have_separate_homes_and_sessions(self):
        runner = self.runner(('operator', 'validator'))
        _, _, env_op = self.call(runner, 'operator', stream('ses_OP'))
        _, cmd_val, env_val = self.call(runner, 'validator', stream('ses_VAL'))
        self.assertNotIn('--session', cmd_val)
        self.assertNotEqual(env_op['XDG_DATA_HOME'], env_val['XDG_DATA_HOME'])
        self.assertEqual((self.state('operator')['session_id'], self.state('validator')['session_id']), ('ses_OP', 'ses_VAL'))

    def test_context_size_uses_last_step_not_sum(self):
        two_steps = '\n'.join(json.dumps(e) for e in [
            {'type': 'step_finish', 'sessionID': 's', 'part': {'reason': 'tool-calls', 'tokens': {'input': 1000, 'output': 1, 'cache': {}}}},
            {'type': 'text', 'sessionID': 's', 'part': {'text': 'ok'}},
            {'type': 'step_finish', 'sessionID': 's', 'part': {'reason': 'stop', 'tokens': {'input': 1500, 'output': 1, 'cache': {'read': 500}}}}])
        parsed = parse_opencode_stream(two_steps.splitlines())
        self.assertEqual(parsed['usage']['input_tokens'], 2500)
        self.assertEqual(parsed['usage']['context_tokens'], 2000)
        runner = self.runner()
        self.call(runner, 'operator', two_steps)
        self.assertEqual(self.state('operator')['context_tokens'], 2000)


@unittest.skipUnless(shutil.which('opencode'), 'OpenCode CLI not installed')
class RealResumeTests(unittest.TestCase):
    def test_real_cli_resume_sends_prior_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'ws'
            root.mkdir()
            prepare_integrations(ROOT, root, root)
            log = Path(tmp) / 'requests.jsonl'
            with ScriptedAPIServer([{'need_tools': True, 'text': 'first answer'},
                                    {'need_tools': True, 'text': 'second answer'}], log) as server:
                provider = {'type': 'opencode_cli', 'models': {'worker': 'fake-model'},
                            'api_profile': {'vendor': 'local', 'base_url': server.base_url}}
                runner = EphemeralAgentRunner(root, {'providers': {'oc': provider},
                                                     'roles': {'validator': {'provider': 'oc', 'tier': 'worker', 'timeout_seconds': 240}}})
                runner.instructions_root = ROOT
                first = runner.run_role('validator', 'first step')
                second = runner.run_role('validator', 'second step')
            self.assertTrue(first.success, first.error)
            self.assertTrue(second.success, second.error)
            self.assertEqual(first.structured_data['session_id'], second.structured_data['session_id'])
            main_calls = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
            main_calls = [c for c in main_calls if c['tools']]
            history = json.dumps(main_calls[-1]['messages'])
            self.assertIn('first answer', history)  # resumed session carried the prior turn


if __name__ == '__main__':
    unittest.main()
