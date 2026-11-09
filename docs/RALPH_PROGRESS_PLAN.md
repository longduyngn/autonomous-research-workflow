# W&B progress and recoverable supervision: Ralph plan

User objective: monitor experiment progress in W&B and task status; diagnose
suspected stalls before stopping; preserve running computation across agent
timeouts. Do not rerun task 33 or weaken its original scientific requirements.

## Research and decisions

- Reference inspected: `archive/ref/agentic_workflow/Work/lab/lab/daemon.py`
  (`idle_nudge`, running-job check-ins are not inactivity), `watchdog.py`
  (separate daemon heartbeat and deduplicated alerts), `progress.py` (reports
  derived from recorded facts), and `fleet.py` (job anomaly supervision).
  These are source references, not verified deployment on this Windows host.
- W&B primary sources: https://github.com/wandb/wandb/blob/main/wandb/apis/public/runs.py
  (run state/summary/heartbeat), https://github.com/wandb/wandb/blob/main/wandb/sdk/wandb_settings.py
  (resume/offline/settings), https://github.com/wandb/wandb/blob/main/wandb/sdk/wandb_login.py
  (environment or interactive login), https://github.com/wandb/wandb/blob/main/README.md
  (SDK initialization). Official docs URLs currently redirect to an inaccessible
  host in the research tool, so use official SDK sources plus installed-SDK tests.
- Local SQLite owns task/job identity, budgets, progress and diagnostic claims.
  W&B is optional telemetry: unavailable/auth/offline is UNKNOWN, never success,
  never a reason to resubmit compute. Run completion still requires downloaded
  artifacts and existing validator checks.
- Track useful progress (monotonic completed units), telemetry freshness and
  agent heartbeat separately. A repeated heartbeat or log chatter is not work.
- One bounded read-only diagnostic agent per suspected-stall episode; no second
  writer in the active role memory. Decisions are advisory. Existing compute and
  exact-action boundaries stay enforced.

## Sequential iterations

- [x] R0: inspect current implementation, reference and primary W&B sources.
- [x] R1: durable job registry, progress helper, optional W&B logging/polling.
- [x] R2: remote handoff, recoverable timeout, nonblocking watchdog and diagnosis.
- [x] R3: show jobs/progress/W&B/diagnosis in CLI, Discord status and dashboard.
- [x] R4: failure/restart/race tests; offline SDK and end-to-end controller probes.
- [x] R5: login handoff via Discord; deploy only with no active workers, verify.

## Acceptance evidence

Record focused tests after each iteration and full regression after integration.
Cover duplicate submission, telemetry outage, stagnant counters with live
heartbeat, late poll races, model unavailable/invalid diagnosis, controller
restart, timeout while remote compute survives, missing artifacts and secret
redaction. Live W&B verification requires user login; label offline vs live
receipts explicitly. No GPU experiment is started solely for verification.

R1 evidence: 8 focused monitoring tests pass (identity/restart, counter stagnation, diagnostic claims, telemetry outage, offline receipts, scope and secret checks).

R2/R3 evidence: 11 supervision tests + 8 monitoring tests pass; online synthetic W&B run wrote 3 metrics and Public API verified completed=3/state=finished. CLI/Discord/dashboard use the same local registry. Login handoff and sanitized correction delivered to Discord.

R4 evidence: real W&B online/offline probes, a live read-only diagnostic returning UNKNOWN on insufficient evidence, restart/download/validation integration test, dashboard DOM + screenshot. Added crash-before-wait recovery and stale-worker fencing after review of the controller restart boundary.

R5 evidence: 171/171 full tests passed in 54.155s with real Git Bash selected; compileall, diff check and pip check passed. Online/offline W&B receipts and a live diagnostic receipt saved. Listener replaced only after confirming zero active tasks; new PID 9464 reports listening and monitoring tables exist. No GPU workload or historical task-33 rerun was started.
