# P2 handoff (living document — update every iteration)

## How P2 runs

- Interactive coordinator: Claude Code session. It runs `python scripts/p2_autopilot.py heartbeat`
  while working.
- Failover: Windows scheduled task `AntigravityP2Autopilot` runs `scripts/p2_autopilot.py tick` every
  10 minutes. When the heartbeat is older than 45 minutes it asks the quota router for the frontier route
  (Claude Opus → Codex gpt-6-astra → AGY claude-opus-5-5-high; reserve AGY gemini-4-argon-high), runs one
  bounded coordinator session, re-runs gate G and commits only on pass. Quota errors cool the account down
  and the next route is tried. State: `storage/p2/autopilot/` (ledger.jsonl, runs/, wait.json).
- A coordinator resuming by hand must first check `python scripts/p2_autopilot.py status`; if an autopilot
  run holds the lock, wait for it to finish before editing.
- Stop autopilot: `python scripts/p2_autopilot.py uninstall` (or `done`).

## Current position

- Done: P2.0-01, P2.1-01 (contract in `config/provider_versions.json`, fixtures in `tests/fixtures/opencode/1.19.5/`),
  P2.1-02 (`core/opencode_config.py`), P2.1-02b (`core/openrouter_free.py`).
- Next: P2.1-03 adapter in `core/providers.py` opencode branch: close stdin, pass `--dir`, isolated env from
  `opencode_config.isolated_env` when `api_profile` is set, auto_free model via `FreeModelSelector`
  (state `storage/openrouter`), free_only check, parse tool_use/step_finish(tokens,cost)/error(statusCode,
  retry-after) into the standard result; 429 → cool down that free model and report quota.
- The scheduled autopilot task is NOT installed (blocked by the session permission classifier); the user
  can install it with `python scripts/p2_autopilot.py install`.

## Known live gates (need the user)

- rclone not installed / remote `gdrive` not configured (needed before P2.5-03 live). Folder ID recorded in
  USER_DECISIONS_VI.md.
- Kaggle secrets for rclone token (P2.5-03/P2.7-02 live).

## Open risks

- OpenCode isolation: always run with isolated XDG/HOME dirs and `OPENCODE_DISABLE_CLAUDE_CODE`,
  `OPENCODE_DISABLE_PROJECT_CONFIG`, `OPENCODE_DISABLE_DEFAULT_PLUGINS` so it does not pick up user/global
  config or Claude Code settings.
