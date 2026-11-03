# Proposal implementation status — 2026-11-03

Latest verification: 115 tests pass; root hook setup/probes and actual desktop/mobile
dashboard rendering passed. See FINALIZATION_20261103.md for evidence and live limits.

This is the current source-level status. The older review's C/F labels identify
checkouts, not maturity levels. Its week/month examples are illustrative. We kept
two task workers, a maximum of three, optional milestones and sequential Ralph.

## The seven earlier core proposals

The saved conversation proposal lists seven core items plus an eighth Claude
compatibility item. They are tracked explicitly here to avoid conflating that
list with the seven bullets in the earlier readiness limitations.

| # | Proposal | Implemented behavior | Remaining boundary |
|---|---|---|---|
| 1 | Durable conversational intake | Stable actor/channel/message IDs, persisted intake before dispatch, exact-ID deduplication, model-backed conversation with scoped history, deterministic status/search, explicit new-task/retry commands | Natural-language text cannot silently authorize new work; multiple commands in one message are not split automatically |
| 2 | Task linkage and feedback | Reply mapping, task ownership, revisioned feedback, application between roles, recoverable responses, transactional finalization guard | Old tasks lack verified sender IDs; they remain available through the local dashboard/CLI rather than being silently assigned an owner |
| 3 | Compact task memory | Atomic state/handoff/progress, numbered plan snapshots, separate controller facts and model notes, evidence paths | No semantic vector database or automatic inference of missing experiment state |
| 4 | Event-driven researcher review | Verified milestone, new feedback and bounded repeated-failure review; existing checks and verified prefix preserved | Arbitrary scientific surprise cannot be detected automatically; operator/validator must report it through their results |
| 5 | Durable waits | Remote/resource/approval/provider/input states release workers; separate accumulated wait time and deadline; polling does not call models | CLI failures after possible effects are not automatically replayed; first-request HTTP 429 is the supported safe automatic provider cooldown |
| 6 | Durable resource ownership | One Colab owner/generation, cross-process operation lock, gateway checks for MCP and managed browser effects, verified shutdown before release | Direct browser/native shell access outside the managed gateway is not an OS sandbox; no live browser restart test in this revision |
| 7 | Exact-action approvals | Owner, task, arguments, workspace fingerprint, plan/feedback revision, expiry, one-use decision; native hooks/API shell gate; Discord/host control commands | Native vendor/account approval remains independent. Large workspaces beyond the bounded fingerprint require explicit host review. Uncertain effects require reconciliation |

Claude compatibility: generated instructions/hooks/skills plus explicit role-scoped
session resume are adapter-tested. A real Claude account smoke test remains needed.
OpenCode quality and model comparisons remain deferred as requested.

## New review ideas selected

- Explicit native session identity, per-role locking, bounded rotation and portable
  disk history. Every wake still receives current task facts.
- Durable computation checkpoint publication, manifests/hashes, compatible restore,
  timer helper at application safe boundaries and an actual local process-loss test.
- Cheap waiting, unknown-vs-stopped resource reconciliation, fencing at the executor,
  exact rather than broad approval, and retained failed observations.
- Disabled Shadeform/VastAI/Runpod/SSH lifecycle contract and capability discovery.

Not selected: automatic paid fallback, purchases, self-approval via MCP, token/key
rotation, a large fleet, parallel experiment branches, mandatory calendars, or
public artifact publication. Rented services have no installed vendor adapters.

## Evidence and deployment

See [the Ralph log](RALPH_RESUMABLE_20261103.md) and
`storage/ralph-20261103/iteration-09-tests-attempt1.log` (103 passing tests).
These tests include real subprocess execution, process-loss recovery and HTTP
gateway calls with mocked cloud/model dependencies. They do not prove live Claude,
Colab/Drive durability, Discord delivery or vendor provisioning.

Read [the operating guide](RESUMABLE_WORKFLOW.md) before restarting services. This
change does not automatically restart an existing listener/bridge or rewrite old
task bundles. Preserve current remote jobs, reconcile runtime state, then restart
the host bridge and listener when idle. Bridge protocol 2 rejects older peers.
