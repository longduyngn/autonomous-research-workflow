# P2 execution log

Plan: `PLANNER_PLAN_VI.md` with overrides in `USER_DECISIONS_VI.md`.
User approval of the P2 plan: 2026-11-11 (session chat, "có duyệt").

Statuses: `ready`, `in_progress`, `verified`, `reused_verified`, `deferred`.

| ID | Status | Evidence |
|---|---|---|
| P2.0-01 | reused_verified | HEAD `db7aa24` (baseline `cab220d` + planner docs). Gate G on 2026-11-11: `unittest discover` 354 tests OK (exit 0); `main.py --no-env doctor` exit 0; `git diff --check` exit 0. No task/job state touched; `.claude/worktrees/` left untracked. |
| P2.1-01 | verified | OpenCode `opencode-ai@1.19.5` pinned in `config/provider_versions.json` (latest stable, 2026-10-28). Real binary probed against `tests/fakes/api_server.py` in an isolated XDG home: V1 `permission` schema only (V2 `permissions` rejected), denied tools are removed from the tool list, 429 → internal retries then one `error` event (statusCode, retry-after) + exit 1, `step_finish` carries tokens/cost. Two adapter defects found for P2.1-03: stdin must be closed (open pipe blocks before first model call) and `--dir <workspace>` is required (relative paths resolve against inherited PWD). Fixtures `tests/fixtures/opencode/1.19.5/`; `test_p2_opencode_version.py` 7 tests OK; gate G 367 tests OK, doctor 0, diff check 0. |
| infra | verified | `scripts/p2_autopilot.py` + `tests/test_p2_autopilot.py` (6 tests): model-free failover tick using the quota router frontier route; scheduled-task install was blocked by the session permission classifier and is left to the user. |
