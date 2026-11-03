# User task inventory — actual completion boundaries

The earlier Ralph source checklist is not proof that all requested live behavior
works. Fixture completion, attempted delegation and real execution are distinct.

| User task | Actual status |
|---|---|
| Read/audit current workflow and compare report/reference | Source review done; findings and limitations recorded. Final production signoff remains open. |
| Make Antigravity/Codex headless execute real commands/code | OPEN: AGY authentication/600s timeout; Codex nested tool sandbox failure. |
| Share actual implementation work between both headless agents | OPEN: both assigned work, neither produced a usable implementation; Codex completed static review only. |
| Full real-user Discord task through researcher/operator/validator/communicator | OPEN: probe #29 verified status/approval routing only, not model/code execution. |
| Exactly three channels, safe discovery and env synchronization | Routing/env sync deployed and tested; real-user status/approval verified. Model clarification still depends on working provider. |
| Communicator across tasks; researcher/operator/validator per task | Implemented with user/guild isolation, native caches and disk memory; live repeated-wake continuity still needs provider acceptance. |
| Reference-style context rotation and disk handover | Implemented with usage threshold/fallback cap; provider-specific live behavior unverified. |
| Researcher intervention on special events | Milestone/feedback/failure and bounded explicit unexpected-result events implemented/tested; real complex-task exercise remains open. |
| Repair outbox, CLI validation and review validator contract | Outbox revisions/stale ACK fixed, common CLI validation added; strict verdict contract intentionally retained. |
| Replaceability via provider plugins/API | Existing adapters plus explicit plugin seam implemented; not a full rewrite of all built-ins into plugins. |
| Future rented GPU/SSH expansion | Requested extension seam implemented; no vendor provisioning executor, no automatic purchases. |
| Portable runtime/browser configuration | Helpers and environment overrides implemented; full Linux deployment unverified. |
| Docker/Compose reproducibility | Files/config validation done; build/run OPEN because daemon unavailable. |
| Track and commit maintained workflow code | DONE locally: daef10c, 821c94c, 6b2cfb3, ef961d9. Fresh tracked export doctor/tests pass. No remote/push configured. |
| Isolation, two task workers/queue, one Colab runtime | Implemented/tested; current resource ownership changes have no new live cloud acceptance. |
| Colab durable automatic/retrievable checkpoints | Checkpoint helper/skills/store and real local process-loss test done; application saves and explicit durable mount required. Live Colab loss/restore OPEN. |
| Dashboard, named headless runs and short onboarding | Implemented, dashboard rendered, source/docs committed. Keep readiness statements current. |
| Duplicate approval reply and damaged Vietnamese notice | Fixed, regression-tested, deployed; probe correction delivered in English. |

Next work must prioritize native provider execution, then real Discord task
execution/continuity/researcher event acceptance. Do not close these rows from
unit-test counts or the successful routing-only probe. No further action is needed
from the user for probe #29 itself.
