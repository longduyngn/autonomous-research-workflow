# Resource and approval queue roadmap (historical design)

The sequence below was approved during the earlier readiness review. Its core
wait/ownership/approval work is now implemented and offline-tested; see
[current coverage](PROPOSAL_STATUS_20261103.md) and [operation](RESUMABLE_WORKFLOW.md).
Live account/browser verification and installed rented-compute adapters remain
separate work. This document preserves the original acceptance rationale.

## Ralph sequence and acceptance criteria

1. Add `WAITING_RESOURCE` and durable resource ownership. A personal Colab
   runtime has one owner independent of local worker leases. Waiting requests
   release their local worker slot. Ownership survives bridge/controller restart.
   Acceptance: two competing tasks never operate the same runtime; restart does
   not silently hand an existing VM to another task.
2. Add `WAITING_APPROVAL` and an action-scoped approval record: requester/task/run,
   exact action hash, required identity, creation/expiry time and decision.
   Acceptance: stale, duplicate, wrong-user and wrong-action replies cannot
   authorize execution. Unapproved work remains pending without repeated model
   calls or burning the repair budget.
3. Integrate supported MCP actions. Existing authorized runtime operations can
   use official MCP tools and configured browser connection automation. A model
   cannot approve its own CLI/account permission request; a transport connection
   is not authorization. Unsupported actions fail explicitly without UI guessing.
4. Resume the approved exact action idempotently, then re-observe the actual
   resource before continuing. Never blindly replay a GPU submission. On timeout
   or denied approval, preserve artifacts and surface the outstanding action.
5. Expose resource ownership, waiting reasons, approval scope and decisions in the
   dashboard/CLI/authorized Discord commands. The current dashboard stays
   read-only; mutation endpoints need a separate authenticated control surface.
6. Test crash/restart, concurrent claims, expired approvals, interrupted actions
   and shutdown evidence using fixtures before any live allocation.

Do not auto-release Colab ownership merely because an HTTP transport closes.
Require explicit handoff or actual shutdown evidence. Retain failed observations.

Later candidates, subject to a separate scope decision: experiment dependency
graphs, parallel experiment branches, resumable remote execution environments,
semantic research memory, richer per-provider streaming and OCR/figure tools.
These are useful extensions; none is necessary to run a simple local task.
