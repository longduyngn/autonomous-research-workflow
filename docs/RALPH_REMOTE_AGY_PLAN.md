# AGY on Colab: bounded Ralph checkpoints

User request: log into AGY on Colab, exchange messages with a reusable local AGY
role, and use that pair to run/manage work. Preserve existing experiments.

1. Inspect live provider/runtime state and official Linux/remote OAuth support.
2. Install the official CLI on a CPU runtime. Authenticate there independently;
   never copy Windows credentials or expose OAuth codes in logs/Discord.
3. Add `remote_manager`, with isolated portable/native memory and a durable
   request ledger. Transport requests through the existing fenced Colab bridge;
   no public port, network SQLite, or second personal Colab runtime.
4. Run remote turns in a detached process, poll receipts without a model, and
   reconcile by request ID. A lost/ambiguous process is UNKNOWN, never replayed.
5. Verify two real exchanges, native session continuity on both hosts, duplicate
   handling and errors. Record what passed and what remains login-gated.

Official references:
- https://antigravity.google/docs/getting-started?tab=cli
- https://antigravity.google/docs/install?tab=cli
- https://github.com/google-antigravity/antigravity-cli

This adds an explicit opt-in management role. It does not automatically dispatch
existing research tasks to the experimental remote worker.

## Checkpoint status, 2026-11-07

- Source review, CPU installation (AGY 1.5.2), worker deployment, encrypted OAuth
  handoff, request ledger and reusable local role implemented.
- Local real AGY continuity verified: two consecutive successful receipts share
  native session `1b278d9d-8579-486d-a8dc-9591e6e1d3c5`; the second requests resume
  and recalls `CPU-AGY-LINK`. The initial file-read prompt path generated excessive
  model/tool activity; the text-only management role now receives its prompt inline.
- Colab browser closure/VM replacement was observed and explicitly reconnected;
  VM identity changed. No experiment/request was automatically replayed.
- Full regression: 185 tests passed in 86.710s using installed Git Bash. An earlier
  run had 184 passes and one environment failure because PATH selected the denied
  WSL launcher; the targeted Bash test passes with Git Bash. No existing test was
  weakened or skipped.
- Final focused tests after cancellation support: 16 passed. Seven synthetic
  Linux checks passed on actual CPU Colab: receipts, session reuse, artifact
  readback, portable history, deduplication, timeout retention, owner cancellation.
  These Linux checks use a fake AGY executable and make zero model calls.
- **Pending:** Colab Google OAuth (user chose in-chat instructions), then two real
  remote AGY exchanges, remote native continuity, and an independently checked
  CPU execution artifact. Latest account check is `LOGIN_REQUIRED`.
- No Discord login message was sent. No GPU or benchmark was started, and existing
  task 33 remains completed. No successful two-agent live exchange is claimed yet.

Evidence stays under `storage/remote-agy/verification/` and
`storage/remote-agy/colab-personal/logs/`. Operation guide: [REMOTE_AGY.md](REMOTE_AGY.md).

## Live acceptance completed, 2026-11-07

The user completed the OAuth handoff. Subsequent AGY processes authenticated
without another browser login. Three real exchanges used the same remote native
session. The first handshake succeeded; the second wrote the CPU script but its
terminal command was soft-denied despite native exit zero. The old worker wrongly
reported this as completed. Its original receipt is preserved, its current state
is corrected to `WAITING_APPROVAL`, and both the remote worker and generic AGY
adapter now reject denied actions/empty native responses as completion.

After independent source inspection, the third request granted only
`python3 artifacts/cpu_link_verification.py` through session-local settings.
It ran successfully. Host readback verified and recomputed the saved JSON:
`n=10000`, `sum_squares=333383335000`, `gpu_present=false`, and recalled label
`LINK-7C91-BETA`. No benchmark or GPU task was started.

Final checks: 191 full regression tests passed (80.866s), 20 focused remote-agent
tests passed, seven synthetic Linux checks passed, and the real acceptance probe
passed. Evidence: `storage/remote-agy/verification/live/receipt.json`.

Authentication alternatives researched in [REMOTE_AUTH.md](REMOTE_AUTH.md).
Claude's documented long-lived token is closest to an env-secret bootstrap;
Codex requires a separate durable cache stream, not cloning the active desktop
cache across simultaneous machines. No alternative provider credential was
exported and no subscription was silently switched to API billing.
