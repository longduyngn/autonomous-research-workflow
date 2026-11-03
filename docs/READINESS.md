# Workflow review — 2026-11-02

The design is coherent for trusted local coding/research work: a deterministic
controller manages the queue and evidence, while replaceable models perform
scoped roles. It now has a clearer first-run path and observable task/run state.
It is not a multi-tenant service or an unattended cloud VM fleet.

## What changed

| Finding | Result |
|---|---|
| Global CLI override was not durable across worker processes | Provider choice is stored on submission and retained during recovery |
| Progress and early stdout disappeared until a role finished | Full stdout/stderr stream to per-task run files; the dashboard reads bounded live tails |
| Headless transcripts lacked useful step/attempt identities | Persistent task/step/role/attempt names, prompts, run records and emitted provider session IDs |
| Long-task context repeated large transcripts | Complete disk handoffs with bounded role excerpts and explicit full-log paths |
| Repeated failure reused the same research strategy | One bounded researcher replan, with verified steps/checks/backend and budgets preserved |
| Supplied files/Discord attachments were not prepared for agents | Hashed input snapshots, explicit task input paths, filename/size checks and recovery inheritance |
| Interrupted preparation could become runnable | Stale incomplete initialization escalates instead of silently running without inputs |
| API researchers could not inspect PDF papers or continue truncated text | Paginated PDF text tool and text offsets; diagrams/scans still need visual tools |
| New-user setup assumed an existing `.conda` and local Colab cache | Short venv guide, portable config example, declared dependencies, validated lock snapshot and explicit backend setup |
| Slow delivery could block the scheduler's task dispatch | A bounded separate delivery worker handles its periodic outbox processing |
| Colab silently substituted unavailable requested hardware | Explicit failure; runtime changes require a hardware choice |
| Optional root plugin duplicated lifecycle hooks and hardcoded machine paths | Duplicate hook disabled; root/plugin launch examples use the active interpreter |
| Old experiment/report/manual messaging files cluttered source | Moved to `archive/legacy-20261102/` with verified hashes; source snapshots exclude archives |

`.env` files were not inspected or edited. Credential values were not printed.
Existing roles, model bindings and task history were preserved. Configured legacy
Antigravity names are not security labels: this installation's permission flags
are its existing local choices. The portable example uses stricter defaults.

## Current verification

- Real Codex local smoke pipeline: all four roles completed; native hooks,
  artifact skill execution, independently checked `sum=4950,count=100`, local
  checkpoint and console delivery passed. Evidence:
  `storage/verification-local/codex_cli/task_00001_summary.json`.
- **66 automated tests passed in both the existing and fresh environments.** The suite checks queue limits/leases, retries, checks, provider contracts,
  input preservation, replanning bounds, live logging, dashboard HTTP boundaries,
  attachment rejection, API PDF/text access and delivery responsiveness.
- Dashboard rendered actual Task 19 in headless Edge; transcript controls,
  desktop/mobile rendering and horizontal-overflow checks passed without browser
  errors. Screenshots/results: `storage/audit-20261102/dashboard-*`.
- The API researcher extracted the actual supplied RSA assignment (9 PDF pages).
- Fresh Python 3.11 environment installation from declared requirements and
  dependency consistency checks were exercised separately from the existing
  `.conda` environment. Exact versions are saved in `requirements.lock.txt`.
- A new real Antigravity local four-role pipeline also passed with native hooks and skill receipts: `storage/verification-local/agy_headless_auto/task_00001_summary.json`.
- No cloud allocation or new outbound user-message test was performed here.
  Earlier cloud experiments remain historical evidence, not a current cloud test.

The final test counts and setup results are recorded in
[the Ralph checklist](RALPH_READINESS.md) with original logs under
`storage/audit-20261102/`. Cross-platform CI is configured; Linux execution has
not been claimed as locally verified.

## Limits and remaining design decisions

1. **Provider quality is configured, not measured.** Empty model bindings may use
   the same account model at every tier. Pin models and compare real tasks when
   deciding the researcher/worker/fast assignments.
2. **OpenCode and Claude need live account checks.** Their CLIs are not installed
   here. OpenCode's JSON/permission adapter is contract-tested, not live-proven.
   Direct API tool execution is tested with mocked responses; free model
   availability/quality is account-dependent. No paid fallback is introduced.
3. **Native role permissions differ.** Native shells and API shell execution run
   trusted code; prompt-only validator restrictions are not a filesystem sandbox.
   Hooks provide policy/receipts, not complete isolation from hostile programs.
4. **Complex research still needs a good protocol.** Sequential milestones and
   bounded replanning help, but a dependency graph, parallel experiment branches,
   dataset governance and semantic memory are not implemented. A small smoke
   test is insufficient proof of a full paper reproduction.
5. **Cloud/resource waits need durable ownership.** Personal Colab uses one shared
   browser connection; ownership is currently in the bridge process, not a
   restart-proof resource manager. Genuine approvals currently require escalation
   and explicit recovery. The user approved [resource/approval queues next](ROADMAP.md).
6. **Some state remains intentionally historical.** One old controller Kaggle job
   record says `running`; its actual remote state was not queried or cancelled.
   Old mock tasks and corrected outcomes remain visible as history. No cleanup
   action claims those old executions were real or stops unrelated experiments.
7. **Delivery is at-least-once and services are local.** A send/ack crash can
   duplicate a message. Dashboard remote-user authentication and direct vendor
   webhook signature validation are outside this release.

## Review coverage

The maintained first-party Python, role prompts, active configuration,
hook/skill definitions, UI, tests, documentation and standalone notebooks were
inventoried and reviewed by component. Python/JSON/notebook structures were
parsed, code paths inspected and executable behavior tested. The file inventory
records each maintained file individually; see [file coverage](FILE_AUDIT.md).

Installed environments, dependency caches, Git internals, generated bytecode,
IDE-local metadata and prior task snapshots are not represented as line-by-line
source audits. Runtime databases/logs were inspected for the relevant task,
provider and ownership evidence. This boundary avoids pretending that inspecting
dependency binaries or every historical transcript establishes code correctness.
