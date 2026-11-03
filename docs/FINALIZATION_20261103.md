# Iteration 09 finalization

The handoff's remaining source and verification work is complete. Full suite:
**115 tests passed in 36.019 seconds**. Raw log:
`storage/ralph-20261103/finalization/tests-attempt2.log`. The preceding failed fixture
run is preserved as `tests-attempt1.log` (114 tests, one failure). Its dashboard
fixture started feedback_revision at one, applied another revision, and incorrectly
expected one; setup now starts at zero and tests the real increment.

## Root hook diagnosis and repair

The original root launcher used `python scripts/cloud_hooks.py`. It works only when
cwd happens to be the controller root. The reported Antigravity failure resolved it
relative to `.agents`. The root config is untracked, so there is no Git provenance
proving exactly when the cwd changed or which agent introduced this assumption.
Earlier successful generated task hooks use explicit launcher paths, and independent
Codex shell tests do not pass through this root Antigravity hook. Their success did
not validate this launcher. No vendor-update cause is asserted.

`scripts/setup_hooks.py --apply --check` now derives checkout and interpreter from
the setup script and active Python, updates only lifecycle launcher commands, saves
the original config under `storage/hook-backups/`, and probes the actual configured
PreToolUse command under both cwd values using a harmless read payload and no-env
mode. Applied with user authorization to repair the protected root hook; enabled,
matcher and timeout policy were retained. Both probes passed. Paths in installed
config are generated machine bindings, not literal machine paths in source code.
Rerun setup after moving checkout/environment. No automatic hot-reload behavior of
an already running Antigravity session is assumed.

## Remaining handoff items verified

- Strict commands and scoped reply feedback; native approval owner/action/revision;
  resource waits releasing workers; persistent role memory; bounded event reviews.
- Colab forced transport close occurs under the resource lock. Contender tests prove
  the lock is held. Pending feedback remains explicitly inactive until a boundary.
- Cleanup release does not bootstrap a host bridge. `WORKFLOW_NO_ENV=1` now prevents
  all cloud credential-file loading, including root-workspace use. After this suite,
  no `colab-bridge` process remained running.
- Discovery and special Discord channel recognition use the intended category and
  discovered IDs rather than granting special behavior to arbitrary equal names.
  A regression covers an unrelated category with matching channel names.
- Dashboard rendered in headless Edge at 1440x1000 and 390x844 against an isolated
  DB: waiting/approval/revision views present, zero JS errors or horizontal overflow.
  Screenshots and JSON evidence are under `storage/ralph-20261103/finalization/`.
- `main.py --no-env doctor` and `git diff --check` passed. Doctor found Antigravity
  and Codex; Claude/OpenCode were not installed. No-env credential flags are not
  evidence that credentials are absent from user configuration.

## Operating boundaries

One managed Colab runtime, two task slots (maximum configurable three), sequential
milestones, and no implicit paid fallback. Drive checkpoint persistence still
requires an explicit durable destination and application state saves. Vendor GPU
adapters remain disabled extension points. Claude resume is adapter-tested, not
live-account-certified. This finalization did not send Discord messages, allocate
compute, restart the listener/bridge, or retest live model sessions. Existing task
bundles/history were not rewritten and no commit was created.

Channel proposal mode intentionally treats plain messages in the discovered
task_proposal channel as explicit submissions; ordinary DMs/mentions remain chat.
Use !status in task_log for deterministic status. A configured stale outgoing
channel ID does not currently trigger automatic recovery on HTTP 404: discovery
fallback applies when no target is configured. The earlier session report's broader
fallback claim should not be interpreted as implemented behavior.
