"""Auto-approve the standard Colab Google Drive mount consent in the user's Cốc Cốc profile (opt-in).

Scope, enforced by the pure decision functions below:
- Colab tab: only the "connect to Google Drive" dialog (text mentions Google Drive; primary button is the
  connect action). Other dialogs are left alone.
- Google popup: only https://accounts.google.com pages opened by a Colab tab. It may pick the signed-in
  account (the configured one, or the only one listed), tick "Select all" on the granular consent page and
  press Continue/Allow.
- It never types anything: a password, 2-step verification, CAPTCHA or "add account" page stops with
  LOGIN_REQUIRED for the user. Several accounts without a configured choice stop with ACCOUNT_CHOICE_REQUIRED.
- It never reads cookies or tokens. Each click is recorded (kind + button text only) in the result.

Enable in config: "colab_drive_consent": {"enabled": true, "account": "<optional email of the Colab account>"}.
"""
import re
import time
from urllib.parse import urlparse

CONNECT_WORDS = ('connect to google drive', 'kết nối với google drive', 'connect', 'kết nối')
CONTINUE_WORDS = ('continue', 'tiếp tục', 'allow', 'cho phép', 'next', 'tiếp theo')
SELECT_ALL_WORDS = ('select all', 'chọn tất cả')
STOP_PATH_PARTS = ('/challenge', '/pwd', '/signin/v2/challenge', '/v3/signin/challenge', 'speedbump', '/signup')


def _norm(text):
    return re.sub(r'\s+', ' ', str(text or '')).strip().lower()


def is_colab_url(url):
    host = urlparse(url or '').hostname or ''
    return host in ('colab.research.google.com', 'colab.google.com')


def decide_colab_dialog(dialog_text, button_text):
    """Click only the primary connect button of the Drive permission dialog."""
    text, button = _norm(dialog_text), _norm(button_text)
    if 'google drive' not in text and 'drive' not in button:
        return None
    if button in CONNECT_WORDS:
        return {'action': 'click', 'kind': 'colab_drive_dialog', 'button': button}
    return None


def decide_google_page(url, opener_url, has_password_field, accounts, buttons, has_select_all,
                       select_all_checked, expected_account=''):
    """Return the single next action for a Google sign-in/consent popup, or a stop state.

    accounts: emails shown in an account chooser; buttons: visible button labels.
    """
    parsed = urlparse(url or '')
    if parsed.scheme != 'https' or parsed.hostname != 'accounts.google.com':
        return {'action': 'ignore', 'reason': 'not accounts.google.com'}
    if not is_colab_url(opener_url):
        return {'action': 'ignore', 'reason': 'popup not opened by a Colab tab'}
    path = parsed.path.lower()
    if has_password_field or any(part in path for part in STOP_PATH_PARTS):
        return {'action': 'stop', 'state': 'LOGIN_REQUIRED',
                'reason': 'password, verification or sign-up page; the user must sign in'}
    emails = [_norm(a) for a in accounts if '@' in str(a)]
    if emails:
        expected = _norm(expected_account)
        if expected:
            if expected in emails:
                return {'action': 'choose_account', 'account': expected}
            return {'action': 'stop', 'state': 'ACCOUNT_CHOICE_REQUIRED',
                    'reason': 'configured Colab account is not signed in here'}
        if len(emails) == 1:
            return {'action': 'choose_account', 'account': emails[0]}
        return {'action': 'stop', 'state': 'ACCOUNT_CHOICE_REQUIRED',
                'reason': 'several accounts; set colab_drive_consent.account'}
    if has_select_all and not select_all_checked:
        return {'action': 'select_all'}
    labels = [_norm(b) for b in buttons]
    for word in CONTINUE_WORDS:
        if word in labels:
            return {'action': 'click', 'kind': 'google_consent', 'button': word}
    return {'action': 'wait'}


COLAB_DIALOG_JS = """() => {
  for (const dlg of document.querySelectorAll('mwc-dialog, md-dialog, colab-dialog')) {
    const btn = dlg.querySelector('[slot="primaryAction"]');
    if (!btn) continue;
    const r = btn.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) continue;
    return {text: (dlg.innerText || dlg.textContent || ''), button: (btn.innerText || btn.textContent || '')};
  }
  return null;
}"""

COLAB_CLICK_JS = """() => {
  for (const dlg of document.querySelectorAll('mwc-dialog, md-dialog, colab-dialog')) {
    const btn = dlg.querySelector('[slot="primaryAction"]');
    if (!btn) continue;
    const r = btn.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) continue;
    btn.click();
    const inner = btn.shadowRoot && btn.shadowRoot.querySelector('button');
    if (inner) inner.click();
    return true;
  }
  return false;
}"""

GOOGLE_STATE_JS = """() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const accounts = [...document.querySelectorAll('[data-identifier], [data-email]')]
    .filter(vis).map(e => e.getAttribute('data-identifier') || e.getAttribute('data-email'));
  const buttons = [...document.querySelectorAll('button, [role="button"]')]
    .filter(vis).map(e => (e.innerText || e.textContent || '').trim()).filter(Boolean);
  const pwd = [...document.querySelectorAll('input[type="password"]')].some(vis);
  let selectAll = null;
  for (const box of document.querySelectorAll('input[type="checkbox"], [role="checkbox"]')) {
    const label = ((box.getAttribute('aria-label') || '') + ' ' +
                   ((box.closest('label, div') || {}).innerText || '')).toLowerCase();
    if (label.includes('select all') || label.includes('chọn tất cả')) {
      selectAll = box.checked === true || box.getAttribute('aria-checked') === 'true';
      break;
    }
  }
  return {accounts, buttons, pwd, hasSelectAll: selectAll !== null, selectAllChecked: !!selectAll};
}"""


def _opener_url(page):
    try:
        opener = page.opener()
        return opener.url if opener else ''
    except Exception:
        return ''


def _click_button(page, label):
    pattern = re.compile(r'^\s*' + re.escape(label) + r'\s*$', re.I)
    page.get_by_role('button', name=pattern).first.click(timeout=5000)


def _select_all(page):
    for name in SELECT_ALL_WORDS:
        box = page.get_by_role('checkbox', name=re.compile(re.escape(name), re.I))
        if box.count():
            box.first.check(timeout=5000)
            return
    page.get_by_text(re.compile('select all|chọn tất cả', re.I)).first.click(timeout=5000)


def run_consent_watch(cdp_url, timeout_sec=120, expected_account='', stop_event=None, sleep=time.sleep):
    """Poll Cốc Cốc pages and take allowed consent actions until stopped, finished or a stop state."""
    from playwright.sync_api import sync_playwright

    steps, deadline, oauth_seen = [], time.time() + timeout_sec, False
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        while time.time() < deadline and not (stop_event and stop_event.is_set()):
            pages = [pg for ctx in browser.contexts for pg in ctx.pages]
            for page in pages:
                try:
                    if is_colab_url(page.url):
                        found = page.evaluate(COLAB_DIALOG_JS)
                        decision = found and decide_colab_dialog(found['text'], found['button'])
                        if decision and page.evaluate(COLAB_CLICK_JS):
                            steps.append({k: decision[k] for k in ('kind', 'button')})
                        continue
                    if (urlparse(page.url).hostname or '') != 'accounts.google.com':
                        continue
                    oauth_seen = True
                    state = page.evaluate(GOOGLE_STATE_JS)
                    decision = decide_google_page(page.url, _opener_url(page), state['pwd'], state['accounts'],
                                                  state['buttons'], state['hasSelectAll'],
                                                  state['selectAllChecked'], expected_account)
                    action = decision['action']
                    if action == 'stop':
                        return {'state': decision['state'], 'reason': decision['reason'], 'steps': steps}
                    if action == 'choose_account' and '"' in decision['account']:
                        return {'state': 'ACCOUNT_CHOICE_REQUIRED', 'reason': 'unexpected account text', 'steps': steps}
                    if action == 'choose_account':
                        page.locator(f'[data-identifier="{decision["account"]}"], '
                                     f'[data-email="{decision["account"]}"]').first.click(timeout=5000)
                        steps.append({'kind': 'google_account', 'button': 'choose_account'})
                    elif action == 'select_all':
                        _select_all(page)
                        steps.append({'kind': 'google_consent', 'button': 'select_all'})
                    elif action == 'click':
                        _click_button(page, decision['button'])
                        steps.append({'kind': decision['kind'], 'button': decision['button']})
                except Exception as exc:  # page closed or navigating; retry on the next poll
                    retry = {'kind': 'retry', 'button': type(exc).__name__}
                    if 'closed' not in str(exc).lower() and (not steps or steps[-1] != retry):
                        steps.append(retry)
            sleep(1.0)
    return {'state': 'CONSENT_FLOW_SEEN' if oauth_seen else 'NO_CONSENT_PROMPT', 'steps': steps}
