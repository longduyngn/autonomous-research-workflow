"""Auto-approval of the Colab Drive mount consent: pure decision rules (no browser)."""
import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / '.agents/skills/colab-coccoc-playwright/scripts/drive_consent.py'
spec = importlib.util.spec_from_file_location('drive_consent', SCRIPT)
dc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dc)
COLAB = 'https://colab.research.google.com/drive/abc'
OAUTH = 'https://accounts.google.com/signin/oauth/consent?client_id=x'


def google(url=OAUTH, opener=COLAB, pwd=False, accounts=(), buttons=(), has_all=False, checked=False, expected=''):
    return dc.decide_google_page(url, opener, pwd, list(accounts), list(buttons), has_all, checked, expected)


class ColabDialogRules(unittest.TestCase):
    def test_clicks_only_drive_connect_dialog(self):
        self.assertEqual(dc.decide_colab_dialog('Permit this notebook to access your Google Drive files?',
                                                'Connect to Google Drive')['action'], 'click')
        self.assertEqual(dc.decide_colab_dialog('Cho phép sổ tay này truy cập tệp trên Google Drive?',
                                                'Kết nối với Google Drive')['action'], 'click')

    def test_other_dialogs_and_secondary_buttons_are_ignored(self):
        self.assertIsNone(dc.decide_colab_dialog('Delete runtime?', 'OK'))
        self.assertIsNone(dc.decide_colab_dialog('Access your Google Drive files?', 'No thanks'))


class GooglePopupRules(unittest.TestCase):
    def test_only_accounts_google_popup_opened_by_colab(self):
        self.assertEqual(google(url='https://evil.example/accounts.google.com')['action'], 'ignore')
        self.assertEqual(google(url='http://accounts.google.com/x')['action'], 'ignore')
        self.assertEqual(google(opener='https://mail.google.com/')['action'], 'ignore')
        self.assertEqual(google(opener='')['action'], 'ignore')

    def test_never_types_credentials(self):
        self.assertEqual(google(pwd=True)['state'], 'LOGIN_REQUIRED')
        self.assertEqual(google(url='https://accounts.google.com/v3/signin/challenge/pwd')['state'], 'LOGIN_REQUIRED')

    def test_account_choice(self):
        self.assertEqual(google(accounts=['a@gmail.com'])['account'], 'a@gmail.com')
        self.assertEqual(google(accounts=['a@gmail.com', 'b@gmail.com'])['state'], 'ACCOUNT_CHOICE_REQUIRED')
        self.assertEqual(google(accounts=['a@gmail.com', 'b@gmail.com'], expected='B@gmail.com')['account'], 'b@gmail.com')
        self.assertEqual(google(accounts=['a@gmail.com'], expected='c@gmail.com')['state'], 'ACCOUNT_CHOICE_REQUIRED')

    def test_granular_consent_selects_all_then_continues(self):
        self.assertEqual(google(has_all=True, buttons=['Continue'])['action'], 'select_all')
        self.assertEqual(google(has_all=True, checked=True, buttons=['Cancel', 'Continue'])['button'], 'continue')
        self.assertEqual(google(buttons=['Hủy', 'Cho phép'])['button'], 'cho phép')
        self.assertEqual(google(buttons=['Cancel'])['action'], 'wait')


if __name__ == '__main__':
    unittest.main()
