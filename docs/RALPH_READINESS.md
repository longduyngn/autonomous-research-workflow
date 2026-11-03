# Ralph readiness work

Work sequentially: inspect, reproduce, implement, check, record. Keep task history
and user changes. Do not inspect `.env` files. Do not confuse smoke checks with
completed research experiments.

- [x] 1. Inventory every first-party file, inspect source/config/hooks/skills/tests,
  and classify runtime data, historical reports and independent experiments.
- [x] 2. Fix reproducible controller/provider/configuration defects. Verify the
  installed Codex interface and a local end-to-end task; preserve agent tiers.
- [x] 3. Add persistent, readable run identities and bounded context handoffs for
  longer tasks. Decide on bounded researcher replanning with the user.
- [x] 4. Add a loopback-only, read-only live dashboard and CLI task/run inspection.
  Display actual stages, milestones, attempts, checks, sessions and transcript links.
- [x] 5. Move obsolete reports/experiments into a separate archive with a manifest;
  retain task databases, logs, workspaces and unresolved cloud ownership records.
- [x] 6. Write a short first-run guide, current architecture/provider guide,
  supported-environment limits and maintenance instructions. Verify a clean setup,
  regression suite, dashboard and local provider smoke checks.

## Decisions

- User selected local dashboard plus CLI status.
- User selected archival of old reports/experiments while preserving task history.
- User approved one bounded researcher replan after repeated failures.
- User selected independent mixed CLI/API providers; existing role defaults remain unchanged. OpenCode is opt-in.
- User approved a separate next extension for durable resource/approval queues; see ROADMAP.md. Existing authorized MCP operations may automate supported actions, but a model cannot grant itself new permissions.

## Evidence

1. First-party source/config/hooks/skills/tests/UI/docs were inventoried individually; `.env*`, installed dependencies, Git internals and generated runtime trees were excluded. Current per-file coverage: `FILE_AUDIT.md` and `audit-inventory.json`.
2. Provider selection persists across worker processes; initialization fails closed; current interpreter is used for bridge startup; implicit Colab GPU substitution was removed. Native Codex AND Antigravity local smoke pipelines each completed all four roles, hooks, skill receipts, executable checks and independent validation. Summaries: `storage/verification-local/{codex_cli,agy_headless_auto}/task_00001_summary.json`.
3. Named runs, provider IDs, full live logs, bounded durable handoffs and bounded replanning passed regression tests. Explicit CLI inputs/Discord attachments preserve hashes and survive retries. API paper reading was checked against the actual nine-page RSA assignment.
4. Local dashboard passed desktop/mobile headless Edge checks, transcript navigation, no horizontal overflow and no browser errors. It has no mutation endpoints. Browser evidence: `storage/audit-20261102/dashboard-browser-check.json` and screenshots.
5. Sixteen historical items (23 files, including the disabled duplicate plugin) moved with SHA256 verification; prior README retained as a supplemental copy. Manifest: `archive/legacy-20261102/manifest.json`. Task history and remote ownership records were not moved/deleted.
6. **66 tests passed** in the original environment (34.749 s) and a fresh Python 3.11 environment (35.240 s). The final API-context change also passed the four focused provider tests in BOTH environments. Dependency checks, compilation, JS syntax, clean-env doctor/dry-run, Markdown links and diff whitespace checks passed. Exact fresh dependencies are recorded in `requirements.lock.txt`.

The read-only dashboard is available at http://127.0.0.1:8787. Workers were reloaded only after the real queue was verified idle. No cloud allocation or new outbound test message was performed. Claude/OpenCode installations and real free API models remain external live-verification dependencies; the adapter contracts are tested, and no default model was invented.
