# Three-channel workflow release review

User clarification: keep exactly task-proposal (conversation, clarification and
explicit !task submission), approval list (pending actions and owner decisions),
task log (progress/status/search on request). No separate agent-role channels.

## Findings reconciled and implemented

| Finding | Result |
|---|---|
| Outbox INSERT OR IGNORE | Fixed with revisioned UPSERT and version-checked delivery acknowledgement. Identical finalization does not redeliver. !retry already creates a new task ID; completed-task feedback does not reopen a task, so that specific reported P0 trigger was inaccurate. |
| Rented backends rejected by plan parser | Named extension plans now parse, then wait for explicit configuration without executing operator or purchasing resources. Vendor executors remain uninstalled; accepting a name does not mean provisioning works. |
| Validator prose repeats verdict | Strict rejection retained deliberately: contradictory/quoted verdicts cannot establish success. Role contract asks for exactly one verdict plus evidence. A future structured protocol is preferable to silently ignoring contradictory text. |
| Duplicate provider commands | Both commands now save through common configuration validation. Legacy command retained for compatibility. |
| Provider if/elif extensibility | Explicit host-installed BaseAgentProvider/ProviderRegistry seam added. Built-in adapters remain in their tested dispatcher; arbitrary task prompts cannot import plugins. |
| Windows environment assumptions | Added Python/site-packages layout resolution and CHROME_PATH/COLAB_BROWSER discovery. Runtime snapshots are host-local, not portable virtualenv packages. |
| Missing container packaging | Added Dockerfile/compose/.dockerignore. Compose config validates. Docker daemon is unavailable on this host; no successful build/Linux runtime is claimed. Codex static review caught .env.* exclusion, now fixed. |
| Source absent from Git | Maintained runtime/tests/UI/dependency/config templates are included in the release index. Personal config and generated root hook binding are excluded; fresh checkout falls back to the portable example config. Runtime history and credentials stay local. No remote is configured. |

## Context and reference audit

Reference agents.py uses explicit researcher/thread sessions and event-triggered
wakes, injects current registries/decisions, measures context where supported, and
requests on-disk handover before rotation. It does not globally share every role's
conversation. This release keeps one active cache per task/role for researcher,
operator and validator; communicator is shared per authenticated user/guild across
task reports and front-end conversation. Different users never share that context.

Task 28 has persisted researcher/operator/validator/communicator session IDs and
role state files. It had one failed operator call and a successful retry. The older
adapter clears cache after failure to avoid continuing uncertain actions. Multiple
UI titles across different roles/tasks, retry and rotation do not prove resume is
absent. No claim is made that one chat can grow forever or survive provider loss.

Now successful resumes preserve their prior ID when the provider omits a repeated
ID. Result receipts include generation/resume reason. Known context usage triggers
rotation at 64k input/cache tokens, with bounded recent on-disk handover; missing
usage falls back to 12 passes. History tail reads are bounded. Changing providers
or instructions rotates the native cache. Model notes are not current evidence.

Researcher review was already connected for milestones, user feedback and bounded
repeated failures. Added explicit `RESEARCH_REVIEW: reason` operator event for an
unexpected scientific result or critical missing input, bounded to two by default.
Outstanding remote jobs are not abandoned/re-submitted to trigger review.

## Actual headless delegation

Two isolated tasks were assigned: Antigravity session continuity, Codex portability.
Initial sandbox launches failed to authenticate/find home. Host-account attempts
were authorized. Antigravity then timed out after 600s without a usable patch;
Codex authenticated and emitted native hook/session evidence, but its nested shell
sandbox failed before reading/writing files. Neither is counted as implementation
success. Codex subsequently reviewed inline portability/container source without
tools and returned actionable defects; those were reviewed and incorporated.
Primary agent implemented/tested the remaining code. Raw receipts are retained in
storage/ralph-discord-release/{agy_headless_auto,codex_cli}/.

## Verification and readiness

124 tests passed in 39.141s (tests-attempt3.log), before documentation-only updates
and the additional default channel-ID alias. Earlier logs retain the scheduling
timing failure: the old fixture required overlap during a 25ms sleep. A barrier now
proves that two workers can run concurrently without relying on host timing.
Environment update tests prove allowlisted IDs change, fixture credential bytes
stay intact, BOM/newlines survive, duplicate keys fail and ambiguous discovery does
not create channels. New outbox tests cover a stale acknowledgement race.

Existing Discord category and exactly three channels were discovered live; IDs
were synchronized into env without printing/changing credentials. A failed attempt
to create extra channels got HTTP 403 before the user clarified three channels;
no extra channels were created. Channel discovery stays guild/category-scoped.
Responses persist their actual delivery channel separately from logical task scope.
Approval notices go only to the approvals channel; routine progress is on request.

This is suitable for a trusted local beta with measured fixture coverage. It is
**not yet fully production-verified**: real user-origin Discord acceptance is pending,
Antigravity's current headless authentication/execution needs a successful live
check, Codex's nested tool sandbox needs repair, Claude is not installed, Linux
container build is not verified, and rented GPU APIs are not implemented. These
are explicit boundaries, not features assumed complete from unit-test count.

## Human acceptance pause

After the new listener is running, send a ping in task-proposal and approval list
and wait for the actual user. Ask for !status in task log first, then an approval
decision for a clearly labeled non-executing routing probe. Do not fabricate an
inbound human message or mark the probe successful before DB evidence exists.
Only proceed to a real model-driven task after the provider/account blockers are
resolved. Preserve the previous successful GPU observations as historical evidence.

Final tracked-checkout verification: exported the staged index with no personal config or runtime state; doctor passed and the complete 124-test suite passed in 42.334s. Raw output: storage/ralph-discord-release/tracked-checkout-tests.log. This verifies tracked-source completeness, not remote publication or a Linux build.
