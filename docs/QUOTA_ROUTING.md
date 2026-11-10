# Quota-aware provider routing (opt-in)

By default every role runs on its configured provider and a quota or login error is a
failure (see [providers](PROVIDERS.md)). Set `quota_routing.enabled` to `true` and the
controller instead tracks usage per quota account, keeps the last 10% of each limited
account in reserve, and sends each role to an equivalent model that still has quota.
Every deviation from the configured role is an explicit `QUOTA_ROUTE` event.

Design record and verification log: [RALPH_QUOTA_ROUTER_PLAN.md](RALPH_QUOTA_ROUTER_PLAN.md).

## Quota accounts and where usage comes from

| Account | Meters | Source | Confidence |
|---|---|---|---|
| `codex` | Codex CLI | Default example: `codex app-server` stdio `account/rateLimits/read`, throttled 120 s, no inference. Newest rollout `token_count.rate_limits` is the fallback, with original timestamps preserved. Legacy `probe: codex_rollout` remains file-only. `auth.json` is never opened. | measured |
| `claude` | Claude Code CLI | Local runs use `stream-json --verbose`; `rate_limit_event` is captured from the complete saved stdout into QuotaStore, even with routing disabled. Active throttled Haiku probe and optional status-line capture supplement it. | measured |
| `remote_claude` | Colab worker's Claude login | Worker stream-json events captured into status and imported by `ClaudeCloud.status`; local `claude` usage is never assigned to this account. No extra inference probe. | measured when emitted; otherwise unknown |
| `agy:claude`, `agy:gemini-3.8-flash`, `agy:gemini-4-argon` | AGY pools | `AgyProbe` runs `agy acp` JSON-RPC `initialize` (0 tokens spent) to confirm account tier (`g1-pro-tier`) and model availability, cached at `storage/quota/agy_probe.json` (1800 s throttle). State comes from probe tier and limit-error text/run ledger. `agy:gemini-4-argon` is `unlimited` (percentages ignored, limit errors still honoured). | inferred / estimated |

A measured value older than `stale_seconds` degrades to `estimated`. A window whose
`resets_at` has passed is treated as reset (`inferred`). A newer measured snapshot with
headroom clears an older failure-derived limit state. Account names come from
`account_for` (codex_cli, claude_cli, or the AGY model prefix); `quota_account` on a
provider overrides it.

States per account: `ok`, `reserved` (remaining <= `reserve_percent`, default 10),
`exhausted` (100% or a limit error until its reset). Every limit error or manual
cooldown also blocks the account (`quota:<account>` in `provider_cooldowns`).

## Selection policy

For role R, class C = `role_classes[R]` (the validator uses class `light` when
`light_validator` is true or the research plan carries `"simple": true`):

1. Explicit `provider_override` (task or `--provider`): never rerouted. Exhausted: the
   task waits and the reason says "not rerouted". Reserved: runs, flagged `reserved_use`.
2. Members of `classes[C]` whose account is `ok`, ranked by: the role's previous route
   for this task (`sticky_routes`, avoids pointless memory rotation), then most
   recently reset 5 h window, then most remaining, then config order.
3. `workarounds[<blocked account>]` entries that are `ok`.
4. `reserve[C]` (the uncapped Gemini 4 Argon tier for that class).
5. Otherwise a durable `WAITING_PROVIDER` until the earliest known reset (fallback
   `default_cooldown_seconds`). The wait consumes no retry.

If `requires[R]` (or `quota route --requires`) lists accounts, only those accounts are
considered and their reserved last 10% may be spent; exhausted ones go to step 3 and
then wait (the uncapped reserve cannot do that work).
Task `research_plan` can declare `"requires":{"operator":["claude"]}` for work
requiring the local login, or `remote_claude` for a worker login. Task declarations
override global `quota_routing.requires`; explicit provider pins still win. The
remote launcher declares its own `remote_claude` requirement and returns
`WAITING_PROVIDER` before launch if exhausted. Its reserved use is logged.

A failed CLI run whose stderr/error/short output matches a limit pattern gets
`structured_data.quota`; the controller records a `limit` snapshot, logs
`QUOTA_EXHAUSTED`, cools the account down until the reset (or `default_cooldown_seconds`)
and waits without consuming a retry. Successful output that merely mentions limits is
ignored. Detection is text-based and best effort: confirm the wording on the first real
occurrence, and note that work performed before the limit hit is not undone.

Switching provider or model rotates role memory with a handover, as for any identity
change. Native session IDs remain caches.

## Configuration (`config/workflow_config.example.json`)

```json
"quota_routing": {
  "enabled": false, "poll_seconds": 120, "reserve_percent": 10, "stale_seconds": 900,
  "default_cooldown_seconds": 900, "sticky_routes": true, "light_validator": false,
  "accounts": {"codex": {"probe": "codex_app_server", "probe_interval_seconds": 120}, "claude": {"probe": ["claude_cli", "claude_statusline"]},
               "agy:gemini-4-argon": {"unlimited": true}},
  "classes":  {"frontier": [{"provider": "claude_cli", "model": "opus"}, ...], "worker": [...], "light": [...]},
  "reserve":  {"frontier": [...], "worker": [...], "light": [...]},
  "role_classes": {"researcher": "frontier", "operator": "worker", "validator": "worker", "communicator": "light", ...},
  "requires": {}, "workarounds": {}
}
```

- `classes` entries are `{provider, model[, account]}`; `provider` must exist under
  `providers`. An empty `model` means that provider's tier default (account default).
  The shipped Codex entries pin `gpt-6-astra` (frontier) and `gpt-6.1-sol` (worker),
  verified by this account's live `model/list` on 2026-11-09. Verify availability
  on another account. Set an entry to `""` to use provider tier defaults and
  `CODEX_RESEARCH_MODEL` / `CODEX_WORKER_MODEL` instead.
- Roles missing from `role_classes` are not routed. The `researcher` plan flag and
  `requires` are the only per-task inputs.
- `workarounds` is keyed by the blocked account, for example
  `{"claude": [{"provider": "remote_claude_operator", "account": "remote_claude"}]}`.
  The registered `remote_claude_operator` adapter currently fails closed (see below),
  so ship and keep `workarounds` empty until the Colab worker can run real tasks.
- A personal config without the block keeps working; `quota status/route` then fall back
  to the example block, labelled and disabled.

## Light work

Communicator, `remote_manager`, `workspace_watcher`, `world_watcher` and (flagged)
validators use class `light` = Gemini 3.8 Flash, with Gemini 4 Argon low as its
reserve. The workspace watcher's model review runs at most every
`watchers.workspace.llm_review_seconds` (example 1800); its cheap 30 s checks are
unchanged. When no light or reserve capacity exists the review is skipped and retried.

## Commands

    python main.py quota status [--json]      # refresh probes; honours active-probe throttling
    python main.py quota route ROLE [--task ID] [--requires ACCOUNT]
    python scripts/quota.py status|refresh|route ...
    python main.py task ID                    # quota_routes: QUOTA_ROUTE / QUOTA_EXHAUSTED events
    python main.py doctor                     # read-only quota_routing section

`route` is a what-if when routing is disabled. A poller refreshes snapshots every
`poll_seconds` while workers run and routing is enabled. Ledger rows
(`quota_ledger`: provider, model, account, tokens, duration, limit flag) are written
after each routed run.

### Claude usage probe (default)

`accounts.claude.probe: ["claude_cli", "claude_statusline"]` runs
`claude -p ... --model haiku --output-format stream-json --verbose` in
`storage/quota/claude-probe/` and reads its `rate_limit_event`: the same 5h/7d
utilization the API reports in `anthropic-ratelimit-unified-*` headers (the approach
of the reference lab's key probe, but through the CLI so no token is read). Each probe
spends one small Haiku turn, so the poller reuses `storage/quota/claude_probe.json`
for `probe_interval_seconds` (default 1800). Recent passive `claude_stream` data
for both windows skips the active call. Failed active attempts are throttled too.
`quota refresh` forces a probe; `quota status` honours the throttle. The probe
allows one turn and disables tools. Verified passive live 2026-11-09: 5h 26% / 7d 44%.

### Claude usage capture via status line (optional)

    python scripts/quota.py install-claude-statusline --check    # shows the change, writes nothing
    python scripts/quota.py install-claude-statusline --apply    # backup, then sets only statusLine
    python scripts/quota.py remove-claude-statusline --apply

The command chains an existing status line (so it keeps rendering), stores only the
`rate_limits` object, and refuses to replace a non-command status line. Claude Code
sessions can need a reload after the setting changes. Without passive events,
an active probe or this capture, usage remains `unknown` and relies on failures/ledger.

### AGY ACP probe and language-server analysis

`AgyProbe` runs `agy acp` (the Agent Client Protocol stdio server) in read-only
`initialize` mode with 0 tokens spent. It extracts:
- Account tier: `result._meta.antigravity.tier` (e.g. `g1-pro-tier`).
- Model availability: `result.models.availableModels` (`gemini-4-argon-*`,
  `gemini-3.8-flash-*`, `claude-opus-5-5-*`, `claude-sonnet-5-5-*`).

Probing is cached at `storage/quota/agy_probe.json` and throttled by `min_interval`
(default 1800 s, forced on `quota refresh`). It produces snapshots for `agy:claude`,
`agy:gemini-3.8-flash`, and `agy:gemini-4-argon` with confidence `inferred`.

**Rejection of IDE language-server `GetUserStatus`**: While some IDE extensions
read Antigravity's local language server `GetUserStatus` for numeric `remainingFraction`
and `resetTime`, accessing it requires extracting an ephemeral CSRF token and private
port from process arguments or private filesystem locations. This violates strict
credential and sandbox boundaries. Furthermore, in headless/CLI environments, no
language server is running as a persistent daemon. Hence `GetUserStatus` scraping
was explicitly rejected in favor of the clean, credential-free `agy acp` protocol.

### Remote Claude workaround provider adapter (not usable yet)

`RemoteClaudeOperatorProvider` (`core/remote_claude.py`) is registered under the
provider name `remote_claude_operator`, but it refuses every run before touching Colab
(`success=False`, `structured_data.unsupported=True`). Reason: the cloud worker
(`scripts/remote_claude_worker.py`) only runs the fixed `smoke`/`pair` pilot
scenarios. It cannot receive a task workspace or prompt or return file changes.
An earlier draft launched a smoke pilot and reported it as a successful operator
step. It also treated an unreadable Colab runtime as free. Both would have marked
unexecuted work done or disturbed another experiment, so that draft was replaced on
2026-11-09. To finish this workaround, add a worker task kind with workspace upload,
the real prompt and collection of changes, then keep the `ColabGate` lease (failing
closed on unknown runtime state).

## Verified and unverified

2026-11-10 usage ledger extension: local Claude CLI invocations and the campaign's
Claude supervisor now write usage to both `quota_ledger` and `agent_runs`, including
turn count and the provider's cost estimate. A unique invocation `run_key` prevents
runner/router double counting. Session IDs identify native sessions, not individual
invocations. Existing rows are preserved by additive migrations; historical sessions
are not automatically backfilled from audit totals.

The ledger token estimator continues to use input + cache creation + output;
cache reads remain separately available in `usage_json`. This is not a monetary
invoice or measured account quota. Measured rate-limit snapshots still take priority.
One real native Sonnet run on a synthetic read-only fixture was verified in both
tables (one row each), with no telemetry error. It took one turn with 59,393 cache
creation tokens and zero cache reads, so native/global context remains substantial.
This does not establish savings for a long campaign or account-wide usage coverage.

Offline audit (explicit exclusions; no hardcoded SELF session):

```powershell
python scripts/claude_usage_audit.py audit.json
python scripts/claude_usage_audit.py audit.json --exclude-session UUID
```

Audit deduplicates message IDs and tool IDs, records scope/cutoff and skips malformed
trailing lines. Use the selected invocation's native session row when other live
sessions can change account-wide aggregate deltas. Verification and remaining live
limits: [quota optimization report](ralph-p1-20261109/QUOTA_OPTIMIZATION_VERIFICATION_20261110.md).

Verified here with fixtures and live read-only data: Codex rollout shape and parsing
(`quota status` shows the real account), Claude `rate_limit_event.unifiedWindows`
shape from saved stream-json, config validation, selection branches, orchestrator
waits without retry burn, installer behaviour on temporary files.
Verified live 2026-11-09:
- Active Claude CLI probe (`quota refresh`): 5h 26%, 7d 44%.
- Codex app-server read: 5h 76% (24% remaining), weekly 12% (88% remaining) and complete account model list.
- AGY ACP stdio probe (`agy acp`): tier `g1-pro-tier`, available models verified with 0 tokens spent.
- Live Gemini 3.8 Flash turn with AGY CLI (`agy -p "Reply with exactly 'OK'" --model gemini-3.8-flash --effort low` succeeded with exit code 0).
- Light-role dry-run pipeline (`main.py --no-env run "Light role verification" --dry-run`).
- Light-role routing: `communicator`, `workspace_watcher`, and `remote_manager` route to `gemini-3.8-flash-medium` when quota is ok, and fallback to `gemini-4-argon-low` when Flash is exhausted.
- Plan parsing: `"simple": true` in researcher plan drops the validator role class from heavy (`worker`) to `light` (`gemini-3.8-flash-medium`).
- Remote Claude operator provider: fails closed without Colab calls (unit test).
- Live routing 2026-11-09 with Codex reserved (5h 95% used): researcher→claude_cli/opus, operator/validator→claude_cli/sonnet, communicator/watcher→agy gemini-3.8-flash-medium.

The installed CLI schema supplies the `rate_limit_reached_type` enum, stored with
provenance in `tests/fixtures/codex_limit_schema.json`. Generic reached values
do not identify a window, so they create an unknown-window limit rather than
falsely setting primary usage to 100%. Structured native Codex errors preserve
their reset epoch; synthetic fixtures also cover ISO retry hints.
No true Codex exhaustion was present in 62 saved rollouts or inspected stdout logs.
Still unverified live: actual 5h/weekly exhaustion wording on live accounts, remote worker quota
capture (fixtures only), and optional Claude status-line field names. Personal
`config/workflow_config.json` has routing enabled with the Codex app-server probe
(120 s) and the `gpt-6-astra`/`gpt-6.1-sol` pins (applied 2026-11-09; backup in
`storage/quota/workflow_config.before-quota-patch.json`).
AGY numbers stay unknown: `AgyProbe` only records tier/model availability.
