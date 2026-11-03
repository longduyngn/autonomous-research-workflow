# A00 — Source, History, and Requirement Reconciliation Matrix (C / F / R)

## 1. Checkout & Version Provenance

| Label | Path / Commit | Role in Reconciliation |
|---|---|---|
| **C (BaselineReviewed)** | `C:/Users/PC/.codex/worktrees/cf5a/Antigravity_playgournd` (`a344797`) | Target of `probe_results.json` (SHA256 `b7e66e8f2a899aea22dee7371d0fc7094627bfd5b01beb3db5a73e6aab1f60cb`) and `workflow_comparison.md`. |
| **F (Current Working Checkout)** | `D:/Antigravity_playgournd` (`b40b375` on `master`, building on `ef961d9`, `6b2cfb3`, `821c94c`, `daef10c`) | Active implementation repository. 127/127 offline unit/integration tests pass; `main.py --no-env doctor` and `git diff --check` pass. |
| **R (Reference Archive)** | `D:/Antigravity_playgournd/archive/ref/agentic_workflow/Work/lab` | Reference control plane (`lab/{agents,approval,fleet,research,db}.py`) used selectively for session continuity, context handover, durable waits, and resource reconciliation patterns. |

## 2. Rejected / Excluded Proposals (Do Not Implement)

The following items from `workflow_comparison.md` and reference `R` were explicitly **rejected or excluded** by user scope and architecture decisions:

1. **Automatic Rented GPU Fleet & Prepaid Purchasing (`lab/fleet.py`, `lab/aws.py`)**:
   - Rejected. Personal workflow uses at most **1 shared personal Colab runtime** and local/Kaggle execution.
   - Shadeform, VastAI, Runpod, and SSH exist **only as disabled extension seams** (`core/compute.py`), never auto-provisioned or paid-fallback enabled.
2. **Raw Subscription Token / Key Rotation & Probing (`lab/keys.py`)**:
   - Rejected. Use explicit provider cooldown waits (`WAITING_PROVIDER`) on rate limits; never inspect, print, or rotate raw subscription tokens/credentials.
3. **Broad / Wildcard Maintainer Self-Approval (`operators=["*"]`, `auto_deploy=true`) or MCP Self-Approval**:
   - Rejected. Routine repository maintenance is authorized in isolated workspaces, but protected operations require exact-action/actor/revision/fingerprint approval (`core/approvals.py`) or deferral to `storage/ralph-next/approval_queue.jsonl`.
4. **Parallel Experiment Branches, Complex DAG Planners, or Multi-GPU Concurrency**:
   - Rejected. Keep 2 task worker slots (configurable max 3), sequential Ralph milestones within a task, and optional decomposition (simple tasks use `direct` mode without forced tasklists).
5. **Extra Role-Specific Discord Channels or Multi-Platform Expansion (Zalo/Messenger)**:
   - Rejected. Keep **exactly 3 Discord channels** (`task-proposal`, `approval list`, `task log`) scoped to a single guild/category; keep Zalo/Messenger disabled.
6. **Public / Gated-Public Artifact Publication by Default (`lab/hub.py`)**:
   - Rejected. Checkpoint and artifact destinations must remain private and explicit.
7. **Semantic Vector Database / Automatic Scientific-Surprise Inference**:
   - Rejected. Use structured SQLite + atomic JSON/Markdown handoffs and explicit `RESEARCH_REVIEW: <reason>` operator triggers.
8. **Rewriting the Controller in LangGraph or Deep Agents**:
   - Rejected. Retain the deterministic Python/SQLite controller (`core/orchestrator.py`, `core/database.py`, `core/scheduler.py`).
9. **Re-running Human Discord Routing Probe #29**:
   - Rejected / already completed (`storage/ralph-discord-release/human-acceptance-verified.json`). Probe #29 verified real user `!status` and approval routing plus duplicate-delivery and Unicode fixes, and must not be re-run or misrepresented as a live model/GPU task.

## 3. Baseline C Defects (`probe_results.json`) vs Current F Status

| C Defect ID | `probe_results.json` / `workflow_comparison.md` Finding | Current F Source & Fixture Status | Real / Live Verification Status |
|---|---|---|---|
| **C01** | Permissive verdict parser accepted empty, `"Unable to verify"`, contradictory, and quoted `VERDICT: PASS`. | **Fixed & Fixture-Verified** (`core/orchestrator.py:_parse_validation_verdict`, `tests/test_workflow.py`). Fails closed. | Exercised in offline suite; will be exercised in real nontrivial multi-role run (`F03`/`J03`). |
| **C02** | Validator transport errors (`timeout`) ended in `COMPLETED`. | **Fixed & Fixture-Verified** (`core/orchestrator.py`, `tests/test_workflow.py`). Explicit `outcome` column; validator errors never complete. | Source & fixture verified (`127` tests). |
| **C03 / C04** | Unbounded Discord threads and non-claiming task selection (`duplicate_task_selection`). | **Fixed & Fixture-Verified** (`core/scheduler.py`, `core/database.py:claim_task`, `tests/test_scheduler.py`). Atomic SQLite claims, 2 worker slots, leases. | Source & fixture verified (`J04`). |
| **C05 / C06** | `notebook_execution` ran cells in separate processes, continued after cell error, and faked remote Colab via local subprocess. | **Fixed & Fixture-Verified** (`core/cloud.py`, `tests/test_cloud.py`). Persistent `jupyter_client` local kernel stops on first cell error; Colab uses real bridge/playwright; no silent local fallback. | Local kernel live-verified in test suite; live Colab loss/restore gated by `D04`/`D05`. |
| **C07** | `missing_kaggle_credentials` returned `mcp_isError: false`. | **Fixed & Fixture-Verified** (`mcp_servers/notebook_cloud_mcp.py`, `tests/test_cloud.py`). Returns explicit error envelope. | Source & fixture verified. |
| **C08 / C18** | Shared root `cwd` across agents and hardcoded checkout paths in task bundles. | **Fixed & Fixture-Verified** (`core/workspaces.py`, `core/integrations.py`, `tests/test_integrations.py`). Per-task isolated git snapshot and generated hash-verified bundles. | Needs live headless worker verification (`B01`–`B03`). |
| **C09 / C13** | Unauthenticated ingress and one-shot outbound callback losing or duplicating replies. | **Fixed & Verified** (`core/conversation.py`, `gateways/discord_bot.py`, `tests/test_conversation_delivery.py`). Allowlist, revisioned outbox, atomic delivery claim lease. | **Live-verified** in Discord Probe #29 (`J02`). |
| **C10** | Text-only HTTP/Gemini providers allowed for operational roles. | **Fixed & Fixture-Verified** (`core/providers.py`, `core/provider_registry.py`, `tests/test_providers.py`). Action roles require tool capability. | Source & fixture verified (`H01`). |
| **C11 / C12** | No durable waits or researcher replanning on repeated failure / special events. | **Fixed & Fixture-Verified** (`core/control.py`, `core/orchestrator.py`, `tests/test_waits.py`, `tests/test_research_events.py`). | Needs nontrivial live task exercise (`F03`, `J03`). |
| **C14** | Naive 32k log truncation losing middle decisions. | **Fixed & Fixture-Verified** (`core/role_memory.py`, `core/orchestrator.py:_context`, `tests/test_resumable.py`). Fresh controller facts each wake, bounded role memory, 64k/12-pass handover rotation. | Needs real multi-wake native session proof (`C05`). |
| **C15 / C16 / C17** | Unconfined paths, blocking watcher loop, orphan child processes. | **Fixed & Fixture-Verified** (`core/cloud.py`, `watchers/`, `core/processes.py:stop_process_tree`, `tests/test_watchers.py`). | Source & fixture verified. |

## 4. Full 44-Task Ledger Status Matrix (`docs/ralph-next/tasks.json`)

| Task ID | Profile | Current Implementation & Fixture Status | Verification Needed |
|---|---|---|---|
| **A00** | `coordinator` | Completed in this matrix; 127/127 tests + `doctor` + `git diff --check` verified. | Record `source_verified`. |
| **A01** | `coordinator` | Host model confirmed as `Gemini 4 Argon (Medium)` (`gemini-4-argon-medium`); `agy models` confirmed `gemini-3.8-flash-low` / `gemini-3.8-flash-medium`; Codex cache confirmed `gpt-6.1-sol`. | Update `profiles.json` + `bindings.json` and record `source_verified`. |
| **B01** | `agy_flash` | `scripts/setup_hooks.py` and `tests/test_integrations.py` implemented; root hook works from repo and `.agents` cwd. | Run real headless `agy_flash` verification on `B01`. |
| **B02** | `codex_medium` | Previously failed when launched from inside `CodexSandboxOffline` (nested Windows sandbox error). Need to verify/fix from host user `DESKTOP-JMJKG45/PC` and check `CODEX_HOME` / sandbox handling in `core/providers.py`. | Test & fix real headless `codex_medium` execution (`B02`). |
| **B03** | `codex_medium` | Previously timed out at 600s in `agy_headless_auto` and missed log-based `conversation=<uuid>` extraction when stdout JSON is empty or non-JSON. | Verify/fix `agy` session/output capture in `core/providers.py` and run bounded native probe (`B03`). |
| **B04** | `codex_medium` | `core/providers.py` and `scripts/ralph_next.py` need strict guard so CLI exit 0 with a `Blocked` / `helper_unknown_error` / sandbox-refusal message cannot be marked as a successful operator implementation. | Implement/verify guard and test in `tests/test_execution.py` (`B04`). |
| **C01–C04** | `codex_medium` / `coordinator` | Implemented in `core/role_memory.py`, `core/providers.py`, `core/orchestrator.py`, ` core/conversation.py`; covered by `tests/test_resumable.py` and `tests/test_conversation.py`. | Audit/verify and enhance `agy` log session-ID extraction (`C01`–`C04`). |
| **C05** | `codex_medium` (`live_gate`) | Real multi-wake native session continuity + bounded rotation proof needed. | Execute real multi-wake native run after `B02`/`B03` (`C05`). |
| **D01–D03** | `coordinator` / `codex_medium` | Implemented in `core/checkpoints.py`, `core/cloud.py`, `core/integrations.py`, `tests/test_checkpoints.py` (including real child-process `os._exit(17)` death and deterministic restore). | Audit and verify (`D01`–`D03`). |
| **D04–D05** | `coordinator` / `codex_medium` (`live_gate`) | Offline checkpoint helpers ready; live Colab Drive mount and VM-loss probe require authorized live runtime/mount without disrupting user VM. | Prepare/verify destination check (`D04`) and defer/queue live VM destruction if unauthorized (`D05`). |
| **E01–E05** | `coordinator` / `codex_medium` / `agy_flash` | Implemented in `core/conversation.py`, `core/discord_channels.py`, `gateways/discord_bot.py`, `core/database.py`; covered by `test_conversation.py`, `test_discord_channels.py`, `test_conversation_delivery.py`, `test_notification_unicode.py`. | Audit/verify each contract (`E01`–`E05`). |
| **F01–F02** | `coordinator` / `codex_medium` | Implemented in `core/orchestrator.py`, `core/plans.py`, `tests/test_research_events.py`. | Audit/verify (`F01`–`F02`). |
| **F03** | `codex_medium` (`live_gate`) | Needs real nontrivial task exercising `RESEARCH_REVIEW:` divergence -> researcher replan -> operator repair -> validator pass. | Execute real nontrivial task (`F03`). |
| **G01–G04** | `codex_medium` | Implemented in `core/control.py`, `core/scheduler.py`, `core/resource_gate.py`, `core/colab_bridge.py`, `core/approvals.py`, `core/cloud.py`; covered by `test_waits.py`, `test_resource_gate.py`, `test_approvals.py`, `test_recovery_evidence.py`. | Audit/verify (`G01`–`G04`). |
| **H01–H04** | `coordinator` / `agy_flash` | Implemented in `core/providers.py`, `core/provider_registry.py`, `core/configuration.py`, `core/compute.py`, `docs/PROVIDERS.md`, `docs/COMPUTE_EXTENSIONS.md`. | Audit/verify (`H01`–`H04`). |
| **I01–I02** | `codex_medium` / `agy_flash` | Implemented in `core/portability.py`, `Dockerfile`, `compose.yaml`, `.dockerignore`, `docs/CONTAINERS.md`, `tests/test_portability.py`, `tests/test_readiness.py`. | Audit/verify (`I01`–`I02`). |
| **I03** | `codex_medium` (`live_gate`) | Docker CLI present, Linux engine/daemon unavailable on host. | Check daemon status; queue in `approval_queue.jsonl` if unavailable (`I03`). |
| **J01–J04** | `agy_flash` / `coordinator` / `codex_medium` | `J01` (dashboard/observability), `J02` (Probe #29 routing reused), `J04` (2-task queue & exclusive Colab/CPU Kaggle) verified; `J03` (`live_gate`) requires full nontrivial real multi-role task. | Verify `J01`, `J02`, `J04`; run nontrivial real multi-role task for `J03`. |
| **K01–K02** | `agy_flash` / `codex_medium` | Docs, `.gitignore`, `config/workflow_config.example.json`, tracked checkout export verified. | Re-verify after any code updates (`K01`–`K02`). |
| **L01–L02, Z00** | `coordinator` | Final consistency matrix, deferred approval summary, and truthful readiness handoff. | Complete at end of Ralph run (`L01`, `L02`, `Z00`). |
