---
name: quota-router
description: Check Codex, AGY and Claude Code usage, explain or change quota routing, pin a provider, and handle quota waits. Use when a task is WAITING_PROVIDER, a provider hit its limit, or the user asks which model/provider will run a role.
---

# Quota router (opt-in)

The router tracks remaining usage per quota account (`codex`, `claude`,
`agy:claude`, `agy:gemini-3.8-flash`, `agy:gemini-4-argon`), keeps each limited
account's last 10% in reserve, and picks an equivalent model that still has quota. It
is off unless `quota_routing.enabled` is true in `config/workflow_config.json`.
Full policy: `docs/QUOTA_ROUTING.md`. Never read credential files to find usage.

## Check first

    python main.py quota status            # refreshes read-only probes, prints state per account
    python main.py quota status --json
    python main.py quota route operator    # what-if decision; add --task ID or --requires claude
    python main.py task ID                 # quota_routes: every QUOTA_ROUTE / QUOTA_EXHAUSTED event
    python main.py control list            # waits (dependency quota:<account>) and cooldowns

State meanings: `ok` usable; `reserved` at or below the reserve, not used for
ordinary work; `exhausted` limit hit or 100% until its reset. Confidence is
`measured` (Codex rollout rate_limits, Claude status line/stream), `inferred` (a
window that already reset, or a limit error) or `estimated` (stale data or ledger
budget). AGY has no numeric quota: it is failure-driven, so `unknown` there is normal.

## Reading a decision

`source` tells why: `class` (best equivalent with quota, preferring the window that
reset most recently), `requires` (reserve deliberately spent), `workaround`,
`reserve` (uncapped Gemini 4 Argon), `pinned` (explicit provider), `wait` (nothing
available until `until`). A wait is a durable `WAITING_PROVIDER` that consumes no
retry; it resumes by itself after the reset.

## Acting

- Explicit choice always wins: `!task --provider claude ...` or `enqueue --provider`
  is never rerouted. If that account is exhausted the task waits and says so; ask the
  user before suggesting they drop the pin.
- Work that only one account can do (for example Claude CLI on the Colab worker) is
  declared in `quota_routing.requires`, which may spend that account's reserve.
- Workarounds (for example Claude exhausted -> remote operator path) are configured
  under `quota_routing.workarounds`; do not invent one that is not configured.
- Light work (communicator, remote_manager, watchers, validator when the plan has
  `"simple": true`) goes to Gemini 3.8 Flash.
- Do not edit `config/workflow_config.json`, restart listeners or touch active tasks
  without being asked. `python main.py control cooldown PROVIDER --seconds N` is a
  manual cooldown, not a quota fix.
- Claude status-line capture is opt-in: `python scripts/quota.py install-claude-statusline --check`
  shows the change; only the user runs `--apply` (it backs up and chains any existing
  status line).

## When data is missing

`unknown` Claude usage means the status-line capture is not installed or no
interactive session has run recently. State that plainly instead of guessing; the
router then relies on limit errors and the ledger (`quota_ledger` table).
