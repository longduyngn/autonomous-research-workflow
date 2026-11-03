---
name: colab-coccoc-playwright
description: >-
  Automate Google Colab on Cốc Cốc browser via Playwright CDP and Colab MCP —
  auto-confirm MCP connection, switch runtime (CPU/T4/L4/A100/TPU), sync and
  execute notebooks without 300s MCP timeouts, read live training logs, and
  disconnect runtime to save compute units.
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
