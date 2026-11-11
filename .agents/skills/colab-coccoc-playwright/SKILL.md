---
name: colab-coccoc-playwright
description: >-
  Automate Google Colab on Cốc Cốc browser via Playwright CDP and Colab MCP —
  auto-confirm MCP connection, switch runtime (CPU/T4/L4/A100/TPU), sync and
  execute notebooks without 300s MCP timeouts, read live training logs, and
  disconnect runtime to save compute units.
  Also mounts Google Drive (optionally auto-approving the standard Drive consent in Cốc Cốc) and
  verifies an explicit off-VM checkpoint destination.
---

# Google Colab on Cốc Cốc — Playwright CDP & MCP Skill

This skill integrates Playwright CDP (`http://127.0.0.1:9222`) with `colab-bridge` (`http://127.0.0.1:8765`, auto-started on demand) so Google Colab notebooks can be connected, switched to A100/GPU, executed, monitored, and disconnected with single commands.

## Key Technical Details Solved by This Skill
1. **Session Reuse**: Checks `colab_tools` first; if `run_code_cell` is already unlocked on the open Cốc Cốc tab, reuses the session immediately without opening a new tab or re-running Playwright.
2. **Fresh Tab Navigation for New Tokens**: When a new `#mcpProxyToken=...` is issued, closes stale `empty.ipynb` tabs and opens the URL in `ctx.new_page()` with `wait_until="commit"` so Colab's onload MCP dialog always triggers.
3. **Shadow DOM Selector for MCP Dialog**: Clicks `colab-local-mcp-connect-dialog` $\rightarrow$ parent `mwc-dialog` $\rightarrow$ `md-text-button[slot="primaryAction"]` (`Connect`).
4. **Shadow DOM Selector for Hardware (`A100` / `L4` / `T4`)**: Selects `colab-runtime-attributes-selector#shadow` $\rightarrow$ `mwc-radio[value="GPU,A100"]` (fails explicitly if the requested hardware is unavailable), clicks `Lưu`, and auto-confirms follow-up dialogs.
5. **No 300s Timeout on Long Training Cells**: Setup cells run synchronously via MCP `run_code_cell`, while long training cells (`train.py`) are triggered via `colab-run-button` (`--background-last`) and monitored via `live-log` (`get_cells(includeOutputs=True)`).

## Commands

Run via [colab_playwright.py](./scripts/colab_playwright.py):

```powershell
# 1. Check / start Coc Coc on CDP port 9222
.conda\python.exe .agents/skills/colab-coccoc-playwright/scripts/colab_playwright.py ensure-browser

# 2. Connect to Colab MCP & auto-click "Connect" (reuses session if already active)
.conda\python.exe .agents/skills/colab-coccoc-playwright/scripts/colab_playwright.py auto-confirm --connect

# 3. Switch runtime hardware (A100, L4, T4, CPU, TPU) and rename notebook tab
.conda\python.exe .agents/skills/colab-coccoc-playwright/scripts/colab_playwright.py set-runtime --hardware A100 --title experiment.ipynb

# 4. Sync & run a local .ipynb notebook on Colab (--background-last for training notebooks)
.conda\python.exe .agents/skills/colab-coccoc-playwright/scripts/colab_playwright.py run-notebook --notebook experiment.ipynb --hardware A100 --background-last

# 5. Read live output of the running cell (<0.2s via MCP get_cells)
.conda\python.exe .agents/skills/colab-coccoc-playwright/scripts/colab_playwright.py live-log --cell-index -1 --tail 30

# 6. Disconnect & delete runtime (powerwash-current-vm) + close MCP bridge connection
.conda\python.exe .agents/skills/colab-coccoc-playwright/scripts/colab_playwright.py disconnect-runtime
```

## Drive-backed checkpoints

Use this when a Colab job needs outputs to survive VM loss, including while the local controller sleeps. A saved notebook, a local task checkpoint and Colab MCP access do not grant Google Drive access. Colab may require its normal per-runtime Drive consent even when the browser is already signed in.

Check first; reuse a working mount. After the user has granted consent, retry mount once: a previous timed-out mount call may have returned before consent finished. Do not infer readiness from a closed dialog or a directory named `drive`.

```powershell
# Readiness only, no mount request or file writes on Drive.
.conda\python.exe .agents/skills/colab-coccoc-playwright/scripts/drive_checkpoint.py

# Normal mount + small publish/hash/restore probe in an explicitly chosen private directory.
.conda\python.exe .agents/skills/colab-coccoc-playwright/scripts/drive_checkpoint.py --mount --probe --destination /content/drive/MyDrive/private-research/my-campaign --receipt storage/tools/drive-probe.json
```

The helper returns `AUTH_REQUIRED` (exit 2) if the mount is unavailable. It never reads or transfers auth tokens.

### Auto-approve Drive consent (opt-in)

With `--auto-consent`, or `"colab_drive_consent": {"enabled": true, "account": ""}` in the config, a watcher
([drive_consent.py](./scripts/drive_consent.py)) runs in Cốc Cốc while `drive.mount` waits and:
- clicks only the Colab "Connect to Google Drive" dialog;
- on an `https://accounts.google.com` popup opened by a Colab tab, picks the account (the configured `account`,
  or the only one listed), ticks "Select all" and presses Continue/Allow;
- never types: password, 2-step verification or sign-up pages return `LOGIN_REQUIRED`; several accounts without
  `account` return `ACCOUNT_CHOICE_REQUIRED`. Both are reported to the user instead of being worked around.

The receipt gets `auto_consent: {state, steps}` (step kinds and button labels only).

```powershell
.conda\python.exe .agents/skills/colab-coccoc-playwright/scripts/drive_checkpoint.py --mount --auto-consent --probe --destination /content/drive/MyDrive/Checkpoint/workflow-runs/_probe --receipt storage/tools/drive-probe.json
``` Read-only status checks do not need another user confirmation. On a known runtime, pass `--expected-boot-id` to reject accidental VM changes before a mount/write.

`READY` plus `publish_restore_verified=true` proves a small synthetic checkpoint was published and restored through that Drive mount. It is not a destructive VM-loss test or proof that the actual experiment is saving automatically. Configure the experiment's raw/state/manifest/cache snapshots at safe boundaries before a long run. Keep large cache sets in archives rather than thousands of loose Drive files.

Persist the receipt and wake a waiting task only on a material state transition, such as `AUTH_REQUIRED` to verified readiness. Send the event to the scheduler's actual wake path; for this workspace that is `<campaign>/operator_wake.json`. Heartbeats alone should not repeatedly call an LLM. A new VM requires the mount/readiness check again; a skill cannot guarantee that Google will never request consent again.
