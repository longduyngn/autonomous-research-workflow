"""Playwright CDP & MCP orchestration helper for Google Colab on Coc Coc browser."""
import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

COCCOC_PATH = r"C:\Program Files\CocCoc\Browser\Application\browser.exe"
CDP_PORT = int(os.environ.get("COLAB_CDP_PORT", "9222"))
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
URL_FILE = ROOT / "storage/tools/colab_connect_url.txt"


def is_port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0


def ensure_coccoc_cdp(port: int = CDP_PORT) -> dict:
    if is_port_open(port):
        return {"status": "already_running", "cdp_url": f"http://127.0.0.1:{port}"}
    user_data = Path(os.environ.get("LOCALAPPDATA", "")) / "CocCoc/Browser/User Data"
    cmd = [
        COCCOC_PATH,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data}",
    ]
    subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    for _ in range(20):
        if is_port_open(port):
            return {"status": "started", "cdp_url": f"http://127.0.0.1:{port}"}
        time.sleep(0.5)
    return {
        "status": "port_not_open",
        "note": "Double-click CocCoc_Colab shortcut on Desktop to start Coc Coc with --remote-debugging-port=9222",
    }


def _find_colab_page(browser):
    pages = []
    for context in browser.contexts:
        for page in context.pages:
            if "colab.research.google.com" in page.url or "colab.google.com" in page.url:
                pages.append(page)
    return pages[-1] if pages else None


def auto_confirm_mcp(timeout_sec: int = 50, open_from_url_file: bool = True) -> dict:
    from playwright.sync_api import sync_playwright

    deadline = time.time() + timeout_sec
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        if open_from_url_file:
            for _ in range(45):
                if URL_FILE.exists() and URL_FILE.read_text(encoding="utf-8").strip():
                    break
                time.sleep(0.5)
            if URL_FILE.exists():
                url = URL_FILE.read_text(encoding="utf-8").strip()
                if url:
                    for old_pg in list(ctx.pages):
                        if "colab.research.google.com/notebooks/empty.ipynb" in old_pg.url:
                            try:
                                old_pg.close()
                            except Exception:
                                pass
                    page = ctx.new_page()
                    page.goto(url, timeout=30000, wait_until="commit")
                    page.bring_to_front()

        while time.time() < deadline:
            page = _find_colab_page(browser)
            if page:
                page.bring_to_front()
                try:
                    click_res = page.evaluate("""() => {
                        const mcpDlg = document.querySelector('colab-local-mcp-connect-dialog');
                        if (mcpDlg) {
                            const parentDialog = mcpDlg.closest('mwc-dialog, md-dialog, colab-dialog') || mcpDlg.parentElement;
                            if (parentDialog) {
                                const primaryBtn = parentDialog.querySelector('[slot="primaryAction"], md-text-button[slot="primaryAction"]');
                                if (primaryBtn) {
                                    const rect = primaryBtn.getBoundingClientRect();
                                    if (rect.width > 0 && rect.height > 0) {
                                        primaryBtn.click();
                                        const inner = primaryBtn.shadowRoot && primaryBtn.shadowRoot.querySelector('button');
                                        if (inner) inner.click();
                                        return {
                                            status: 'clicked',
                                            button: (primaryBtn.innerText || primaryBtn.textContent || '').trim(),
                                            slot: primaryBtn.getAttribute('slot') || 'primaryAction'
                                        };
                                    }
                                }
                            }
                        }
                        for (const dlg of document.querySelectorAll('mwc-dialog, md-dialog, colab-dialog')) {
                            const primaryBtn = dlg.querySelector('[slot="primaryAction"]');
                            if (primaryBtn) {
                                const txt = (primaryBtn.innerText || primaryBtn.textContent || '').trim().toLowerCase();
                                const rect = primaryBtn.getBoundingClientRect();
                                if (rect.width > 0 && rect.height > 0 && (txt === 'connect' || txt === 'kết nối' || txt === 'allow' || txt === 'ok')) {
                                    primaryBtn.click();
                                    const inner = primaryBtn.shadowRoot && primaryBtn.shadowRoot.querySelector('button');
                                    if (inner) inner.click();
                                    return {status: 'clicked', button: txt, slot: 'primaryAction'};
                                }
                            }
                        }
                        return null;
                    }""")
                    if click_res:
                        return {**click_res, "url": page.url}
                except Exception:
                    pass
            time.sleep(1.5)
    return {"status": "timeout", "note": "No confirmation button found within timeout"}


def set_runtime(hardware: str = "A100", notebook_title: str = "") -> dict:
    from playwright.sync_api import sync_playwright

    hw = hardware.upper()
    val_map = {
        "CPU": "",
        "A100": "GPU,A100",
        "L4": "GPU,L4",
        "T4": "GPU,T4",
        "G4": "GPU,G4",
        "H100": "GPU,H100",
        "TPU": "TPU,V6E1",
        "V6E1": "TPU,V6E1",
        "V5E1": "TPU,V5E1",
    }
    radio_val = val_map.get(hw, f"GPU,{hw}")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        page = _find_colab_page(browser)
        if not page:
            return {"status": "error", "error": "No open Colab tab found"}
        page.bring_to_front()
        if notebook_title:
            page.evaluate("""(title) => {
                const docName = document.querySelector('#doc-name, input.doc-name');
                if (docName) {
                    docName.focus();
                    docName.value = title;
                    docName.dispatchEvent(new Event('input', {bubbles: true}));
                    docName.dispatchEvent(new Event('change', {bubbles: true}));
                    docName.blur();
                }
            }""", notebook_title)

        has_sel = page.evaluate("() => !!document.querySelector('colab-runtime-attributes-selector')")
        if not has_sel:
            page.locator("#runtime-menu-button").click()
            time.sleep(0.6)
            page.evaluate("""() => {
                const item = document.querySelector('[command="change-runtime-type"]');
                if (item) {
                    item.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                    item.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                    item.click();
                }
            }""")
            time.sleep(1.2)

        sel_res = page.evaluate("""(targetVal) => {
            const sel = document.querySelector('colab-runtime-attributes-selector');
            if (!sel || !sel.shadowRoot) return {status: 'error', error: 'no colab-runtime-attributes-selector'};
            let radio = sel.shadowRoot.querySelector(`mwc-radio[value="${targetVal}"]`);
            let actualVal = targetVal;
            if (!radio || radio.disabled || radio.hasAttribute('disabled')) return {status: 'error', error: `Requested hardware unavailable: ${targetVal}`};
            const innerInput = radio.shadowRoot && radio.shadowRoot.querySelector('input');
            if (innerInput) innerInput.click();
            else radio.click();
            return {status: 'selected', radioValue: actualVal, checked: radio.checked};
        }""", radio_val)
        if sel_res.get("status") == "error":
            return sel_res
        time.sleep(0.8)

        save_res = page.evaluate("""() => {
            const sel = document.querySelector('colab-runtime-attributes-selector');
            const dlg = sel ? sel.closest('mwc-dialog, md-dialog, colab-dialog') : null;
            if (dlg) {
                const saveBtn = dlg.querySelector('[slot="primaryAction"]');
                if (saveBtn) {
                    const innerBtn = saveBtn.shadowRoot && saveBtn.shadowRoot.querySelector('button');
                    if (innerBtn) innerBtn.click();
                    else saveBtn.click();
                    return {saved: true};
                }
            }
            return {saved: false};
        }""")

        # Auto-confirm any follow-up dialogs (e.g., terminating previous runtime or fallback notice)
        confirmed_dialogs = []
        for _ in range(3):
            time.sleep(1.2)
            conf = page.evaluate("""() => {
                for (const dlg of document.querySelectorAll('mwc-dialog, md-dialog, colab-dialog')) {
                    if (dlg.querySelector('colab-runtime-attributes-selector')) continue;
                    const btn = dlg.querySelector('[slot="primaryAction"]');
                    if (btn) {
                        const r = btn.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) {
                            const txt = (btn.innerText || btn.textContent || '').trim();
                            const inner = btn.shadowRoot && btn.shadowRoot.querySelector('button');
                            if (inner) inner.click();
                            else btn.click();
                            return txt;
                        }
                    }
                }
                return null;
            }""")
            if conf:
                confirmed_dialogs.append(conf)

        return {**sel_res, **save_res, "confirmed_dialogs": confirmed_dialogs, "hardware": hw, "url": page.url}


def trigger_cell_run(cell_id: str = None, cell_index: int = -1) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        page = _find_colab_page(browser)
        if not page:
            return {"status": "error", "error": "No open Colab tab found"}
        page.bring_to_front()
        return page.evaluate("""({cellId, cellIndex}) => {
            const cells = Array.from(document.querySelectorAll('.cell, colab-cell'));
            let target = (cellId && document.getElementById('cell-' + cellId))
                || (cellIndex >= 0 && cells[cellIndex])
                || cells[cells.length - 1];
            if (!target) return {status: 'error', error: 'cell_not_found'};
            target.scrollIntoView({block: 'center'});
            const runBtn = target.querySelector('colab-run-button');
            if (runBtn) {
                const inner = runBtn.shadowRoot && runBtn.shadowRoot.querySelector('button');
                if (inner) inner.click();
                else runBtn.click();
                return {status: 'clicked_run_button', cellId};
            }
            return {status: 'error', error: 'no_run_button'};
        }""", {"cellId": cell_id, "cellIndex": cell_index})


def disconnect_runtime() -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        page = _find_colab_page(browser)
        if not page:
            return {"status": "error", "error": "No open Colab tab found"}
        page.bring_to_front()
        page.locator("#runtime-menu-button").click()
        time.sleep(0.6)
        clicked = page.evaluate("""() => {
            const item = document.querySelector('[command="powerwash-current-vm"]');
            if (item) {
                item.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                item.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                item.click();
                return item.innerText.trim();
            }
            return null;
        }""")
        if not clicked:
            return {"status": "menu_item_not_found", "url": page.url}
        time.sleep(1.2)
        confirm = page.evaluate("""() => {
            for (const dlg of document.querySelectorAll('mwc-dialog, md-dialog, colab-dialog')) {
                const btn = dlg.querySelector('[slot="primaryAction"]');
                if (btn) {
                    const r = btn.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {
                        const txt = (btn.innerText || btn.textContent || '').trim();
                        btn.click();
                        const inner = btn.shadowRoot && btn.shadowRoot.querySelector('button');
                        if (inner) inner.click();
                        return txt;
                    }
                }
            }
            return null;
        }""")
        return {"status": "disconnected", "menu_item": clicked, "confirmed": confirm, "url": page.url}


def main():
    parser = argparse.ArgumentParser(description="Colab Coc Coc Playwright & MCP helper")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ensure-browser")
    p_conf = sub.add_parser("auto-confirm")
    p_conf.add_argument("--connect", action="store_true", help="Also call colab_connect concurrently")
    p_rt = sub.add_parser("set-runtime")
    p_rt.add_argument("--hardware", default="A100")
    p_rt.add_argument("--title", default="")
    p_trig = sub.add_parser("trigger-cell")
    p_trig.add_argument("--cell-id", default=None)
    p_trig.add_argument("--cell-index", type=int, default=-1)
    p_run = sub.add_parser("run-notebook")
    p_run.add_argument("--notebook", required=True)
    p_run.add_argument("--hardware", default="A100")
    p_run.add_argument("--background-last", action="store_true")
    p_log = sub.add_parser("live-log")
    p_log.add_argument("--cell-index", type=int, default=-1)
    p_log.add_argument("--tail", type=int, default=30)
    p_disc = sub.add_parser("disconnect-runtime")
    p_disc.add_argument("--no-release", action="store_true", help="Keep colab-bridge session open")
    args = parser.parse_args()

    from main import load_dotenv, load_config
    from core.cloud import CloudTools

    load_dotenv(ROOT / ".env")
    tools = CloudTools(ROOT, load_config())

    if args.cmd == "ensure-browser":
        print(json.dumps(ensure_coccoc_cdp(), ensure_ascii=False, indent=2))
    elif args.cmd == "auto-confirm":
        if args.connect:
            print(json.dumps(tools.colab_connect({"force": False}), ensure_ascii=False, indent=2))
        else:
            print(json.dumps(auto_confirm_mcp(), ensure_ascii=False, indent=2))
    elif args.cmd == "set-runtime":
        print(json.dumps(set_runtime(args.hardware, args.title), ensure_ascii=False, indent=2))
    elif args.cmd == "trigger-cell":
        print(json.dumps(trigger_cell_run(args.cell_id, args.cell_index), ensure_ascii=False, indent=2))
    elif args.cmd == "run-notebook":
        out = tools.colab_call({
            "name": "run_notebook",
            "arguments": {
                "notebook_path": args.notebook,
                "hardware": args.hardware,
                "background_last_cell": args.background_last,
            },
        })
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif args.cmd == "live-log":
        out = tools.colab_call({
            "name": "read_live_output",
            "arguments": {"cell_index": args.cell_index, "tail_lines": args.tail},
        })
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif args.cmd == "disconnect-runtime":
        res = disconnect_runtime()
        if not args.no_release:
            try:
                res["bridge_release"] = tools.colab_release({"force": True})
            except Exception as exc:
                res["bridge_release_error"] = str(exc)
        print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
