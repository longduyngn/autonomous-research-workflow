# Ralph implementation: resumable research workflow

**Latest: iteration 09 source/fixture verification complete.** 115 tests passed;
hook probes from both cwd values and actual desktop/mobile dashboard render passed.
See FINALIZATION_20261103.md. Earlier pause/review entries remain historical.

**Iteration 09 remains open after review of the resumed agent run.** See
HANDOFF_20261103.md and REVIEW_AGENT_RUN_20261103.md. Review verified 109 tests;
follow R1/R2/R3 before final signoff. Historical pause evidence is retained below.

Target: D:/Antigravity_playgournd, not the older cf5a worktree. The review calls
these F and C respectively; R is archive/ref/agentic_workflow/Work/lab. These are
snapshot labels, not maturity grades. Week/month and 12-hour examples are not
requirements. Keep two task workers, optional milestones and sequential work.

## Sequential checklist

- [x] Recheck probe findings against current source; record adopted/deferred ideas.
- [x] Persistent role memory/native session capability and safe rotation.
- [x] Conversation intake, task-linked feedback, deterministic status/search.
- [x] Durable resource/approval/provider waits connected and fixture-tested; final integration review remains in iteration 09.
- [x] Portable atomic checkpoint store, retrieval and interrupted-process resume.
- [x] Disabled rental/SSH adapter boundary; no implicit purchases/fallback.
- [x] Integrated regressions, docs, CLI/dashboard visibility and final evidence.

Accept from R: durable identity, live state injected on every wake, explicit
handoffs, cheap waiting, resource reconciliation, atomic exported evidence.
Do not copy fleet size, key rotation, broad approval, auto deployment, mandatory
campaign calendar, parallel experiment branches or public artifact publishing.
Native session history is a cache, not verified evidence or portable memory.
Training must explicitly save model/optimizer/RNG/data-position state; a generic
controller cannot reconstruct unsaved tensors from a destroyed VM.

Tests must use isolated DBs/fixtures. No new Discord messages, model calls, GPU
allocations or paid services are needed for this implementation. Existing task
history, dirty work, credentials and previous observations stay intact.

## Iteration 01 checkpoint

81 tests passed (28.497s), log: storage/ralph-20261103/iteration-01-tests.log.
Role session/memory changes are connected; control.py is a tested draft only,
not imported by the scheduler/orchestrator. Native sessions are adapter-tested,
not live account-verified. No service restart, allocation or messages performed.
Next selected item: portable computation checkpoints, ahead of conversation UI.

## Iteration 02 checkpoint

85 tests passed (28.541s), log:
storage/ralph-20261103/iteration-02-tests-attempt2.log. The first packaging failure
is retained in iteration-02-tests.log. Relevant role-memory tests passed again
after session-ID validation hardening. git diff --check passed.

Completed: periodic application checkpoint helper, controller-side scoped store,
manifest/hash verification, compatible retrieval and fresh-directory restore,
new-task native skills and cloud/MCP/CLI endpoints. Real local child-process loss
and resumed RNG/progress exactly match uninterrupted execution. See CHECKPOINTS.md.
Not claimed: live Colab/Drive interruption, automatic Drive authorization, saving
unsaved ML tensors, or rented GPU execution. Existing task workspaces were not
rewritten and no service was restarted.

The next selected iteration was conversation intake/task linkage/feedback revision.
User clarified: continue sequential iterations without pausing between them.

## Iteration 03 checkpoint

Connected durable Discord intake with stable user/channel/message IDs, conservative
chat routing, explicit new-task/retry commands, reply linking, scoped status/search,
recoverable dispatch/response records and at-least-once response delivery. Feedback
is versioned and applied at role boundaries without changing original requests or
abandoning pending remote jobs. Finalization checks pending feedback transactionally.

88-test suite: 87 passed, one historical plan-filename compatibility failure; fixed
by retaining the old filename when there is no user-feedback revision. Focused
reruns: all 3 conversation and 13 readiness tests passed. No live Discord messages.
Natural-language intent inference is deliberately conservative; this is not yet a
freeform conversational LLM. Next: durable waits, then resource gateway fencing,
then exact-action approvals, each with independent regression checks.

## Iteration 04 checkpoint

Connected SQLite waits to scheduler and worker lifecycle. Remote polling now
returns the worker slot, retains job identity, and resumes validation only after
download. Provider cooldown is checked before a role starts. Approval/input waits
cannot auto-replay on a timer; denial/expiry escalates explicitly. Original start
time is preserved; waiting_seconds and a separate wait_deadline are persisted.
Two focused integration regressions passed, including restart, free-slot use,
no operator replay, provider cooldown and expired approval. Next: Colab gateway.

## Iteration 05 checkpoint

Persistent personal-Colab owner and generation are enforced inside the bridge for
MCP calls AND managed browser effects. OS locking covers the whole effect across
bridge processes. New/unknown state requires observed zero active sessions. Closing
transport retains ownership unless shutdown is verified. Three resource tests and
all 14 cloud tests passed. Live browser behavior was not exercised. Existing host
bridges must be restarted deliberately; incompatible protocol fails explicitly.

## Iteration 06 checkpoint

Native hooks now persist denied actions, bound to task owner, exact arguments,
workspace/source fingerprint, plan/feedback revision, expiry and one-use decision.
Discord decisions verify actor/channel; local control CLI is a trusted administrator
interface. Native provider permissions remain in effect. The role returns to a
durable approval wait; consumption followed by an uncertain failure requires human
reconciliation rather than replay. Two end-to-end approval fixtures passed after
fixing controller-log changes that incorrectly invalidated the source fingerprint.
API shell tools use the same gate. Safe first-request HTTP 429 can cool down; CLI
errors or HTTP errors after tools are not assumed safe to replay.

## Iteration 07 checkpoint

Each accepted plan has an immutable numbered snapshot. Researcher reviews verified
milestones sequentially (configurable), new feedback and bounded repeated failures.
Verified prefixes and existing executable checks remain preserved. Validator prompts
identify plan/feedback revisions and forbid invented blocking scope. Compact state
separates controller facts from model notes, with paths to full evidence. The new
event-order regression and all eight core workflow tests passed. No parallel branch
or mandatory task list was added. Next: disabled compute adapter extension boundary.

## Iteration 08 checkpoint

Added a typed, disabled compute extension boundary and capability discovery for
Shadeform, VastAI, Runpod and SSH. Requests require bounded cost/time and checkpoint
destination. Config alone cannot activate an uninstalled adapter. No vendor API,
SDK, SSH connection or paid fallback was introduced. Two contract tests passed.
Next: integrated regression, process/approval visibility, documentation and audit.

## Iteration 09 — paused, not complete

Integrated suite passed 103 tests in 40.254s, with raw output saved to
storage/ralph-20261103/iteration-09-tests-attempt1.log. Additional source/tests were
then added. Latest focused checks: approvals 3 passed; waits 4 passed; conversation
8 tests with 7 passing and 1 fixture error (disconnected bot.user is None).

The user explicitly requested pause and a full handoff. No new implementation
iteration was opened after that request. See HANDOFF_20261103.md for the exact
failure, release/feedback race review targets, deployment limits and restart plan.
Update (2026-11-03 20:09 +07:00): Independent review (`docs/REVIEW_AGENT_RUN_20261103.md`) confirmed 109/109 tests passing in `storage/ralph-20261103/review-next-agent/full-tests-attempt1.log` after fixing the Discord fixture, forced `/release` ordering, and pending-feedback separation. Subsequent Ralph changes R1 (strict `!` command prefix in `core/conversation.py`), R2b (test isolation from host Colab bridge and controller `.env`), and R3 (lock-contention proof inside `before_release`, role guidance for `pending_user_feedback`, and dashboard wait/approval/revision fixture tests) are applied in source and logged in `storage/ralph-20261103/session-activity-20261103.md`. Iteration 09 remains open pending host execution of the new 113-test suite log after `.agents/hooks.json` path update.

Independent review: 109 tests passed in 30.701s; log under
storage/ralph-20261103/review-next-agent/full-tests-attempt1.log. Doctor --no-env
and git diff --check also passed. New reproduced issue: ordinary text starting
with "task ..." creates a task because the command prefix is optional. Next agent
should follow the explicit R1/R2/R2b/R3 feedback in REVIEW_AGENT_RUN_20261103.md.
The test suite also spawned a real host Colab bridge; reviewer verified its origin
and terminated only that childless test-created process. Test isolation must be
corrected before final signoff despite the passing suite.
