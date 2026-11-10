"""P2.1-02 API profiles / isolated OpenCode config and P2.1-02b OpenRouter free-model discovery."""
import json
import tempfile
import unittest
from pathlib import Path

from core.configuration import validate_config
from core.opencode_config import (api_profile, check_credential, isolated_env, qualified_model,
                                  runtime_config, VENDORS)
from core.openrouter_free import FreeModelSelector, candidates, is_zero_priced
from core.quota import account_for

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT / 'tests/fixtures/openrouter/models_20261111.json').read_text(encoding='utf-8'))


def provider(**profile):
    return {'type': 'opencode_cli', 'api_profile': {'vendor': 'openrouter', **profile}}


class ApiProfileTests(unittest.TestCase):
    def test_every_vendor_profile_normalizes(self):
        for vendor in VENDORS:
            extra = {'base_url': 'http://127.0.0.1:1234/v1'} if vendor == 'local' else {}
            profile = api_profile({'type': 'opencode_cli', 'api_profile': {'vendor': vendor, **extra}})
            self.assertEqual(profile['account'], f'api:{vendor}')
            self.assertTrue(profile['base_url'])
            self.assertEqual(profile['require_api_key'], vendor != 'local')

    def test_invalid_profiles_rejected(self):
        with self.assertRaises(ValueError):
            api_profile({'api_profile': {'vendor': 'nope'}})
        with self.assertRaises(ValueError):
            api_profile({'api_profile': {'vendor': 'local'}})  # no endpoint
        with self.assertRaises(ValueError):
            api_profile({'api_profile': {'vendor': 'openai', 'auto_free': True}})
        with self.assertRaises(ValueError):
            validate_config({'providers': {'x': {'type': 'http_llm', 'api_profile': {'vendor': 'openrouter'}}}})
        with self.assertRaises(ValueError):
            validate_config({'providers': {'x': {'type': 'opencode_cli', 'api_profile': {'vendor': 'bad'}}}})

    def test_example_config_profile_is_valid(self):
        config = json.loads((ROOT / 'config/workflow_config.example.json').read_text(encoding='utf-8'))
        validate_config(config)
        profile = api_profile(config['providers']['opencode_api_openrouter'])
        self.assertTrue(profile['free_only'] and profile['auto_free'])

    def test_credential_is_referenced_by_name_only(self):
        profile = api_profile(provider(api_key_env='OPENROUTER_API_KEY'))
        config = runtime_config(profile, 'nvidia/nemotron-3-ultra-550b-a55b:free')
        text = json.dumps(config)
        self.assertIn('{env:OPENROUTER_API_KEY}', text)
        self.assertNotIn('sk-or-', text)
        self.assertEqual(config['model'], 'openrouter/nvidia/nemotron-3-ultra-550b-a55b:free')
        self.assertEqual(list(config['provider']['openrouter']['models']), ['nvidia/nemotron-3-ultra-550b-a55b:free'])
        self.assertEqual(qualified_model(profile, config['model']), config['model'])  # idempotent

    def test_missing_credential_fails_before_launch(self):
        profile = api_profile(provider())
        with self.assertRaises(ValueError):
            check_credential(profile, {})
        check_credential(profile, {'OPENROUTER_API_KEY': 'present'})
        local = api_profile({'api_profile': {'vendor': 'local', 'base_url': 'http://127.0.0.1:1/v1'}})
        check_credential(local, {})

    def test_isolated_env_strips_inherited_opencode_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = isolated_env({'OPENCODE_CONFIG': 'C:/user/opencode.json', 'OPENCODE_PERMISSION': '{}', 'PATH': 'p'},
                               Path(tmp) / 'home', {'model': 'x/y'})
            self.assertNotIn('OPENCODE_CONFIG', env)
            self.assertNotIn('OPENCODE_PERMISSION', env)
            self.assertEqual(env['PATH'], 'p')
            self.assertEqual(json.loads(env['OPENCODE_CONFIG_CONTENT']), {'model': 'x/y'})
            for name in ('XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME'):
                self.assertTrue(Path(env[name]).is_dir())
                self.assertTrue(Path(env[name]).is_relative_to(Path(tmp)))
            self.assertEqual(env['OPENCODE_DISABLE_CLAUDE_CODE'], 'true')

    def test_quota_accounts(self):
        self.assertEqual(account_for(provider()), 'api:openrouter')
        self.assertEqual(account_for(provider(account='api:custom')), 'api:custom')
        self.assertEqual(account_for({'type': 'http_llm', 'base_url': 'https://openrouter.ai/api/v1/chat/completions'}),
                         'api:openrouter')
        self.assertIsNone(account_for({'type': 'opencode_cli'}))


class FreeModelTests(unittest.TestCase):
    def test_only_zero_priced_tool_models_and_no_router(self):
        ids = [m['id'] for m in candidates(CATALOG)]
        self.assertTrue(ids)
        self.assertNotIn('openrouter/free', ids)
        for model in CATALOG['data']:
            if model['id'] in ids:
                self.assertTrue(is_zero_priced(model))
                self.assertIn('tools', model['supported_parameters'])
        self.assertNotIn('google/lyria-3-pro-preview', ids)  # priced

    def test_ranking_prefers_agentic_for_workers_and_intelligence_for_fast(self):
        worker = [m['id'] for m in candidates(CATALOG, 'worker')]
        self.assertEqual(worker[0], 'nvidia/nemotron-3-ultra-550b-a55b:free')
        # A benchmarked general model outranks tiny or unbenchmarked ones.
        self.assertLess(worker.index('thinkingmachines/inkling-2-small:free'), worker.index('liquid/lfm-2.5-2.6b:free'))
        self.assertLess(worker.index('liquid/lfm-2.5-2.6b:free'), worker.index('recursive/flint-v1.0:free'))
        fast = [m['id'] for m in candidates(CATALOG, 'fast')]
        self.assertLess(fast.index('thinkingmachines/inkling-2-small:free'), fast.index('google/gemma-4-31b-it:free'))

    def test_priced_model_is_never_selected(self):
        priced = {'data': [{'id': 'vendor/paid', 'pricing': {'prompt': '0.000001', 'completion': '0'},
                            'supported_parameters': ['tools'], 'context_length': 10 ** 6}]}
        self.assertEqual(candidates(priced), [])
        self.assertFalse(is_zero_priced({'pricing': {}}))
        self.assertFalse(is_zero_priced({'pricing': {'prompt': '0', 'completion': '0', 'request': '0.01'}}))

    def test_selector_cache_cooldown_and_stale_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = [1000.0]
            calls = []

            def fetch():
                calls.append(1)
                if len(calls) > 1:
                    raise OSError('offline')
                return CATALOG

            selector = FreeModelSelector(tmp, fetch=fetch, clock=lambda: now[0], ttl=100)
            first = selector.select('worker')
            self.assertEqual(first, 'nvidia/nemotron-3-ultra-550b-a55b:free')
            cached = json.loads(selector.cache_path.read_text(encoding='utf-8'))
            self.assertTrue(all(is_zero_priced(m) for m in cached['data']))  # priced models never cached
            selector.cool_down(first, now[0] + 50)
            second = selector.select('worker')
            self.assertNotEqual(second, first)
            now[0] += 60  # cooldown expired
            self.assertEqual(selector.select('worker'), first)
            now[0] += 500  # cache stale, refresh fails -> stale cache still used
            self.assertEqual(selector.select('worker'), first)
            self.assertEqual(len(calls), 2)
            self.assertTrue(selector.is_free(first))
            self.assertFalse(selector.is_free('vendor/paid'))

    def test_all_cooling_down_returns_none_with_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            selector = FreeModelSelector(tmp, fetch=lambda: CATALOG, clock=lambda: 10.0)
            for model in selector.ranked('worker'):
                selector.cool_down(model['id'], 99.0)
            self.assertIsNone(selector.select('worker'))
            self.assertEqual(selector.next_reset(), 99.0)


if __name__ == '__main__':
    unittest.main()
