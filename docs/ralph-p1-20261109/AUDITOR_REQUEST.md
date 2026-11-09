# Codex Sol quota investigator / P2 instrumentation worker

Read PLAN_VI.md. You are gpt-6.1-sol medium. Root coordinates; another worker owns
P1 implementation in worker snapshot. You own ONLY storage/ralph-p1-20261109/audit
and optional new scripts/tests in storage/ralph-p1-20261109/audit/tools. No root
production code edits and no worker snapshot edits. Do not spawn agents.

R0: Retrieve evidence of Claude/Codex quota exhaustion during latest SMILE campaign.
Use storage/handoffs/smile-workflow-20261109/ manifests and the existing independent
audit docs/reviews/2026-11-09-usage/ as pointers; inspect raw only within campaign
thread/session IDs or campaign-native receipts. No .env/auth/credentials. No model,
cloud, GPU or Discord calls. Preserve originals; exported artifacts never include
raw user chat, reasoning, credentials or large tool bodies. Numeric aggregates and
short safe error classification/source line refs are sufficient.

Find actual quota error events and timestamps, provider/model/session/run, role,
bounded task/phase summary and source evidence. Distinguish real quota exhaustion,
context limits, capacity/auth/sandbox errors and a user saying quota is back.
Match execution spans/checkpoints to event time; unknown attribution stays UNKNOWN.
Compute usage in bounded intervals/turns before incidents with unique response/msg
IDs and provider-specific schema. Never sum cumulative snapshots blindly. Do not
claim token/quota savings or that high token total proves the reason a cap was hit.
State whether model inference was ongoing versus a CLI only waiting on a child.

R3: Build a reusable local-only CLI/report for this investigation and P2 future
usage accounting. Integrate by reading existing core/quota.py/store/schema, not
rewriting router. Design/export per task/run/role/phase/provider/model: input/new/
cache-write/cache-read/output/reasoning, counter scope/epoch/cutoff, errors and
usage source; missing != zero. Add small meaningful fixture tests for dedup,
cumulative reset/conflict/cutoff and Claude/Codex schema differences. Exposed text
categories may be char counts/estimates with unknown residual; no fake exact shares.

Output QUOTA_INCIDENTS.json + CSV, concise QUOTA_FINDINGS_VI.md quoting the user's
question, P2_USAGE_PLAN.md with commands and acceptance, scripts/tests and actual
test receipts. Send R0_READY/R3_READY paths and concise conclusion to coordinator.
No unlimited scans of all accounts. All paths inside your owned output directory.
