# Review reconciliation against the working checkout

The supplied review/probes target cf5a at a344797 (C). This implementation targets
D:/Antigravity_playgournd (called F in that review). R is the archived lab. C/F
are location/version labels, not quality grades. Week/month/12-hour examples
are illustrative; no calendar policy is adopted.

## Rechecked baseline defects

Strict verdict, explicit failure outcomes, real persistent Jupyter semantics,
bounded SQLite claims, task snapshots, allowlisted ingress, real cloud adapters,
durable outbox and bounded replan already exist here. Current regressions exercise
malformed/contradictory verdicts, validator failures, persistent cross-cell state,
stop-on-cell-error, claims, isolation and missing-auth MCP errors. Old C probes
are evidence about C, not proof those defects remain in this checkout.

## Accepted reference patterns

Reference agents.py resumes explicit sessions while injecting current decisions
and observations each wake; portable handover survives rotation. fleet.py keeps
provider errors distinct from machine termination. Adopt those boundaries, not
fleet size, automatic deployments, raw token rotation or prepaid purchasing.
Approval IDs alone are insufficient: exact action/revision/actor/expiry are needed.

Keep sequential Ralph and two task workers, optional milestones and practical
acceptance criteria. Role context is per task/workspace; never globally share one
operator/validator session across unrelated tasks. Validator history is not current
proof. Provider identity/instructions changes rotate the native cache.

## Staged acceptance

Iteration01: persistent role context and explicit native resume; 81 tests passed.
Subsequent iterations implement checkpoint/resume loss fixtures, conversation
feedback, cheap durable waits and resource reconciliation, exact-action approvals,
event reviews and the disabled rental/SSH extension boundary. See
PROPOSAL_STATUS_20261103.md for precise coverage and remaining boundaries. A checkpoint
written only to a disappearing VM is not durable. Long-running training must save
its model/optimizer/scheduler/RNG/data cursor itself; the controller cannot infer it.

No claims of a week/month soak, HA, live Claude readiness, new Colab experiments
or rented GPU execution are made by these offline tests.
