---
name: kaggle-playwright-cancel
description: >-
  Read live training progress or cancel/stop running Kaggle notebook GPU workers
  directly via Playwright CDP on Cốc Cốc / Chrome browser when Kaggle CLI lacks
  a native cancel command.
---

# Kaggle Live Progress & Worker Cancel Skill (Playwright CDP)

Kaggle's official CLI (`kaggle kernels`) can push and poll status (`kaggle kernels status <slug>`), but cannot read live streaming cell outputs while a version is still running and does not provide a CLI `cancel` command. This skill uses Playwright over CDP (`http://127.0.0.1:9222`) to inspect live logs or stop running GPU workers immediately.

## Commands

Run via [kaggle_cancel.py](./scripts/kaggle_cancel.py):

```powershell
# 1. Read live training progress (Epoch / Loss) from a running Kaggle kernel without stopping it
.conda\python.exe .agents/skills/kaggle-playwright-cancel/scripts/kaggle_cancel.py --slug OWNER/KERNEL --progress-only

# 2. Cancel / stop a running Kaggle GPU kernel version immediately
.conda\python.exe .agents/skills/kaggle-playwright-cancel/scripts/kaggle_cancel.py --slug OWNER/KERNEL
```

## Verification via Kaggle CLI
After cancelling, verify that Kaggle acknowledged worker termination:
```powershell
.conda\Scripts\kaggle.exe kernels status OWNER/KERNEL
```
Expected status: `KernelWorkerStatus.CANCEL_ACKNOWLEDGED` or `cancelled`.
