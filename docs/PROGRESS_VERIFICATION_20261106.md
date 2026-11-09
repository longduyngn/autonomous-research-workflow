# W&B and supervision verification — 2026-11-06

The sequential plan and source research are in [RALPH_PROGRESS_PLAN.md](RALPH_PROGRESS_PLAN.md).
Usage, behavior and boundaries are in [PROGRESS_MONITORING.md](PROGRESS_MONITORING.md).

## Verified

- Full suite: **171/171 passed**, 54.155 seconds. Git Bash was selected in PATH
  for the real POSIX quoting test; the earlier WSL access failure was not hidden
  by skipping that test. Log: `storage/verification-local/wandb-regression-final.log`.
- `compileall`, `git diff --check` and installed dependency `pip check` passed.
- W&B SDK 0.31.0 online synthetic probe wrote three numeric points; Public API
  independently returned completed=3 and finished state. Offline SDK probe also
  persisted a completed local receipt. These are integration tests, not research
  results. Receipts: `wandb-online-receipt.json`, `wandb-offline-receipt.json`.
- A real read-only diagnostic agent was consulted on synthetic stalled-counter
  facts. It returned UNKNOWN because a fresh heartbeat without process/GPU
  evidence cannot establish a dead job; it recommended observation and explicitly
  declined cancellation/restart. Receipt: `stall-diagnostic-live-receipt.json`.
- A controller integration test performs registered launch, remote wait, restart,
  completion observation, explicit artifact download/recording, acceptance and
  finalization without repeating the original submission or consuming a retry.
- Tests cover crash-before-wait commit, stale-worker fencing, duplicate Colab
  launch and Kaggle push, unavailable W&B, progress rollback/scope shrink,
  nonce check-in, concurrent diagnostic claims, late diagnosis after new progress,
  invalid diagnosis, compatible checkpoint recovery, safe-boundary budget stop,
  GPU heartbeat without counter advance, and missing/incomplete artifacts.
- Dashboard rendered in a fresh headless browser against an isolated DB. The
  experiment progress bar showed 35/100 separately from milestones; no JS errors.
  Screenshot: `storage/verification-local/progress-ui/dashboard.png`.
- The existing Discord listener was replaced only after zero active tasks were
  confirmed. PID 9464 reports listening; new job/review tables are initialized.
  Source hashes and deployment observations are in `progress-deployment-receipt.json`.

## Authentication and operational boundaries

The W&B SDK resolved an existing host login and verified it online. Login
instructions were sent to the task-proposal Discord channel, followed by a
sanitized correction that the user should not run relogin preemptively. One
attempt to include account identity/status in that correction was rejected by
automatic approval review; the replacement message omitted those details and
was delivered. No key was requested in chat, printed or transferred to a VM.

No GPU job, task-33 rerun or paid rental was started for verification. Colab and
Kaggle adapter behavior was exercised with fixtures; the new wrapper has not yet
been observed on a live GPU experiment. Their platform secrets are independent
of host login. Local process registration does not by itself detach a process
from an agent. A budget-expired/unreachable remote runtime is retained for
checkpoint/stop reconciliation, not assumed terminated or forcibly released.

This implementation provides durable supervision and telemetry, not a guarantee
of full benchmark reproduction. Original cohort/acceptance scope still has to
be established correctly and independently validated.
