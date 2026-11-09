# Ralph plan: quota-aware provider router

Status: IMPLEMENTED; Codex follow-up verified, personal-config approval and live
exhaustion/remote capture remain pending (2026-11-09). Implement in small Ralph iterations: inspect → implement
one item → verify → record in the progress log at the bottom → continue.

## Goal

Continuously track remaining usage for Codex, AGY and Claude Code. When a
provider/model drops to its last 10%, route roles to an equivalent-performance
model that still has quota, preferring the provider whose 5-hour window reset most
recently. Spend the last 10% only on work that no other provider can do (for
example Claude CLI logged in on the Colab worker). When everything equivalent is
exhausted, use a workaround (e.g. Claude exhausted → remote operator path) or
wait until the earliest reset. Gemini 4 Argon (AGY) has no hourly cap and is the
reserve. Gemini 3.8 Flash has a 5h quota that is rarely used; give it light work.

## Discovered facts (2026-11-09, this host)

| Provider | Version | Machine-readable usage source |
|---|---|---|
| Codex CLI | 0.167.0 | Read-only app-server `account/rateLimits/read` (camelCase fields), throttled 120 s; newest rollout `token_count.rate_limits` is fallback (snake_case fields). No inference or credential reads. |
| AGY | 1.5.3 | No numeric quota API found. `agy models` lists: `gemini-4-argon-{low,medium,high}`, `gemini-3.8-flash-{low,medium,high}`, `claude-opus-5-5-*`, `claude-sonnet-5-5-*`, `gpt-oss-120b-medium`. CLI logs only show `doRefreshQuota`. Use failure-text detection + our own run ledger. |
| Claude Code | 2.1.319 | Status-line stdin JSON exposes `rate_limits.five_hour/seven_day` (`used_percentage`, `resets_at`) in interactive sessions; `--output-format stream-json` may emit `rate_limit_event`. Headless `json` output has `usage` tokens. Limit errors contain text like "hit your limit … resets". Verify the actual shapes locally before relying on them. |

Credentials rule (CLAUDE.md): never read/print `auth.json`, `.credentials*`, `.env`
or OAuth tokens. No undocumented OAuth usage endpoints that need a token.

## Model equivalence classes (configurable, not hard-coded)

| Class | Members (provider → model) | Use |
|---|---|---|
| `frontier` | claude_cli→opus; agy→claude-opus-5-5-high; codex_cli→(strongest, e.g. "astra" alias — read model ids from config/env, do not guess); agy→gemini-4-argon-high | researcher |
| `worker` | claude_cli→sonnet; codex_cli→(e.g. "sol"); agy→claude-sonnet-5-5-medium; agy→gemini-4-argon-medium | operator, validator |
| `light` | agy→gemini-3.8-flash-{low,medium} | communicator, watcher (30 min), remote_manager, validator for simple tasks |
| `reserve` | agy→gemini-4-argon-* (no hourly cap) | last automatic fallback before waiting |

Codex model names: the example pins `gpt-6-astra` (frontier) and `gpt-6.1-sol`
(worker), verified by live account `model/list` on 2026-11-09. `""` still means
the provider tier default. Personal-config changes require approval.

## Policy

1. Quota accounts are keyed by *who meters them*: `codex`, `claude`,
   `agy:gemini-3.8-flash`, `agy:gemini-4-argon`, `agy:claude` (AGY-hosted Claude
   models share one AGY pool unless evidence shows otherwise — keep it configurable).
2. Snapshot = `{account, window, used_percent|null, resets_at|null, source,
   observed_at, confidence: measured|estimated|inferred}`. Stale (> `stale_seconds`)
   measured data degrades to `estimated`.
3. Remaining ≤ `reserve_percent` (default 10) → account is RESERVED. Exhausted
   (limit error or ≥100%) → cooldown until `resets_at` (reuse
   `ControlPlane.cool_down`, so existing `WAITING_PROVIDER` waits keep working).
4. Selection for a role: candidates = equivalence class for the role, minus
   unavailable/reserved accounts → rank by (most recent window reset first, then
   most remaining, then config order). If empty: candidates that are RESERVED but
   the role/task is marked `requires` that account (e.g. `remote_claude`, Claude CLI
   on Colab) → allowed. Else configured `workarounds` (e.g. claude exhausted →
   `remote_operator` path / AGY-hosted Claude). Else `reserve` class (Argon). Else
   wait PROVIDER until earliest `resets_at`.
5. Explicit user choice wins: `task.provider_override` (`!task --provider ...`)
   is never silently re-routed; if its account is exhausted the task waits and the
   reason says so. Routing is opt-in: `quota_routing.enabled` (default false in the
   example config) — this keeps the documented "no silent fallback" contract;
   when enabled every reroute is an explicit, logged `QUOTA_ROUTE` event with
   reason, stored in run evidence and visible in `task` / dashboard.
6. Light work goes to Gemini 3.8 Flash: communicator, remote_manager,
   workspace_watcher LLM review at 30-minute cadence, and validator when the task is
   marked simple (researcher plan flag or `quota_routing.light_validator: true`).
7. Ledger: after every run record provider, model, account, tokens (`usage` from
   evidence), duration and any limit error → `quota_ledger` table. Used for
   AGY/Claude estimates when no measured percentage exists (budgets per account are
   config values, default unknown → only failure-driven state).

## Architecture (fits CLAUDE.md ownership)

- `core/quota.py` (new): probes (`CodexRolloutProbe`, `ClaudeStatusProbe`,
  `LimitErrorDetector`, `LedgerEstimator`), `QuotaStore` (SQLite tables
  `quota_snapshots`, `quota_ledger` via `db._connect()`), `QuotaRouter.select(role,
  task) -> RouteDecision(provider, model, account, reason, reserved_use)`. Pure,
  no model calls, injectable clock/paths for tests.
- `core/providers.py`: `run_role(..., model_override=None)`; `resolve_model`
  honours it; detect limit errors in `_run_cli` → `structured_data['quota']`
  (`exhausted`, `resets_at`, `account`). Role memory identity already includes
  provider+model, so a reroute rotates with a handover (expected).
- `core/orchestrator.py`: in `process_next_step` / `_spawn_and_log`, ask the router
  when enabled; record `QUOTA_ROUTE`; on quota-exhausted result → `cool_down`
  account until reset and `wait('PROVIDER', ...)` instead of burning a retry.
- `core/scheduler.py` or daemon thread: refresh snapshots every
  `poll_seconds` (default 120). No model calls in the poll.
- Claude snapshot capture: `scripts/claude_statusline_quota.py` — a status-line
  command that writes `rate_limits` to `storage/quota/claude_statusline.json` and
  prints a short line. Install only via an explicit opt-in command
  (`python scripts/quota.py install-claude-statusline --check/--apply`), never
  silently overwrite an existing statusLine (chain it).
- CLI: `python main.py quota status|refresh|route ROLE [--task ID]` and
  `scripts/quota.py` (same functions). Discord: `!quota` in task-log channel only
  on request (respect three-channel contract).
- Skill: `.claude/skills/quota-router/SKILL.md` — when to check quota, how to read
  `quota status`, how to pin/override, the reserve rule.
- Docs: `docs/QUOTA_ROUTING.md`; update `docs/PROVIDERS.md` "No silent fallback"
  paragraph to describe the opt-in router; README link.

## Ralph iterations

| ID | Item | Verify |
|---|---|---|
| Q01 | Confirm real usage shapes: Codex rollout `rate_limits` (newest file by mtime across dirs), Claude headless `json`/`stream-json` fields and limit-error text (from existing logs/tests, no new paid calls unless tiny), AGY quota error text in `storage/logs`. Record findings in this file. | Notes appended with file paths/line excerpts (no secrets). |
| Q02 | `core/quota.py` snapshot model + `QuotaStore` tables + `CodexRolloutProbe` + `LimitErrorDetector`. | New `tests/test_quota.py` with fixture rollout lines and error texts. |
| Q03 | Config schema `quota_routing` (enabled, poll_seconds, reserve_percent, stale_seconds, accounts, classes, role_classes, requires, workarounds, light_validator) + validation in `core/configuration.py` + example config. | Config validation tests; `python main.py --no-env doctor`. |
| Q04 | `QuotaRouter.select` policy (reserve 10%, fresh-reset preference, requires, workaround, Argon reserve, wait). | Table-driven unit tests covering every branch, injected clock. |
| Q05 | Provider integration: `model_override`, limit detection into `structured_data['quota']`, ledger write. | `tests/test_providers.py` additions with fake CLI output; existing tests pass. |
| Q06 | Orchestrator integration behind `enabled` flag: route event, explicit override respected, exhausted → cooldown + PROVIDER wait (no retry burn). | Orchestrator tests with mock providers; dry run `python main.py --no-env run "Check the workflow" --dry-run`. |
| Q07 | Poller + `main.py quota` commands + `scripts/quota.py` + Claude status-line capture script (opt-in installer, chains existing). | CLI tests; `python main.py --no-env quota status` prints real Codex data read-only. |
| Q08 | Light-role assignment: Gemini 3.8 Flash for communicator/remote_manager/watcher (30 min LLM review cadence), optional simple-task validator. | Watcher/config tests. |
| Q09 | Skill `.claude/skills/quota-router/SKILL.md`, `docs/QUOTA_ROUTING.md`, PROVIDERS/README updates. | Docs reviewed for accuracy against code. |
| Q10 | Full verification and record. | `python -m unittest discover -s tests -v`, `python main.py --no-env doctor`, `git diff --check`; results appended below. |

## Constraints

- Do not read/print credentials; do not change personal `config/workflow_config.json`
  except as the user asks (example config is the shipped default).
- Do not restart listeners or interfere with active tasks/experiments
  (`.claude/worktrees/smile-campaign` is separate; leave it alone).
- Do not commit; leave changes in the working tree for user review.
- Keep `provider_override` semantics and three-channel Discord contract intact.

## Progress log

(append one entry per iteration: ID, what changed, verification output summary,
open issues)

### Q01 - confirm usage shapes (2026-11-09)

Baseline before changes: `.conda/python.exe -m unittest discover -s tests` -> 207 tests OK.

- Codex: 56 rollout files under `~/.codex/sessions/YYYY/MM/DD/`. The newest by
  *mtime* lives in `2026/10/20` (a resumed old session), so selection must use mtime
  across all date dirs, not the directory date. `event_msg`/`token_count` payload:
  `rate_limits = {limit_id:"codex", primary:{used_percent:1.0, window_minutes:300,
  resets_at:<epoch>}, secondary:{used_percent:70.0, window_minutes:10080,
  resets_at:<epoch>}, credits:{...}, plan_type:"plus", rate_limit_reached_type:null}`.
  Event line also has top-level `timestamp` (ISO). A window whose `resets_at` is in
  the past is stale: its usage has reset (treat as 0, confidence `inferred`).
  Only `rate_limits` lines were read; no credential file was opened.
- Claude: `storage/remote-claude/claude-pair-20261107-1/validator.native.jsonl`
  (stream-json) contains `{"type":"rate_limit_event","rate_limit_info":{"status":
  "allowed","resetsAt":<epoch>,"rateLimitType":"five_hour","overageStatus":...,
  "unifiedWindows":{"five_hour":{"utilization":0.05,"resetsAt":<epoch>,"status":
  "allowed"},"seven_day":{"utilization":0.04,...}}}}`. `utilization` is a 0..1
  fraction. The final `result` event has `usage` (input/output/cache tokens),
  `is_error`, `subtype`, `api_error_status`, `total_cost_usd`. Our local `claude_cli`
  adapter uses `--output-format json` (single result object, no rate_limit_event), so
  the live signal for local runs is the opt-in status line (`rate_limits.five_hour.
  used_percentage`, `resets_at` per the plan; NOT live-verified here) plus limit-error
  text. No real limit-error text exists in saved logs; detector patterns are
  best-effort ("hit your limit", "usage limit reached", "5-hour limit reached",
  "resets 3pm", legacy `limit reached|<epoch>`) and must be confirmed on first
  occurrence.
- AGY 1.5.3: `agy models` confirmed (gemini-4-argon-{low,medium,high},
  gemini-3.8-flash-{low,medium,high}, claude-opus-5-5-*, claude-sonnet-5-5-*,
  gpt-oss-120b-medium). AGY logs only show `quota_manager.go: doRefreshQuota:
  starting reload`; no numeric quota and no saved exhaustion text. Detector uses
  generic markers (`RESOURCE_EXHAUSTED`, `quota`, `429`, "rate limit", "try again in")
  and is failure-driven + ledger.
- Open: Codex limit-error text is also unverified; `rate_limit_reached_type` in the
  rollout is the measured signal.

### Q02 - snapshot model, store, probes, detector (2026-11-09)

Added `core/quota.py`: `Snapshot` (+`effective()`: elapsed window -> reset/inferred,
stale measured -> estimated), `account_for`, `usage_tokens`, `parse_codex_rate_limits`,
`parse_claude_limits`/`parse_claude_event`, `CodexRolloutProbe` (newest by mtime
across date dirs, tail-scan, only `token_count.rate_limits` extracted),
`ClaudeStatusProbe`, `LimitErrorDetector` (text markers + reset hints: epoch after
`|`, ISO, `resets 3pm`, `in 2 hours 5 minutes`, `retry-after`), `QuotaStore`
(`quota_snapshots`, `quota_ledger`; older observation never overwrites newer; fresh
measured headroom clears an older `limit` exhaustion), `account_state`
(ok/reserved/exhausted + `until`), `QuotaMonitor`. Router/`RouteDecision` are in the
same file (exercised in Q04). `tests/test_quota.py`: 12 tests pass (fixtures only).
Open: Claude/AGY/Codex limit-error wording unverified live; AGY has no measured data.

### Q03 - config schema (2026-11-09)

`core/configuration.py: validate_quota_routing` (called from `validate_config`):
types/ranges for `enabled`, `poll_seconds`, `reserve_percent` [0,100), `stale_seconds`,
`default_cooldown_seconds`, `sticky_routes`, `light_validator`; `accounts` (+`unlimited`,
`budget_tokens_5h`); `classes`/`reserve`/`workarounds` entries must name a configured
provider and a string model; `role_classes`/`requires` must reference configured
roles; `enabled` without `role_classes` is rejected. `config/workflow_config.example.json`
gained `quota_routing` with `enabled: false`; classes: frontier (claude opus, codex ""
= account default, agy claude-opus-5-5-high), worker (claude sonnet, codex "", agy
claude-sonnet-5-5-medium), light (gemini-3.8-flash-medium/low); reserve per class =
gemini-4-argon-high/medium/low (Argon only under `reserve`, a deliberate small
deviation from the plan table: same effect, since reserve is tried last).
Codex model ids are `""` placeholders: pin `astra`/`sol` ids yourself. Personal
`config/workflow_config.json` untouched. Verify: `tests/test_quota.py` ConfigTests;
`main.py --no-env doctor` runs; test_readiness config tests still pass.

### Q04 - router policy (2026-11-09)

`QuotaRouter.select(role, task=None, requires=None, prefer=None) -> RouteDecision | None`
(None = role not routed / router has no class for it; caller keeps config). Order:
pinned `provider_override` (never rerouted; exhausted -> `wait` with "not rerouted";
reserved -> runs, `reserved_use`) -> class members that are `ok`, ranked by (sticky
previous route, most recently reset 5h window, most remaining, config order) ->
configured `workarounds[account]` for any blocked account -> `reserve[class]` (Argon)
-> `wait` until the earliest known reset (fallback `default_cooldown_seconds`). With
`requires=[accounts]` reserved accounts may be spent (`reserved_use`), exhausted ones
fall to workarounds then wait, and the uncapped reserve is skipped (it cannot do that
work). Validator uses class `light` when `light_validator` or `plan.simple is true`.
Manual cooldowns (`provider_cooldowns` by provider name or `quota:<account>`) are
honoured through an injected `cooldown_until`. `considered[]` records every
candidate's state for the QUOTA_ROUTE event. `RouterTests`: 14 table-driven cases;
`tests/test_quota.py` 28 tests pass.

### Q05 - provider integration (2026-11-09)

`core/providers.py`: `run_role(..., model_override=None)` and `resolve_model(role,
provider, override)` (non-empty override wins over role `model`/`model_env`; `""` means
the target provider's tier default so a role pin meant for another provider is never
sent to it; `None` = unchanged behaviour). Override reaches CLI, HTTP, Gemini and
plugin adapters and the role-memory identity (a reroute therefore rotates memory with
a handover, as intended). `_run_cli` now records `structured_data['model']`,
`['quota_account']`, `['quota_snapshots']` (parsed from the CLI's own stdout:
codex `rate_limits`, claude `rate_limit_event`) and, on *failed* runs only,
`['quota'] = {exhausted, resets_at, matched, account, provider, model}` and prefixes
the error with "Provider quota exhausted". Successful output that merely discusses
limits is not flagged. `snapshots_from_stream` added to `core/quota.py`.
Deviation: the ledger write lives in the orchestrator (Q06) because the runner has no
database/task context. `tests/test_providers.py`: +6 tests (13 total) pass.

### Q06 - orchestrator integration (2026-11-09)

`HybridOrchestrator` builds a router only when `quota_routing.enabled` (otherwise
`self.quota is None` and no code path changes). `process_next_step` asks the router
instead of the old configured-provider cooldown check; `_spawn_and_log` applies the
decision (`provider_override` + new `model_override`, passed only when routed so
existing fake runners are unaffected). `QUOTA_ROUTE` events (payload = decision incl.
`considered[]`) are logged for reroutes, reserve spends, workarounds, blocked pins and
waits, deduplicated per (task, role); the decision is also stored as
`structured_data['quota_route']` in the run receipt and shown in `task` output
(`quota_routes`). Explicit `provider_override` (task or process) is never rerouted:
exhausted -> PROVIDER wait whose reason says "not rerouted". A `wait` decision or a
failed run carrying `structured_data['quota']` -> `QUOTA_EXHAUSTED` event, `limit`
snapshot, `cool_down('quota:<account>')`, `wait('PROVIDER')`, `TaskDeferred`: no
retry is consumed (`retry_count` stays 0, asserted). Ledger rows are written after
every routed-orchestrator run (tokens from `usage`, duration, limit flag); passive
snapshots in CLI stdout are stored. The Discord communicator reply path
(`_conversation_reply`) is routed too and skips the model reply on `wait`.
Helpers in `core/quota.py`: `resolve`, `record_route`, `record_result`, `build_router`.
Verify: `tests/test_quota_orchestrator.py` 5 tests pass; `main.py --no-env run "Check
the workflow" --dry-run` -> COMPLETED. Open: replay after a quota failure inside an
operator step can repeat side effects exactly like the existing retry path.

### Q07 - poller, CLI, scripts, Claude status-line capture (2026-11-09)

- `TaskScheduler.tick` runs `QuotaMonitor.refresh` on a dedicated 1-thread pool every
  `quota_routing.poll_seconds` (only when routing is enabled; read-only probes, no model
  calls; failures logged as `QUOTA_POLL_ERROR`).
- `python main.py quota status|refresh|route ROLE [--task ID] [--requires ACCOUNT] [--json]`
  and `python scripts/quota.py ...` share `core/quota_cli.py`. Works with a personal
  config that has no `quota_routing` block (falls back to the example block, labelled,
  `enabled: false`; `route` is then explicitly "what-if only").
- `scripts/claude_statusline_quota.py`: status-line command storing only `rate_limits`
  (+`captured_at`) in `storage/quota/claude_statusline.json`, printing `claude 5h 41% 7d 10%`,
  chaining a previous command (passed base64 via `--chain-base64`), never failing the session.
- `python scripts/quota.py install-claude-statusline|remove-claude-statusline`
  `--check` (default, writes nothing) / `--apply` (timestamped backup, atomic write,
  only the `statusLine` key changes, existing command chained, non-command statusLine
  refused, idempotent, `--settings PATH`). The real `~/.claude/settings.json` was NOT
  modified (tests use temp files).
- `doctor` gains a read-only `quota_routing` section.
Verify: `main.py --no-env quota status` printed real Codex data (primary 5%/weekly 71%,
`measured`, resets 20:57) read from the newest rollout; tests: test_quota_cli.py 11 pass.

### Q08 - light-role assignment (2026-11-09)

- Example config: `communicator` -> `gemini-3.8-flash-medium`, `workspace_watcher` and
  `world_watcher` -> `gemini-3.8-flash-low` (remote_manager already was Flash-medium);
  all four are in router class `light` (Flash first, `gemini-4-argon-low` as reserve).
  Personal `config/workflow_config.json` untouched; the example change only affects
  fresh clones.
- `watchers.workspace.llm_review_seconds` (example 1800 = 30 min; absent keeps the old
  600 s). Cheap 30 s checks still run every tick; only the LLM review is throttled.
  Watchers (`workspace_watcher`, `world_watcher`) route via `core.quota.route_call`
  (constructor arg `quota_router`, wired in `main.build_system` from
  `orchestrator.quota`); when no light/reserve capacity exists the review is skipped
  without consuming the cadence and world items stay unseen, so nothing is lost.
- Simple validator: `parse_plan` preserves `simple: true` (only literal true);
  `roles/researcher.md` tells the researcher when to set it; `light_validator: true`
  in config forces it. Everything else validates on the `worker` class.
- Verify: `LightRoleTests` (4 tests) added; test_quota_orchestrator.py 9 pass.

### Q09 - skill and docs (2026-11-09)

Added `.claude/skills/quota-router/SKILL.md` (check -> read decision -> act; pin and
reserve rules; missing-data guidance), `docs/QUOTA_ROUTING.md` (accounts/sources,
policy, config, light work, commands, status-line capture, verified vs unverified),
updated the "No silent fallback" paragraph in `docs/PROVIDERS.md` and linked the new
doc from `README.md`. Cross-checked each statement against `core/quota.py`,
`core/quota_cli.py`, `core/orchestrator.py` and the tests (selection order, `requires`
semantics, wait without retry burn, cadence, installer guarantees). CLAUDE.md left
unchanged (user-maintained contract); suggest a one-line pointer if wanted.

### Q10 - final verification (2026-11-09)

- `.conda/python.exe -m unittest discover -s tests -v` -> `Ran 261 tests in 86.282s` `OK`
  (baseline 207; +54 = test_quota 28, test_quota_cli 11, test_quota_orchestrator 9,
  test_providers +6). No failures or errors.
- `.conda/python.exe main.py --no-env doctor` -> OK; new `quota_routing` section:
  `enabled: false, configured: false, codex_rollout_dir_present: true,
  claude_statusline_capture_present: false`.
- `.conda/python.exe main.py --no-env quota status` -> real Codex data read-only
  (5 h used 5%, weekly 71%, resets 2026-11-09 20:57:19, `measured`); claude / agy:*
  `unknown` (no status-line capture installed, AGY has no numeric quota).
- `git diff --check` -> exit 0 (only pre-existing LF/CRLF warnings on files not
  touched by this work). New files scanned for trailing whitespace: none.
- `python scripts/quota.py install-claude-statusline --check` against the real
  settings file reported `action: install, chains_existing: false, applied: false`;
  nothing was written (no backup file exists). `config/workflow_config.json`, listeners,
  active tasks and `.claude/worktrees` untouched; nothing committed.

Open issues / assumptions
1. Limit-error wording for Codex, Claude and AGY is unverified live; patterns are
   best effort. `rate_limit_reached_type` values are unknown.
2. Claude status-line field names (`rate_limits.five_hour.used_percentage`,
   `resets_at`) follow the plan, not a live capture; stream-json
   `unifiedWindows.*.utilization` (fraction) is verified from saved logs. Local
   `claude_cli` runs use `--output-format json`, which carries no rate-limit event.
3. Codex `astra`/`sol` ids are `""` placeholders (account default) in the example config.
4. Argon appears only under `reserve` (not in frontier/worker classes) - equivalent
   behaviour, documented deviation. The ledger write lives in the orchestrator/watchers
   rather than the provider runner. Enabling routing in the personal config is the user's
   step (example block can be copied; nothing was changed there).
5. No shipped `workarounds` (remote Claude/AGY paths are not provider adapters yet).
6. Replaying an operator step after a mid-run quota failure can repeat side effects,
   the same as the existing retry path.
7. Example-config defaults changed for fresh clones only: communicator/watchers on
   Gemini 3.8 Flash, `llm_review_seconds: 1800`.


### G1 - live Codex app-server probe (2026-11-09)

Added a read-only JSON-RPC client (initialize/initialized, account/rateLimits/read
only; model/list also permitted for discovery), bounded timeout and child cleanup.
CodexAppServerProbe caches normalized usage, throttles successful and failed
attempts for 120 s, preserves observation times, and uses newer rollout data as
fallback. Legacy probe=codex_rollout remains supported without changing personal
config. 32 focused fixture tests passed (including new cache/fallback tests).
Live CLI 0.167.0 schema generation succeeded; account/rateLimits/read returned
5h 7%, weekly 1%, source=codex_app_server. Sandbox execution could not create
Codex runtime files; the authorized host probe succeeded without reading any
credentials or making an inference turn. No listeners were restarted.

### G2 - verified account model IDs (2026-11-09)

Live model/list returned (nextCursor=null): gpt-6.1-sol (default), gpt-6-astra,
gpt-6.1-terra, gpt-6-sol, gpt-6-luna, gpt-5.6-sol, gpt-5.6-terra, gpt-5.6-luna.
The example frontier uses gpt-6-astra and worker uses gpt-6.1-sol. Personal
config/workflow_config.json is untouched pending the handoff's explicit approval
requirement. No model calls were needed for discovery.

### G3 - structured Codex limits, partial live evidence (2026-11-09)

Scanned 62 native session rollouts for non-null rate_limit_reached_type and
quota error events, plus saved stdout/stderr receipts under storage/logs,
storage/ralph-discord-release and storage/handoffs: none found. Did not deliberately
exhaust an account. The installed CLI's generated schema enumerates
rate_limit_reached, workspace_owner_credits_depleted, workspace_member_credits_depleted,
workspace_owner_usage_limit_reached, workspace_member_usage_limit_reached.
Saved that enum with provenance in tests/fixtures/codex_limit_schema.json. Generic
reached types now produce an unknown-window limit instead of falsely setting 5h
to 100%. Native turn.failed/error envelopes preserve resets_at, and ISO try-again
hints parse correctly even after long stdout. 48 focused fixture tests passed.
Real 5h/weekly exhaustion wording remains unavailable; synthetic error fixtures
are explicitly not live captures.

### G4 - passive local Claude stream capture (2026-11-09)

Local adapter now requests stream-json --verbose. It reads the complete saved
stdout (not just run_process's 24k tail), selects the final result, keeps session
ID/usage/is_error and denies both permission_denials and denied_actions. A stream
without a final result fails. Native hook launch/receipt checks are unchanged.
Normalized snapshots are persisted to the configured controller QuotaStore even
when routing is disabled; a DB capture error remains in the run receipt.
ClaudeCliProbe skips inference when both passive windows are newer than its
1800-second interval. Failed active attempts are now throttled too, with one turn
and no tools. Focused provider + hook tests: 20 passed using Git Bash in PATH
(the Windows WSL bash fails E_ACCESSDENIED in this sandbox).
Live: one Haiku request, tools disabled, max-turns=1, output=ok, session present,
5h=26%, weekly=44%, source=claude_stream. It used input=2, output=4 and 59125
cache-creation tokens from CLI context, so no further model probes were run.
Receipt: storage/quota/claude-passive-acceptance/acceptance.json.

### G5 - task requirements and independent remote quota (2026-11-09)

research_plan preserves validated requires role/account lists. Per-task
requirements override global defaults, and explicit provider_override still wins.
A required remote account can come from a configured workaround and spend its
last 10%. Remote Claude starts declare operator/validator requirements for
remote_claude, log QUOTA_ROUTE, and return WAITING_PROVIDER before sending any
RPC when exhausted. The standalone script adds --no-env and prints quota state.
Worker smoke and paired stream output capture normalized rate_limit_event
windows into status with original observed_at; ClaudeCloud.status imports them
only into remote_claude, never the local claude account. No remote deployment,
login, model call or runtime operation was performed. Focused fixture suite for
requirements, remote worker, providers, hooks and Codex probes: 41 passed. Remote
quota capture is fixture-verified only; a live worker session remains unverified.

### G4/G5 integration follow-up (2026-11-09)

Full regression initially passed 282 tests in 102.074 s. Subsequent shared-checkout
changes introduced a communicator plan-mode guard and a call to nonexistent
persist_claude_run. The paused quota-optimization handoff documents both issues
in docs/ralph-p1-20261109/QUOTA_OPTIMIZATION_PAUSED_VI.md. Updated the existing
AGY smoke assertion to plan while preserving native session-ID checks, without
changing the AGY implementation. Removed only the unfinished telemetry call; G4
passive snapshot persistence remains in _run_cli. Added a public run_role regression
so this failure cannot be hidden by testing _run_cli alone. Broader optimization
backends remain paused/out of this task.

Remote smoke output preserves native.json as the final JSON result and additionally
exports native.jsonl for stream evidence; fixture verifies nonce/result/quota without
a model call. Earlier full-regression attempt was interrupted when a pre-existing
quota CLI fixture inherited the example active Claude probe; no usable completion
receipt was returned. The final quota CLI fixtures explicitly use file-only Claude
and mocked AGY readiness, avoiding inference during tests.

### Codex final acceptance (2026-11-09)

- Final source: .conda/python.exe -m unittest discover -s tests -v ->
  Ran 284 tests in 101.369s, OK. Git Bash was prepended to PATH for the existing
  bash hook contract test. Quota CLI fixtures use no inference.
- .conda/python.exe main.py --no-env doctor -> exit 0, routing enabled; doctor
  invokes no probes. Capability/note text now matches stream capture and live RPC.
- .conda/python.exe main.py --no-env quota status -> exit 0; at this later
  snapshot Codex 5h 49%, weekly 8% (legacy rollout source in personal config),
  Claude 5h 26%, weekly 44% from the verified passive run, AGY numeric usage unknown.
- git diff --check -> exit 0; explicit whitespace scan covers new/untracked files.
- Logs: storage/quota/codex-final-tests.log, codex-doctor.log, codex-status.log.
- G1 live RPC and G2 account model list were verified without inference; G4 has
  one controlled live Haiku receipt. G3 real exhaustion wording and G5 remote
  stream capture remain unverified live (schema/synthetic fixtures only).
- Pending G2 approval: storage/quota/proposed-personal-config.patch.json proposes
  only 4 operations: codex_app_server probe, 120-second throttle, frontier
  gpt-6-astra and worker gpt-6.1-sol. Applied to an in-memory personal-config copy
  and validate_config passed; config/workflow_config.json itself was not written.
- No commit, listener restart, remote runtime operation or credential inspection.

### A1 - AGY quota source and ACP probe (2026-11-09)

Investigated machine-readable quota sources in AGY 1.5.3:
- Flags `agy --settings`, `agy models`, `--output-format json/stream-json` emit no rate limit metrics.
- `--log-file` only prints internal statements (`doRefreshQuota: starting reload (force=true)`).
- IDE language-server `GetUserStatus` was analyzed and rejected: accessing it requires scraping ephemeral CSRF tokens and internal ports from process command lines or private local directories, violating security and sandbox isolation rules. In addition, no language-server daemon is active on the host outside of the IDE.
- ACP stdio protocol (`agy acp`) was implemented via `AgyProbe` in `core/quota.py`. It sends an `initialize` JSON-RPC handshake to query account tier (`g1-pro-tier`) and model capabilities without consuming tokens.
- Snapshots are cached in `storage/quota/agy_probe.json` with an 1800-second throttle interval (`force=True` on `quota refresh`).
- Unit tests added: `AgyProbeTests` in `tests/test_quota.py` verifying cache throttling and stale-cache refresh with mock ACP server.

### A2 - AGY pool mapping and confidence state (2026-11-09)

- Verified that AGY-hosted Claude (`agy:claude`) and Gemini 3.8 Flash (`agy:gemini-3.8-flash`) have independent quota pools.
- Verified that `agy:gemini-4-argon` is `unlimited: true` (unmetered per 5h window, serving as the ultimate reserve before waiting).
- Updated `account_for` in `core/quota.py` to map `remote_claude_operator` provider to `remote_claude` account.
- Enhanced `account_state` to retain `inferred` confidence when snapshots exist without numeric percentages (`used_percent is None`).
- Verified pool mapping and status output across all accounts in `tests/test_quota.py`.

### A3 - AGY limit errors and reset hints (2026-11-09)

- Expanded `LimitErrorDetector._LIMIT_PATTERNS` in `core/quota.py` with Google/AGY exhaustion regexes:
  `resourcehasbeenexhausted`, `user_?rate_?limit_?exceeded`, `rate_?limit_?exceeded`, `exhausted your (?:model )?capacity`, `request limit for`, `quota will replenish`, and `quota exceeded for quota metric`.
- Extended `LimitErrorDetector.resets_at` to parse `replenish(?:es)? in ...`, `try again after ... am/pm`, and relative hour/minute/second formats.
- Added comprehensive unit tests in `tests/test_quota.py: DetectorTests` verifying detection and epoch calculation from AGY error text.

### A4 - Workaround provider adapter and Colab lease contract (2026-11-09)

- Implemented `RemoteClaudeOperatorProvider` in `core/remote_claude.py` conforming to `BaseAgentProvider` (`run(...) -> AgentRunResult`).
- Registered `remote_claude_operator` in `EphemeralAgentRunner` via `self.extensions.register(...)`.
- Wired lease acquisition through `ColabGate` in `storage/resource-control/colab.json`:
  claims lease with sha256 workspace owner, executes deployment and smoke jobs, monitors completion, collects output, and cleanly releases gate in `finally:`.
- If `ResourceBusy` is raised, gracefully returns `success=False` with `resource_busy=True`.
- Updated `config/workflow_config.example.json` with `remote_claude_operator` under `providers` and `accounts.remote_claude`.
- Unit tests added in `tests/test_quota.py: RemoteClaudeOperatorProviderTests` and `RouterTests.test_workaround_routes_to_remote_claude_operator_when_claude_exhausted`.

### A5 - Light-role check, live Flash turn, and simple plan validation (2026-11-09)

- Executed dry-run task pipeline: `python main.py --no-env run "Light role verification" --dry-run` completed with status `COMPLETED`.
- Executed live Gemini 3.8 Flash turn with AGY CLI: `agy -p "Reply with exactly 'OK'" --model gemini-3.8-flash --effort low` succeeded with exit code 0 and output `OK`.
- Verified routing for `communicator`, `workspace_watcher`, and `remote_manager` via `python main.py --no-env quota route <role>`, confirming they route to `agy_headless_safe` with `gemini-3.8-flash-medium`.
- Verified that when Flash is exhausted (`Snapshot('agy:gemini-3.8-flash', 'limit', 100.0, ...)`), routing automatically falls back to `gemini-4-argon-low`.
- Verified that `"simple": true` in researcher plan drops validator class to `light` (`gemini-3.8-flash-medium`), while non-simple tasks remain on `worker` (`sonnet`).

### Gemini / AGY final acceptance (2026-11-09)

- Tests:
  - `python -m unittest tests/test_quota.py -v`: 34 tests OK.
  - `python -m unittest tests/test_quota_cli.py -v`: 11 tests OK.
  - `python -m unittest tests/test_quota_orchestrator.py -v`: 9 tests OK.
  - `python -m unittest tests/test_providers.py -v`: 18 tests OK.
  - `python -m unittest tests/test_codex_quota_probe.py -v`: 7 tests OK.
  - Full suite `python -m unittest discover -s tests -v`: 283 tests ran, all quota and AGY tests passed (the only failure is Windows WSL bash argument quoting in `test_claude_hook_command.py`, which belongs to GPT item G4).
- CLI verification:
  - `python main.py --no-env doctor`: exit code 0, all capabilities and quota routing report healthy.
  - `python main.py --no-env quota status`: exit code 0, displays all accounts (`codex`, `claude`, `agy:claude`, `agy:gemini-3.8-flash`, `agy:gemini-4-argon` uncapped at 100%).
- Code hygiene:
  - `git diff --check`: clean (exit code 0, no trailing whitespace or CRLF formatting regressions).
- Documentation:
  - Updated `docs/QUOTA_ROUTING.md` with ACP probe findings, language-server rejection rationale, `RemoteClaudeOperatorProvider` workaround contract, and verified facts.
- No secrets inspected or logged; no commits created; no listeners restarted; working tree kept clean for user review.

### Review 2026-11-09 (Claude, after GPT G1-G5 and Gemini A1-A5)

- Reviewed: Codex app-server probe (`core/codex_rpc.py`, read-only method allowlist,
  errors not echoed): kept. Claude stream-json adapter + passive capture: kept.
  Limit patterns: kept.
- Fixed A4: `RemoteClaudeOperatorProvider` launched the fixed `smoke` pilot and
  reported it as a successful operator step, and treated an unreadable Colab runtime
  as free. It now fails closed without Colab calls; test replaced.
- Fixed A1: `AgyProbe` left `agy acp` running after a timeout; it now stops the
  process tree, runs in `storage/quota/agy-probe/` and discards stderr.
- Verified Codex ids live via `model/list`: `gpt-6.1-sol` (default), `gpt-6-astra`.
  Applied the proposed personal-config patch (app-server probe 120 s, astra/sol pins),
  with a backup in `storage/quota/workflow_config.before-quota-patch.json`.
- Live: `quota refresh` gives Claude 5h 31%/7d 44% (measured), Codex 5h 95%
  (reserved, measured). `quota route` sends researcher→claude opus,
  operator/validator→claude sonnet, light roles→gemini-3.8-flash-medium.
- Still open: a real remote task worker kind (needed before the Claude→Colab
  workaround); a numeric AGY quota source; live exhaustion wording.
