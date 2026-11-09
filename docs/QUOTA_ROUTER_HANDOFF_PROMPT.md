# Handoff prompt: finish the quota router (for GPT/Codex and Gemini/AGY)

Copy the common block plus your own section. Both agents work in the same checkout;
GPT owns Codex/Claude-adapter items, Gemini owns AGY items. Do not edit each
other's items.

---

## Common block (send to both)

You are continuing a feature in the Python repo `D:\Antigravity_playgournd`
(Windows, interpreter `.conda\python.exe`). Read `CLAUDE.md`, `docs/QUOTA_ROUTING.md`,
`docs/RALPH_QUOTA_ROUTER_PLAN.md` (spec + progress log), `core/quota.py`,
`core/quota_cli.py`, and the quota parts of `core/orchestrator.py`,
`core/providers.py` and `core/scheduler.py`.

Feature: track remaining usage for Codex, AGY and Claude Code; when an account has
≤10% left, route roles to an equivalent model with quota (prefer the provider whose
5h window reset most recently); keep the last 10% for work that only that provider
can do; use Gemini 4 Argon (AGY, no hourly cap) as reserve; give light work
(communicator, watchers every 30 min, remote_manager, simple validation) to
Gemini 3.8 Flash; when everything is exhausted use a workaround (e.g. Claude
exhausted → remote operator on Colab) or wait for the earliest reset.

Current state (2026-11-09): routing is enabled in `config/workflow_config.json`.
`python main.py --no-env quota status` measures Codex (rollout `rate_limits`) and
Claude (active probe: `claude -p --model haiku --output-format stream-json
--verbose` → `rate_limit_event.unifiedWindows.{five_hour,seven_day}.utilization`).
The three `agy:*` accounts are `unknown`: AGY 1.5.3 prints no quota in
json/stream-json, its `--log-file` only says `doRefreshQuota`, and `/usage` in print
mode is treated as a prompt. 262 tests pass.

Rules:
- Work in Ralph iterations: inspect → implement one item → verify → append an entry
  to the progress log in `docs/RALPH_QUOTA_ROUTER_PLAN.md` → continue.
- Never open or print credentials (`~/.codex/auth.json`, `~/.claude/.credentials*`,
  AGY/Google OAuth files, `.env`, CSRF tokens, bearer tokens). A probe may run the
  CLI, which authenticates itself; it must not read tokens itself.
- No paid calls in tests (use fixtures). A live probe may spend at most one tiny
  turn and must be throttled like `ClaudeCliProbe`.
- An explicit task `provider_override` is never rerouted. Every reroute must be
  logged as a `QUOTA_ROUTE` event.
- Don't restart listeners while tasks are active. Don't commit. Don't touch
  `.claude/worktrees/`.
- Done means: `.conda/python.exe -m unittest discover -s tests -v` passes,
  `.conda/python.exe main.py --no-env doctor` and `... quota status` run,
  `git diff --check` is clean, and docs match the code. Report what you verified
  live, kept separate from what you only tested with fixtures.

---

## GPT / Codex section

G1. **Live Codex usage.** Rollout files lag: when no Codex session ran recently, the
    snapshot goes stale (>15 min) and degrades to `estimated`. Find a
    credential-free live source: `codex app-server` JSON-RPC (e.g.
    `account/rateLimits/read`, check `codex app-server --help` and the generated
    schema), or the `token_count` event that `codex exec --json` emits. Add a
    throttled `CodexAppServerProbe`/`CodexExecProbe` and keep the rollout probe as a
    fallback.
G2. **Codex model ids.** List the real model ids available to this account (without
    reading `auth.json`): `codex` help/config/`~/.codex/config.toml` model entries or
    an app-server model list. Fill the frontier ("astra"-class) and worker
    ("sol"-class) entries in `quota_routing.classes` in the example config. Ask
    before changing `config/workflow_config.json`.
G3. **Codex limit errors.** Capture the real wording and the
    `rate_limit_reached_type` values when the 5h/weekly limit is hit (from saved
    rollouts or stdout logs). Make `LimitErrorDetector` parse their reset time.
    Add fixtures.
G4. **Passive Claude capture.** Switch the local `claude_cli` adapter in
    `core/providers.py` from `--output-format json` to `stream-json --verbose`.
    Keep the result, session id, usage, `is_error` and permission-denial parsing
    working, and feed `rate_limit_event` into `QuotaStore` after every run, so
    the active probe rarely needs to spend a turn. Keep the hook-evidence
    contract intact (see `tests/test_providers.py`, `tests/test_claude_hook_command.py`).
G5. **Reserve semantics for Claude.** `requires` can spend the last 10%. Wire
    `requires: {"operator": ["claude"]}`-style declarations for tasks that need the
    Claude CLI login on Colab (`core/remote_claude.py`, `scripts/remote_claude.py`).
    The Colab worker's own Claude login has its own quota account
    (`remote_claude`); probe it through the remote worker's stream-json output.

## Gemini / AGY section

A1. **AGY quota source.** Look for a credential-free, machine-readable quota source
    in AGY 1.5.3: `agy acp` (Agent Client Protocol stdio server: list its methods
    and any usage/quota/session-limit notifications), `agy --settings`, `agy models`
    flags, `stream-json` events under a limited account, and the local
    language-server/daemon status (`agy remote-control status`). Note: some IDE
    extensions read Antigravity's local language server `GetUserStatus`
    (per-model `quotaInfo.remainingFraction`, `resetTime`). Use it only if it
    needs no token scraping from process arguments or files; otherwise document
    why it was rejected. Implement the best source as a throttled `AgyProbe` that
    fills `agy:gemini-3.8-flash`, `agy:gemini-4-argon` and `agy:claude`.
A2. **Pool mapping.** Check whether AGY-hosted Claude Opus/Sonnet and Gemini 3.8
    Flash share one pool or have separate ones, and whether Argon is truly
    uncapped per hour (it may still have a daily or weekly cap). Update
    `account_for` and the `accounts` config to match the evidence.
A3. **AGY limit errors.** Collect the real exhaustion wording (e.g.
    `RESOURCE_EXHAUSTED`, model quota messages, reset hints) from `storage/logs`
    and a controlled run if one is cheap. Add fixtures and reset parsing.
A4. **Workarounds.** The remote paths (`core/remote_agent.py`,
    `core/remote_claude.py`, `scripts/remote_agy.py`) are not provider adapters
    yet, so `quota_routing.workarounds` is empty. Register a provider type for the
    remote operator through `ProviderRegistry` (an extension contract, see
    `core/provider_registry.py`), so that `workarounds: {"claude": [{"provider":
    "remote_claude_operator", ...}]}` routes a Claude-exhausted operator to the
    Colab worker. Respect the single managed Colab runtime lease
    (`core/resource_gate.py`).
A5. **Light-role check.** Confirm with a dry run plus one real Gemini 3.8 Flash run
    that communicator, workspace_watcher (30-min LLM review) and remote_manager
    route to Flash, that Argon-low is used only when Flash is exhausted, and that
    the `simple: true` validator path works.
