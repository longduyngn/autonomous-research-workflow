"""Inspect live progress or request cancellation of one exact Kaggle notebook version via Playwright CDP.

Cancellation targets only the version recorded at submission (``--version``). The result is
``cancel_requested``, never ``cancelled``: the controller verifies the stop afterwards with
``kaggle kernels status``. Without a matching version row nothing is clicked.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def select_version_row(row_texts, version):
    """Index of the row whose text names exactly ``Version <version>``; None if absent or ambiguous."""
    pattern = re.compile(rf"\bVersion\s+{int(version)}\b(?!\.\d)", re.I)
    matches = [i for i, text in enumerate(row_texts) if pattern.search(text or "")]
    return matches[0] if len(matches) == 1 else None


def cancel_kaggle_kernel(slug: str, cdp_port: int = 9222, progress_only: bool = False, version=None) -> dict:
    from playwright.sync_api import sync_playwright

    cdp_url = f"http://127.0.0.1:{cdp_port}"
    clean_slug = slug.strip("/").replace("https://www.kaggle.com/code/", "").split("/edit")[0]
    edit_url = f"https://www.kaggle.com/code/{clean_slug}/edit"
    if not progress_only and version is None:
        return {"status": "refused", "slug": clean_slug,
                "error": "Cancellation requires the submitted version; nothing was clicked."}

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            return {"status": "error", "error": f"Could not connect to browser on port {cdp_port}: {e}"}

        page = None
        for ctx in browser.contexts:
            for pg in ctx.pages:
                if clean_slug in pg.url:
                    page = pg
                    break
            if page:
                break
        if not page:
            page = browser.contexts[0].new_page()
        if "/edit" not in page.url:
            page.goto(edit_url, timeout=30000, wait_until="domcontentloaded")
        page.bring_to_front()
        time.sleep(3)

        live_lines = []
        for fr in page.frames:
            try:
                txt = fr.evaluate("() => document.body ? document.body.innerText : ''")
                for line in (txt or "").splitlines():
                    if any(k in line for k in ["Epoch", "Loss", "Step", "Downloading", "%|"]):
                        live_lines.append(line.strip())
            except Exception:
                pass
        tail_progress = "\n".join(live_lines[-20:])
        if progress_only:
            return {"status": "running", "slug": clean_slug, "live_progress": tail_progress}

        ver_btn = page.locator("button[aria-label='Show Versions'], button:has-text('Versions')")
        if ver_btn.count() > 0 and ver_btn.first.is_visible():
            ver_btn.first.click()
            time.sleep(1.5)
        rows = page.locator("[role='listitem'], li").all()
        index = select_version_row([(r.text_content() or "") for r in rows], version)
        if index is None:
            return {"status": "version_not_found", "slug": clean_slug, "version": version,
                    "last_progress": tail_progress, "note": "No unique row for this version; nothing was clicked."}
        row = rows[index]
        more_btn = row.locator("button[aria-label='More options for this version']")
        if more_btn.count() == 0:
            return {"status": "button_not_found", "slug": clean_slug, "version": version,
                    "note": "Version may already be finished or stopped."}
        more_btn.first.click()
        time.sleep(1)
        for item in page.locator("ul[role='menu'] li, div[role='menuitem']").all():
            txt = (item.text_content() or "").strip().lower()
            if any(w in txt for w in ["cancel", "stop", "dừng", "hủy"]):
                item.click()
                time.sleep(1)
                confirm = page.locator("button:has-text('Cancel Run')")
                if confirm.count() > 0 and confirm.first.is_visible():
                    confirm.first.click()
                return {"status": "cancel_requested", "slug": clean_slug, "version": version,
                        "method": "version_options_menu", "last_progress": tail_progress,
                        "verify": "Controller confirms the stop with `kaggle kernels status`."}
        return {"status": "button_not_found", "slug": clean_slug, "version": version,
                "note": "No cancel item in this version's menu."}


def main():
    parser = argparse.ArgumentParser(description="Inspect or request cancellation of a Kaggle notebook version")
    parser.add_argument("--slug", required=True, help="Kaggle owner/kernel-slug")
    parser.add_argument("--version", type=int, help="Submitted version to cancel (required to cancel)")
    parser.add_argument("--cdp-port", type=int, default=9222, help="CDP port")
    parser.add_argument("--progress-only", action="store_true", help="Read live training progress without cancelling")
    args = parser.parse_args()
    result = cancel_kaggle_kernel(args.slug, args.cdp_port, args.progress_only, args.version)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
