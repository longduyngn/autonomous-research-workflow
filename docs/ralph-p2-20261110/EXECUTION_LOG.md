# P2 execution log

Plan: `PLANNER_PLAN_VI.md` with overrides in `USER_DECISIONS_VI.md`.
User approval of the P2 plan: 2026-11-11 (session chat, "có duyệt").

Statuses: `ready`, `in_progress`, `verified`, `reused_verified`, `deferred`.

| ID | Status | Evidence |
|---|---|---|
| P2.0-01 | reused_verified | HEAD `db7aa24` (baseline `cab220d` + planner docs). Gate G on 2026-11-11: `unittest discover` 354 tests OK (exit 0); `main.py --no-env doctor` exit 0; `git diff --check` exit 0. No task/job state touched; `.claude/worktrees/` left untracked. |
| P2.1-01 | verified | OpenCode `opencode-ai@1.19.5` pinned in `config/provider_versions.json` (latest stable, 2026-10-28). Real binary probed against `tests/fakes/api_server.py` in an isolated XDG home: V1 `permission` schema only (V2 `permissions` rejected), denied tools are removed from the tool list, 429 → internal retries then one `error` event (statusCode, retry-after) + exit 1, `step_finish` carries tokens/cost. Two adapter defects found for P2.1-03: stdin must be closed (open pipe blocks before first model call) and `--dir <workspace>` is required (relative paths resolve against inherited PWD). Fixtures `tests/fixtures/opencode/1.19.5/`; `test_p2_opencode_version.py` 7 tests OK; gate G 367 tests OK, doctor 0, diff check 0. |
| infra | verified | `scripts/p2_autopilot.py` + `tests/test_p2_autopilot.py` (6 tests): model-free failover tick using the quota router frontier route; scheduled-task install was blocked by the session permission classifier and is left to the user. |
| P2.1-02 | verified | `core/opencode_config.py`: API profiles (openrouter/openai/deepseek/gemini/anthropic/local), config validation, credential referenced as `{env:NAME}` only, isolated XDG env stripping inherited `OPENCODE_*`. `account_for` meters `api:<vendor>` for OpenCode profiles and known http_llm hosts. Provider `opencode_api_openrouter` added to example + personal config. `test_p2_api_profiles.py` 12 tests OK; gate G 379 tests OK, doctor 0, diff check 0. |
| P2.1-02b | verified | `core/openrouter_free.py` + `scripts/openrouter_free.py`: public catalog (no key, no inference), zero-price hard filter (priced models never cached or selected), tools/context filter, routers excluded, weighted benchmark ranking per tier, per-model cooldowns, stale-cache fallback. Live refresh 2026-11-11: 13 free tool-capable models, worker top `nvidia/nemotron-3-ultra-550b-a55b:free`. Fixture `tests/fixtures/openrouter/models_20261111.json`. |
