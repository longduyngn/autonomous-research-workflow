"""Runtime observations supplement the repository's Colab browser skill."""
import time


def ensure_runtime_menu(page):
    """Restore Colab's collapsed header before using its runtime controls."""
    menu = page.locator('#runtime-menu-button')
    if not menu.is_visible():
        toggle = page.locator('#toggle-header-button')
        if toggle.is_visible():
            toggle.evaluate('(element) => element.click()')
            menu.wait_for(state='visible', timeout=10000)


def prepare_runtime_controls(skill):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(skill.CDP_URL)
        page = skill._find_colab_page(browser)
        if not page:
            raise RuntimeError('No Colab tab found')
        ensure_runtime_menu(page)


def runtime_status(skill):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(skill.CDP_URL)
        page = skill._find_colab_page(browser)
        if not page:
            return {'connected': None, 'error': 'No Colab tab found'}
        observed = page.evaluate("""() => {
            const host = document.querySelector('colab-connect-button');
            const button = host?.shadowRoot?.querySelector('#connect');
            return {connect_button_present: !!button,
                    label: (button?.textContent || '').trim(),
                    tooltip: button?.getAttribute('tooltiptext') || ''};
        }""")
        # Only a known idle 'connect to new runtime' state establishes disconnect.
        # Unknown/loading states are not accepted as proof.
        label = observed['label'].splitlines()[0].strip().casefold()
        idle = observed['connect_button_present'] and label in {'connect', 'reconnect', 'kết nối', 'kết nối lại'}
        no_sessions = False
        if idle:
            ensure_runtime_menu(page)
            opened = page.evaluate("() => !!document.querySelector('#sessions-dialog[open]')")
            if not opened:
                page.locator('#runtime-menu-button').click()
                page.locator('[command="manage-sessions"]').click()
            try:
                page.wait_for_function("""() => {
                    const root = document.querySelector('colab-sessions-dialog')?.shadowRoot;
                    return root && !root.querySelector('mwc-circular-progress') && root.querySelector('.dialog-table');
                }""", timeout=15000)
                empty = page.evaluate("""() => (document.querySelector('colab-sessions-dialog')?.shadowRoot
                    ?.querySelector('.dialog-table .no-results')?.textContent || '').trim()""")
                no_sessions = empty.casefold() in {'không có phiên nào đang hoạt động', 'no active sessions'}
                observed['sessions_observation'] = empty or 'Session list was not empty or not recognized'
            finally:
                page.locator('#sessions-dialog [slot="primaryAction"]').click()
        return {**observed, 'connected': False if idle and no_sessions else None,
                'runtime_stopped': idle and no_sessions,
                'active_session_count': 0 if no_sessions else None,
                'observed_at_unix': time.time(), 'evidence': 'Colab runtime connection control'}
