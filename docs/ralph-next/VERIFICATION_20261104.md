# Independent verification — 2026-11-04

Reviewed the work against [START_PROMPT.md](START_PROMPT.md), START_HERE.md,
tasks.json, the final verdict, current source, and local evidence. The original
assignment is preserved. This review did not run workers, contact Discord, start
cloud jobs, replace listeners, or change credentials.

## Result

The local software passes its tests. The broad live-readiness verdict needs the
qualifications below; the saved evidence does not establish every live acceptance
criterion in the original prompt.

| Verification performed now | Result |
| --- | --- |
| Baseline suite | 131 tests passed across 25 modules; the old verdict said 17 modules |
| Suite after fixes | 136 tests passed across 27 modules, 35.543 seconds |
| Clean export without personal config, `.env`, or runtime state | Same 136 tests passed, 38.441 seconds |
| `main.py --no-env doctor` | Exit 0 in working and clean exported trees |
| `scripts/setup_hooks.py --check` | Exit 0; hook works from repository and `.agents` cwd |
| `scripts/verify_verdict.py` | 44 evidence records match reviewed ledger hashes, dependency statuses and verdict table |
| `git diff --check` | Passed |

The export contained current source plus the newly added tests/scripts, with the
example configuration copied into place. It used the installed interpreter and
dependencies; it was not an independent package-install or Linux test.
Command outputs are retained locally in `storage/verification-20261104/`.

## Evidence checked and limits

- All 44 task records have valid review/check metadata, matching latest ledger
  status and SHA256. Seven table statuses were incorrectly `source_verified`
  instead of `reused_verified`: C03, D01, E01, F01, H01, H03 and H04. Corrected
  the table. Schema and digest checks establish consistency, not independent
  proof of the contents of every historical check.
- B02/B03 saved output files match their successful native-tool receipts and
  contain `sum=19`. The historical evidence lists `python receipt.json` as a
  passing command; that is not a valid executable verification recipe. The new
  auditor reads these receipts and actual output artifacts rather than replaying
  those command entries. Original records remain preserved.
- C05 receipts show session reuse and generation rotation for both providers.
  F03 metrics were independently recomputed from the supplied fallback batch;
  output matches, plan revision is 2 and both researcher runs share a session.
- D04/D05 durable-store manifest and file hashes, downloaded state, restored state,
  final resumed state and uninterrupted reference hashes match. Both phases have
  stopped-runtime observations and increasing ownership generations. However,
  phase 1 explicitly deletes its own ephemeral directory, and phase 2 does not
  record a boot ID. An absent directory plus a new fencing generation cannot
  prove a different physical VM. CPU checkpoint recovery is supported; distinct
  VM replacement and GPU training recovery remain unestablished by this probe.
- J03 model computation is supported: telemetry statistics were recomputed from
  all eight raw rows and every saved artifact receipt matches the output bytes.
  Intake IDs (`discord_msg_j03_q1`, `discord_msg_j03_t1`) and actor `user_ndlong`
  are synthetic, both replies have `delivered=0`, and no real Discord message ID
  or delivery acknowledgment is supplied. This supports simulated conversation
  intake plus real multi-role execution, not live user-origin Discord delivery.
  Probe #29 remains separate historical routing evidence; it was not repeated.
- I03 retains a successful build log, doctor output, container listing and
  dashboard data, consistent with the receipt. Docker was not rebuilt during this
  review. Neither these receipts nor `doctor` establish current service, login,
  quota, model binding, or GPU availability. Local doctor still reports default
  model selections; the exact models in the historical probes are not a promise
  about the current role configuration.

## Fixes, tests and cleanup

`atomic_json` previously overwrote the destination directly after ten failed
atomic replacements. A process interruption could then truncate durable role
state, and the fallback also changed file permissions. It now retries transient
locks, preserves the previous file and raises on persistent failure, and removes
the temporary file. A persistent Windows lock now surfaces as a failure instead
of a reported successful save; no state write is claimed in that case.

Five new regression tests cover transient/persistent replacement failures,
incomplete or stale reviews, same-size artifact corruption and path escape, and
invalid session resume/rotation. They use temporary fixtures and run without
local historical evidence. The separate read-only auditor needs ignored local
`storage/ralph-next/` evidence and fails if that evidence is missing.

Updated README/CLAUDE navigation and stale readiness text, marked the initial
blocker snapshot as historical, refreshed profile status and handoff manifest,
and removed redundant ignore rules. Removed generated bytecode caches from
source directories and the disposable verification export after its successful
run. Kept original prompts, failed attempts, runtime databases, checkpoint store,
logs, environments, archives and historical receipts.

Repeat the checks with the workflow interpreter:

```powershell
python -m unittest discover -s tests -v
python scripts/verify_verdict.py
python main.py --no-env doctor
python scripts/setup_hooks.py --check
git diff --check
```

Remaining evidence gaps are actual Discord transport/delivery for the model task
and independently identified replacement-VM recovery. They require appropriately
scoped live acceptance; they were not silently promoted to verified by this review.
