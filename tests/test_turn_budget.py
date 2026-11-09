"""Soft turn budgets and the real Codex usage-limit message."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from core.configuration import role_turn_budget, validate_config
from core.providers import EphemeralAgentRunner
from core.quota import LimitErrorDetector

CODEX_LIMIT = ("■ You’ve hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit\n"
               "https://chatgpt.com/settings/usage to purchase more credits or try again at Nov 10th, 2026\n"
               "3:24 AM.")


class TurnBudgetTests(unittest.TestCase):
    def test_new_key_overrides_legacy_and_validates(self):
        config = {'role_max_turns': {'operator': 10, 'validator': 9}, 'role_turn_budget': {'operator': 50}}
        self.assertEqual(role_turn_budget(config, 'operator'), 50)
        self.assertEqual(role_turn_budget(config, 'validator'), 9)
        self.assertEqual(role_turn_budget({}, 'operator'), 35)
        with self.assertRaises(ValueError):
            validate_config({'role_turn_budget': {'operator': 0}})

    def run_claude(self, stdout, role_cfg=None):
        seen = {}

        def process(argv, cwd, **kwargs):
            seen['argv'] = argv
            return {'exit_code': 0, 'stdout': stdout, 'stderr': ''}
        with tempfile.TemporaryDirectory() as tmp:
            config = {'role_turn_budget': {'communicator': 2}, 'roles': {'communicator': {'tier': 'fast'}}}
            runner = EphemeralAgentRunner(Path(tmp), config)
            with patch('core.providers.find_executable', return_value='cli.exe'), \
                 patch('core.providers.run_process', side_effect=process):
                result = runner._run_cli('communicator', 'p', {'type': 'claude_cli'}, config['roles']['communicator'],
                                         'probe', 10, Path(tmp) / 'probe.log')
        return seen['argv'], result

    def test_claude_has_no_hard_turn_flag_and_flags_over_budget(self):
        final = json.dumps({'type': 'result', 'subtype': 'success', 'result': 'done', 'num_turns': 7})
        argv, result = self.run_claude(final)
        self.assertNotIn('--max-turns', argv)
        self.assertTrue(result.success)
        self.assertTrue(result.structured_data['turn_budget_exceeded'])
        self.assertIn('soft', result.structured_data['turn_enforcement'])

    def test_claude_within_budget_is_not_flagged(self):
        final = json.dumps({'type': 'result', 'subtype': 'success', 'result': 'done', 'num_turns': 2})
        _, result = self.run_claude(final)
        self.assertNotIn('turn_budget_exceeded', result.structured_data)

    def test_http_loop_continues_past_budget_after_one_wrap_up_nudge(self):
        config = {'providers': {'api': {'type': 'http_llm', 'base_url': 'http://local', 'require_api_key': False,
                                        'default_model': 'm', 'supports_tools': True, 'max_tool_rounds': 6}},
                  'roles': {'operator': {'provider': 'api', 'tier': 'worker'}},
                  'role_turn_budget': {'operator': 2}}
        call = lambda i: {'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': str(i), 'type': 'function', 'function': {'name': 'list_files', 'arguments': '{}'}}]}}]}
        replies = [call(i) for i in range(3)] + [{'choices': [{'finish_reason': 'stop',
                                                              'message': {'role': 'assistant', 'content': 'done'}}]}]
        with tempfile.TemporaryDirectory() as tmp:
            runner = EphemeralAgentRunner(tmp, config)
            with patch.object(runner, '_post', side_effect=replies) as post:
                result = runner.run_role('operator', 'work')
        self.assertTrue(result.success, result.error)
        self.assertTrue(result.structured_data['turn_budget_exceeded'])
        nudges = [m for m in post.call_args_list[-1].args[1]['messages']
                  if m.get('role') == 'user' and 'Soft turn budget reached' in str(m.get('content'))]
        self.assertEqual(len(nudges), 1)


class CodexLimitMessageTests(unittest.TestCase):
    def test_real_codex_limit_text_is_detected_with_absolute_reset(self):
        found = LimitErrorDetector.detect(CODEX_LIMIT, now=datetime(2026, 11, 9, 23, 0).timestamp())
        self.assertTrue(found['exhausted'])
        self.assertEqual(found['resets_at'], datetime(2026, 11, 10, 3, 24).timestamp())

    def test_month_day_without_year_rolls_forward(self):
        now = datetime(2026, 12, 31, 23, 0).timestamp()
        self.assertEqual(LimitErrorDetector.resets_at('try again at Jan 1st 1:05 AM', now),
                         datetime(2027, 1, 1, 1, 5).timestamp())


if __name__ == '__main__':
    unittest.main()
