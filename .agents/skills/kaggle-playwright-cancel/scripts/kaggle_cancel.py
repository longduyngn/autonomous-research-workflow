"""Inspect live progress or cancel running Kaggle notebook via Playwright CDP on active browser."""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def cancel_kaggle_kernel(slug: str, cdp_port: int = 9222, progress_only: bool = False) -> dict:
    from playwright.sync_api import sync_playwright

    cdp_url = f"http://127.0.0.1:{cdp_port}"
    clean_slug = slug.strip("/").replace("https://www.kaggle.com/code/", "").split("/edit")[0]
    edit_url = f"https://www.kaggle.com/code/{clean_slug}/edit"

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

        # Read any visible training progress across frames
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

        # 1. Open Show Versions drawer
        ver_btn = page.locator("button[aria-label='Show Versions'], button:has-text('Versions')")
        if ver_btn.count() > 0 and ver_btn.first.is_visible():
            ver_btn.first.click()
            time.sleep(1.5)

        # 2. Click more options on top running version
        more_btn = page.locator("button[aria-label='More options for this version']")
        if more_btn.count() > 0 and more_btn.first.is_visible():
            more_btn.first.click()
            time.sleep(1)

            # 3. Find and click Cancel / Stop (stopCancel)
            for item in page.locator("ul[role='menu'] li, div[role='menuitem'], button").all():
                txt = (item.text_content() or "").strip()
                if any(w in txt.lower() for w in ["cancel", "stop", "dừng", "hủy"]):
                    item.click()
                    time.sleep(1)
                    for conf in ["Cancel Run", "Yes", "Confirm", "OK"]:
                        c = page.locator(f"button:has-text('{conf}')")
                        if c.count() > 0 and c.first.is_visible():
                            c.first.click()
                            break
                    return {
                        "status": "cancelled",
                        "slug": clean_slug,
                        "method": "version_options_menu",
                        "last_progress": tail_progress,
                    }

        return {
            "status": "button_not_found",
            "slug": clean_slug,
            "last_progress": tail_progress,
            "note": "Kernel may already be finished or stopped.",
        }


def main():
    parser = argparse.ArgumentParser(description="Inspect or cancel running Kaggle notebook via Playwright CDP")
    parser.add_argument("--slug", required=True, help="Kaggle owner/kernel-slug")
    parser.add_argument("--cdp-port", type=int, default=9222, help="CDP port")
    parser.add_argument("--progress-only", action="store_true", help="Read live training progress without cancelling")
    args = parser.parse_args()

    result = cancel_kaggle_kernel(args.slug, args.cdp_port, args.progress_only)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
