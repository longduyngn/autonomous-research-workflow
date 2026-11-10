"""P2.1-01: pinned OpenCode compatibility contract and recorded event fixtures."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / 'config' / 'provider_versions.json').read_text(encoding='utf-8'))['providers']['opencode_cli']
FIXTURES = ROOT / CONTRACT['fixtures']


def events(name):
    return [json.loads(line) for line in (FIXTURES / name).read_text(encoding='utf-8').splitlines() if line.strip()]


class OpenCodeContractTests(unittest.TestCase):
    def test_contract_pins_exact_v1_version(self):
        self.assertRegex(CONTRACT['version'], r'^\d+\.\d+\.\d+$')
        self.assertEqual(CONTRACT['permission_schema'], 'v1')
        self.assertFalse(CONTRACT['v2_permissions_supported'])
        self.assertIn('--dir', CONTRACT['run_flags'])
        self.assertIn('stdin', CONTRACT['required_invocation'])

    def test_adapter_permission_keys_are_v1_keys(self):
        # The adapter must only emit keys the pinned schema understands.
        from unittest.mock import patch
        import tempfile
        from core.integrations import prepare_integrations
        from core.providers import EphemeralAgentRunner
        for role in ('validator', 'operator', 'planner'):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                prepare_integrations(ROOT, root, root)
                config = {'providers': {'oc': {'type': 'opencode_cli', 'models': {'worker': 'fake/fake-model',
                          'research': 'fake/fake-model'}}},
                          'roles': {role: {'provider': 'oc', 'tier': 'worker'}}}
                runner = EphemeralAgentRunner(root, config)
                runner.instructions_root = ROOT
                output = json.dumps({'type': 'text', 'sessionID': 's1', 'part': {'text': 'VERDICT: PASS'}})
                with patch('core.providers.find_executable', return_value='opencode.exe'), \
                        patch('core.providers.run_process', return_value={'exit_code': 0, 'stdout': output, 'stderr': ''}) as process:
                    runner.run_role(role, 'check')
                permissions = json.loads(process.call_args.kwargs['env']['OPENCODE_PERMISSION'])
                self.assertLessEqual(set(permissions), set(CONTRACT['permission_keys']))

    def test_successful_tool_run_shape(self):
        stream = events('read_tool_ok.jsonl')
        kinds = [e['type'] for e in stream]
        self.assertIn('tool_use', kinds)
        self.assertEqual(kinds[-1], 'step_finish')
        tool = next(e for e in stream if e['type'] == 'tool_use')['part']
        self.assertEqual((tool['tool'], tool['state']['status']), ('read', 'completed'))
        self.assertIn('<WORKSPACE>', tool['state']['output'])  # resolved inside --dir
        finish = stream[-1]['part']
        self.assertEqual(finish['reason'], 'stop')
        self.assertEqual(set(finish['tokens']), {'total', 'input', 'output', 'reasoning', 'cache'})
        self.assertIn('cost', finish)
        self.assertEqual(len({e['sessionID'] for e in stream}), 1)

    def test_denied_tool_is_reported_as_tool_error(self):
        tool = next(e for e in events('write_denied_v1_env.jsonl') if e['type'] == 'tool_use')['part']
        self.assertEqual(tool['state']['status'], 'error')
        self.assertIn('Available tools: read', tool['state']['error'])

    def test_rate_limit_error_event_carries_status_and_retry_after(self):
        stream = events('rate_limit_429.jsonl')
        error = next(e for e in stream if e['type'] == 'error')['error']
        self.assertEqual(error['data']['statusCode'], 429)
        self.assertEqual(error['data']['responseHeaders']['retry-after'], '3')
        self.assertTrue(error['data']['isRetryable'])

    def test_v2_permissions_rejection_recorded(self):
        text = (FIXTURES / 'v2_permissions_rejected.txt').read_text(encoding='utf-8')
        self.assertIn('V2 permissions are not supported by OpenCode V1', text)

    @unittest.skipUnless(shutil.which('opencode'), 'OpenCode CLI not installed')
    def test_installed_cli_matches_pin(self):
        done = subprocess.run([shutil.which('opencode'), '--version'], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(done.stdout.strip(), CONTRACT['version'])


if __name__ == '__main__':
    unittest.main()
