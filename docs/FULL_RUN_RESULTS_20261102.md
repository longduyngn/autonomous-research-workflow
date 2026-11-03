# Full workflow verification ? 2 November 2026

The two requested real Colab T4 executions succeeded: Antigravity executed task20, and Codex executed task23 after the first runtime was stopped. These were separate executions of the same training implementation. Local probes and report recovery completed. Kaggle calculations passed, but its final task validation remains escalated for the hardware evidence gap described below.

## Experiment and measured results

Synthetic two-moons classification used supplied CSV files (2,000 train, 1,000 test rows), three seeds (11,23,47), and two models: linear logistic classification and a 2?32?32?1 ReLU network. Each model trained for 300 full-batch Adam epochs on actual Tesla T4 CUDA. The test set was used for final evaluation. This is an integration experiment, not a published-paper reproduction.

| Model | Seed11 | Seed23 | Seed47 | Mean |
|---|---:|---:|---:|---:|
| Linear |87.1%|86.9%|86.9%|86.97%|
| MLP |99.6%|99.5%|99.4%|99.50%|

Both trials produced byte-identical weights, predictions and loss curves. A separate Python-standard-library CPU implementation recomputed every probability/class and all accuracies: 6,000 predictions per trial, 12,000 total. Independent BCE-loss recomputation for the first trial differed by at most 4.30e-8. These are three seeds on one synthetic dataset, not broad model-quality evidence.

| Driver | Task | Actual remote program elapsed | Run ID |
|---|---:|---:|---|
| Antigravity |20|18.950 s|9773dc9e8e434240ae50ff65eff37518|
| Codex |23|19.052 s|02316ee972fe4a059f7103ada81f2da3|

These times cover the remote experiment program, not total workflow latency. Planning, CLI startup, browser connection, validation, documentation and deliberate repair probes took much longer. This run is not a normal latency benchmark.

Both receipts establish `shutdown_verified: true`, `runtime_stopped: true`, and `active_session_count: 0`. The first finished at 13:58:00 UTC; the second started at 14:06:51 UTC. No simultaneous Colab GPU allocation occurred. A fresh final session-manager observation also found zero active sessions. No third Colab experiment was requested.

Raw evidence remains under `storage/workspaces/task_00020/artifacts/` and `storage/workspaces/task_00023/artifacts/`. The executed notebooks, CSV hashes, weights, predictions, curves, metrics, hardware and provenance are retained. Receipts/logs are private operational evidence and can contain connection metadata; this report deliberately includes only selected safe fields.

## What the Discord simulation actually tested

The harness authenticated as the existing bot, posted clearly labelled bot-authored test requests with real attachments, and substituted an allowlisted human identity only in the local in-memory Message object before calling the production command handler. The visible Discord messages stayed bot-authored. No human account authentication or impersonated public message occurred.

This exercised production command parsing, allowlist checks, real CDN attachment download, immutable input snapshots, enqueueing, two concurrent workers with overflow queued, duplicate-request suppression, status commands and real outbound delivery. It did **not** test a human-authored request entering through the Gateway WebSocket. Recovery tasks were submitted through the controller API and linked to their failed parents; they were not additional simulated human Discord requests.

## Live feature coverage

| Feature | Evidence / result |
|---|---|
| Mixed role providers | task20: Codex researcher/validator, Antigravity operator/communicator |
| Per-task Codex override | tasks21?23; fresh GPU execution in23 |
| Native skills/hooks | named role transcripts, hook-events and artifact registrations retained |
| Isolated workspaces and attachments | task-specific snapshots/hashes; local audit and numerical tests passed |
| Two-worker queue | ingress probe observed two leased tasks and a pending overflow task |
| Milestones and executable checks | source, GPU verification, then reporting in20 |
| Bounded retry/replan | two deliberate local-only failures, operator retry, one researcher replan; verified prefix/backend preserved |
| Fail-closed validation | validator rejected incorrect report sizes/hash despite correct GPU outputs |
| Local dashboard | real task state, stages and named run logs; browser reported no JavaScript errors |
| Colab safety | two actual T4 runs, sequential, output export and verified shutdown |
| Kaggle lifecycle | CPU-only; failed first job and corrected recovery retained |

The two `CONTROLLED_E2E_FAULT` failures were intentional acceptance probes after successful export/shutdown, not training defects. They consumed two attempts. A subsequent genuine documentation error exhausted task20's budget. Historical task20 remains escalated; successful repair belongs to a linked new task rather than rewriting history.

## Issues found and fixes

- Colab's collapsed header hid Runtime controls. Runtime inspection/hardware/shutdown now restore the visible controls before operating them.
- Hardware-selection failures were previously ignored by notebook execution. Execution now stops if selection fails or is not saved.
- A read-only Codex search containing the quoted word `shutdown` was incorrectly blocked. A narrow literal read/search exception fixes that false positive; real shutdown/deletion/code commands retain their approval policy.
- Discord bot construction now separates handler creation from Gateway startup, allowing the real command handler to be tested without launching a second listener.
- Future Colab control responses omit unnecessary browser URLs to reduce proxy-token exposure in receipts.
- The Kaggle notebook lacked a kernelspec and failed remotely before running code. Submission now stages a Python3 kernelspec and cell IDs, rejects unsupported notebook/kernel types and overlong slugs before push, and preserves the source notebook. Regression coverage checks the staged file.
- Antigravity invented several report artifact sizes and one hash. Independent validation caught them. Recovery generates values from bytes and adds a manifest checker; a correct experiment alone does not guarantee a correct narrative report.
- An initial recovery incorrectly pointed at a managed workspace and was rejected by isolation before any agent spawned. A separate explicitly selected recovery source fixes this; the failed preparation is retained as task25.

## Kaggle recovery measurements and assistance

The corrected CPU job `6a4bcb8799304625a0fe0dc82a609efa` completed remotely. Its 100 batches of 10,000 samples yielded 785,541 points inside the unit quarter-circle: pi=3.142164, standard error=0.0016417860387711914. Independent regeneration matched every batch exactly. Exported platform was Linux, Python3.13.15, cwd `/kaggle/working`; staged metadata disabled both GPU and TPU.

This recovery was **controller-assisted**. Native Codex shell access to Kaggle failed at authentication with WinError10013; no push occurred. The host then attempted the same staged job, which Kaggle rejected with HTTP400 for the long slug. Shortening the slug allowed submission; the returned actual slug was saved. The host also downloaded outputs. No duplicate successful submissions or Colab allocations were used. Task24 exercised the ordinary WAITING_REMOTE polling/error path; successful recovery output transport included host intervention, so a fully unattended Codex-to-Kaggle success is **not established** by this test. Operator guidance now prefers the connected notebook MCP endpoint for authenticated networking instead of a sandbox-blocked shell.

## Boundaries and next work

Claude, OpenCode/OpenRouter and API-only model execution were not live-tested here. Provider contracts are covered by local checks, but this report does not certify those integrations. Durable shared-resource/approval waiting queues remain future work; this run enforced the single-GPU restriction explicitly. No MCP self-approval was used. Unrelated jobs were not cancelled. No GPU cancellation path was tested. Native approval requirements remain in force.

A useful next improvement is automatic deterministic artifact manifests for every report, plus durable resource leases so concurrent requests wait for the one personal Colab GPU instead of relying on sequential operator instructions.

## Final outcomes

Tasks21 and22 completed their local audit/numerical checks. Task23 completed the separate fresh Codex T4 run. Task26 completed the local task20 report recovery, including nine manifest entries checked against bytes, unchanged historical exports and independent prediction checks. Task20 remains ESCALATED for its original report error; task24 retains its failed Kaggle run; task25 retains the rejected recovery preparation. Task27 is ESCALATED: its independent numerical checks and all 15 manifest entries passed, but the validator requires execution-specific evidence that no accelerator was present. Only CPU submission metadata and GPU/TPU-disabled flags were collected. No further job was started; this missing observation is explicitly unresolved. Successful Kaggle completion must not be inferred from correct calculations alone.

The final local regression suite passed **71 tests in37.122 seconds**. `git diff --check` passed. Dashboard views20/23/26 rendered without JavaScript errors. Real Discord result messages for21/22/23 and escalations20/24 were fetched via bot REST and recorded, rather than inferred only from outbox state. See the accompanying execution evidence in `storage/full-run-20261102/`.


Original configuration was restored byte-for-byte (SHA256 `8eb2c08e84032022d54706d22eb6228ee573f513b9a9c06c4cfe7f38fd839c77`). The queue is idle. The verified test listener was replaced by the original-config listener, PID29784, with a connected Gateway and fresh listening heartbeat. The dashboard remains at http://127.0.0.1:8787. The Colab MCP transport was closed after the independent zero-session observation; its transport-close response is not VM shutdown evidence.

The requested two Colab experiments passed; **the entire feature set is not certified production-ready**. Kaggle required host assistance and lacks the final hardware observation. A fully unattended Codex/Kaggle path and durable shared-GPU/approval waiting remain work to address separately.


Follow-up CPU profile audit: [CPU_PROFILE_AUDIT_20261102.md](CPU_PROFILE_AUDIT_20261102.md). The absent GPU/TPU profile is normal for CPU. The earlier final validator demanded accelerator-absence evidence beyond the collected CPU computation protocol; guidance is now clarified without rewriting its historical verdict. Historical HTTP400 root cause was not conclusively exposed in the saved response.
