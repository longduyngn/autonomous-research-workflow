import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from core.colab_runtime import runtime_status


class RuntimeStatusTests(unittest.TestCase):
    def test_empty_connection_label_is_unknown_not_an_index_error(self):
        skill = SimpleNamespace(CDP_URL='http://fixture', _find_colab_page=MagicMock())
        page = skill._find_colab_page.return_value
        page.evaluate.return_value = {'connect_button_present': True, 'label': '', 'tooltip': ''}
        with patch('playwright.sync_api.sync_playwright'):
            result = runtime_status(skill)
        self.assertFalse(result['runtime_stopped'])
        self.assertIsNone(result['active_session_count'])
        page.locator.assert_not_called()

    def test_manual_disconnect_requires_empty_global_sessions(self):
        for sessions, expected in [('No active sessions', True), ('A100 active', False)]:
            with self.subTest(sessions=sessions):
                skill = SimpleNamespace(CDP_URL='http://fixture', _find_colab_page=MagicMock())
                page = skill._find_colab_page.return_value
                page.evaluate.side_effect = [
                    {'connect_button_present': True, 'label': 'Connect', 'tooltip': ''},
                    True, sessions]
                with patch('playwright.sync_api.sync_playwright'), patch('core.colab_runtime.ensure_runtime_menu'):
                    result = runtime_status(skill)
                self.assertEqual(result['runtime_stopped'], expected)
                self.assertEqual(result['active_session_count'], 0 if expected else None)
