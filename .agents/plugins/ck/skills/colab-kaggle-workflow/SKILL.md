---
name: colab-kaggle-workflow
description: >-
  Create, inspect, execute, monitor live logs, and cleanly disconnect/cancel
  task-scoped local, personal Google Colab Pro (A100/L4/T4/TPU) and Kaggle GPU
  notebooks.
---

# Unified Colab & Kaggle Cloud Notebook Workflow

Use the `ck_nb` MCP server, `scripts/cloud.py`, or the integrated Playwright CDP helpers for zero-click execution on Google Colab and Kaggle.

## 1. Google Colab Pro Workflow (Zero-Click via Cốc Cốc CDP + MCP)

### Prerequisites
- Cốc Cốc is open with `--remote-debugging-port=9222` (via the Desktop shortcut `CocCoc_Colab.lnk`).
- `colab-bridge` (`127.0.0.1:8765`) is **auto-started on demand** by `CloudTools._colab()` and `scripts/cloud_hooks.py` — no manual daemon launch is required.

### One-Shot Commands (Preferred)
Use [colab_playwright.py](../../../../skills/colab-coccoc-playwright/scripts/colab_playwright.py) or `colab_call` via `scripts/cloud.py`:

1. **Connect (or reuse active tab) & auto-confirm `Connect` dialog**:
   ```powershell
   .conda\python.exe .agents/skills/colab-coccoc-playwright/scripts/colab_playwright.py auto-confirm --connect
   ```
   *(If the existing Cốc Cốc Colab tab is already connected, this returns immediately in `<0.1s` without opening a new tab.)*

2. **Switch Runtime Hardware (`A100`, `L4`, `T4`, `CPU`, `TPU`) & Rename Tab**:
   ```powershell
   .conda\python.exe .agents/skills/colab-coccoc-playwright/scripts/colab_playwright.py set-runtime --hardware A100 --title experiment.ipynb
   ```

3. **Sync & Execute Entire Notebook (`--background-last` for long training cells)**:
   ```powershell
   .conda\python.exe .agents/skills/colab-coccoc-playwright/scripts/colab_playwright.py run-notebook --notebook experiment.ipynb --hardware A100 --background-last
   ```
   - Runs setup cells (`0 .. N-2`) synchronously via MCP `run_code_cell`.
   - Triggers the final training cell (`N-1`) via `colab-run-button` so it never hits the 300-second MCP request timeout.

4. **Read Live Training Logs (`<0.2s`, no Playwright needed)**:
   ```powershell
   .conda\python.exe .agents/skills/colab-coccoc-playwright/scripts/colab_playwright.py live-log --cell-index -1 --tail 30
   ```

5. **Disconnect & Delete Runtime + Close Bridge Connection**:
   ```powershell
   .conda\python.exe .agents/skills/colab-coccoc-playwright/scripts/colab_playwright.py disconnect-runtime
   ```

---

## 2. Kaggle GPU Workflow (Batch CLI + Playwright Live Progress & Cancel)

1. **Submit a Kaggle GPU/CPU Run**:
   ```powershell
   .conda\python.exe scripts/cloud.py call kaggle_execute_notebook --args "{\"notebook_path\": \"experiment.ipynb\", \"hardware\": \"T4\", \"kaggle_kernel_slug\": \"OWNER/KERNEL\"}"
   ```
2. **Poll Job Status**:
   ```powershell
   .conda\python.exe scripts/cloud.py call kaggle_job_status --args "{\"job_id\": \"<job_id>\"}"
   ```
3. **Read Live Training Progress on Kaggle Editor (via Cốc Cốc CDP)**:
   ```powershell
   .conda\python.exe .agents/skills/kaggle-playwright-cancel/scripts/kaggle_cancel.py --slug OWNER/KERNEL --progress-only
   ```
4. **Cancel / Stop Running Kaggle GPU Worker Immediately**:
   ```powershell
   .conda\python.exe .agents/skills/kaggle-playwright-cancel/scripts/kaggle_cancel.py --slug OWNER/KERNEL
   ```
5. **Export Completed Outputs**:
   ```powershell
   .conda\python.exe scripts/cloud.py call kaggle_export_data --args "{\"job_id\": \"<job_id>\"}"
   ```
