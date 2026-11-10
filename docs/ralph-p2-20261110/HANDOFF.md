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
- Done also: P2.1-03 (OpenCode adapter + `parse_opencode_stream`), P2.1-04 (session resume), P2.1-05 (API usage ledger), P2.1-06 (`_run_http` HTTP errors).
- P2.1 complete (P2.1-07 offline + live OpenRouter free smoke passed).
- Done also: P2.2 (researcher → planner with compatibility; D3 ordered planner chain).
- Done also: P2.6-01 (optional plan approval per task).
- Done also: P2.3 (pinned AGY roles + no-LLM answers).
- Done also: P2.4-01 (standard result contract), P2.4-02 (boundary failover + handoff), P2.4-03 (independent validator).
- Done also: P2.4-04 (CLI pins + throttled smoke). P2.4 complete.
- Done also: P2.6-02 (no default time budgets).
- Done also: P2.5-01 (job contract + journal), P2.5-02 (manifest/store), P2.5-03 (Drive live publish/restore PASS), P2.5-04/05 (Colab/Kaggle adapters, offline), P2.5-06 (model-free poller), P2.5-07 (light results home), P2.5-08 (cross-backend resume, offline).
- Done also: P2.5-09 (optional HF export). P2.5 complete offline.
- Done also: P2.6-03 (handoff schema + delegation policy + context benchmark). P2.6 complete.
- Next: P2.7-01 mini-DDPM example +
  offline e2e, P2.7-02 live acceptance (Kaggle/Colab/Discord; needs browser/accounts), P2.7-03 docs.
- The scheduled autopilot task is NOT installed (blocked by the session permission classifier); the user
  can install it with `python scripts/p2_autopilot.py install`.

## Known live gates (need the user)

- rclone + `gdrive:` configured by the user (P2.5-03 live PASS). Colab mount path of the shared folder (name under
  MyDrive) still to confirm for Colab interop.
- Kaggle secrets for rclone token (P2.5-03/P2.7-02 live).

## Open risks

- OpenCode isolation: always run with isolated XDG/HOME dirs and `OPENCODE_DISABLE_CLAUDE_CODE`,
  `OPENCODE_DISABLE_PROJECT_CONFIG`, `OPENCODE_DISABLE_DEFAULT_PLUGINS` so it does not pick up user/global
  config or Claude Code settings.
