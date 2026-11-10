---
name: smile-a100-reproduce
description: Run and supervise the SMILE A100 reproduction campaign with Claude Sonnet execution, AGY Gemini 3.8 coordination and communication, Claude Opus research, an independent Gemini 4 Argon validator, and a human-proxy review every 15 minutes.
---

# SMILE A100 reproduction: executable Claude handover

## Active provider override (2026-11-08, latest user instruction)

All Codex campaign roles are retired. Operator = native Claude Sonnet
`claude-sonnet-5-5`; planner = independent native Claude Opus
`claude-opus-5-5`; validator = independent AGY `gemini-4-argon-high`.
All other roles, including coordinator, communicator and 900-second human-proxy
guardian, use AGY `gemini-3.8-flash-medium`. This overrides historical tables.
Read `D:/Antigravity_playgournd/storage/campaigns/smile-pre-publish/claude_roles/HANDOFF.md`.
Desired routing is campaign `role_routing.json`; actual activation/native evidence
is `agent_roles.json` and `claude_roles/handoff_acceptance.json`.
Sonnet/Opus supervisor is `claude_roles/supervisor.py`; independent Gemini
coordinator/communicator/guardian is `coordination/supervisor.py`; role messages
are in `messages/`. Long operator work cannot block the 900-second review.
Current native agents run on Windows and control detached Python jobs on Colab
through the fenced bridge. A native cloud agent is NOT active on the current VM.
Do not relaunch primary cohorts, old CPU pilots, retired Codex/mixed AGY/Claude
supervisors, or active validator calls when changing role providers.

## Mission and authorization

The user delegates execution and routine decisions to this campaign. Start working;
do not stop after writing a plan. Reproduce as much as possible of
https://github.com/minhtuong201/smile-pre-publish on A100. Use small Ralph cycles:
inspect one item, implement, verify with evidence, record, and continue.

The latest user explicitly requests a real Claude run, a Claude agent replacing
their human supervision, and permits the handing-off assistant to stop only once
that guardian exists and is operating. Operators use Sonnet; the planner uses
Opus. Important results must be reported directly in Vietnamese chat/status;
the user does not want to read documentation to discover progress.

Autonomously repair malformed planner output, dependency/setup issues,
transient errors, resumable job failures, resource backoff, and role handovers.
Let the responsible planner attempt its own format/plan repair before changing
task state or escalating. Do not send Discord alerts for each small repaired error.
Notify the user only about an unresolved blocker or consequential decision.
Existing project Discord channels may be used for authorized campaign progress;
do not invent recipients or send unrelated messages.

Use the user's existing authenticated Colab/A100 allocation and subscription CLIs.
No new paid rental, increased billing cap, API-billing fallback, silent hardware
substitution, or irreversible account/repository publication is authorized here.
Real MFA/consent or missing credentials remain blockers; continue independent work.

## Roles and execution placement

| Role | Provider/model | Responsibility |
|---|---|---|
| Local operator | Claude Sonnet `claude-sonnet-5-5` | Prepare jobs, preserve leases, transfer artifacts, recover/reconcile |
| Cloud worker | Detached Python/vLLM on managed Colab | Execute experiments; no active native cloud agent |
| Planner | Native Claude Opus (`claude-opus-5-5`, live verified) | Read source, define matrix/protocol, diagnose scientific issues |
| Validator | AGY, Gemini 4 Argon (`gemini-4-argon-high` preferred) | Independent raw-artifact checks and scientific verdict |
| Communicator | AGY, e.g. previously verified `gemini-3.8-flash-medium` | Persistent conversation/status, concise Vietnamese summaries |
| Coordinator and human-proxy guardian | AGY Gemini `gemini-3.8-flash-medium`, shared with communicator | Coordinate routine next steps, check health every 900 seconds |

Verify current account/model availability; retain real native model/session IDs.
Resolve the native `opus` alias with a live probe and record its actual model ID;
do not pretend a Sonnet call is an Opus planner. Use shared subscription quota
economically; prefer deterministic code over repeated idle reasoning.
Keep validator independent of candidate changes and separate from guardian/operator.
Cloud operator plus deterministic validation code should run on cloud. A local
AGY Argon judge may inspect immutable cloud evidence if remote AGY authentication
does not survive VM replacement; report that placement explicitly. Do not relabel
a Claude validator as Gemini. Prefer remote AGY if its live authenticated session
can be reused without new human login.

Agents communicate via native messaging when available, otherwise durable inbox /
outbox files with campaign, task, job, sender, recipient, sequence, and receipt IDs.
Prove a real request and acknowledgement; a successful process exit alone is not
proof of agent communication. Preserve role memory across turns and export a
handover before session rotation. Never run the same native session concurrently.

## Fixed inputs and source inspection

Controller root on this host: `D:\Antigravity_playgournd`.
Campaign root: `storage/campaigns/smile-pre-publish` relative to the controller.
Target source is already cloned at `storage/campaigns/smile-pre-publish/source`.
Pinned initial commit: `4091726835e7fb43dedbb8092deffae58d4df595` (main/HEAD fetched
2026-11-08). Confirm it before execution. Keep this checkout pristine; put runners,
patches, environments and outputs in campaign-owned paths or a separate checkout.
Record patch hashes and distinguish original-code results from repaired variants.

Read target `README.md`, `environment.yml`, scripts and tests before scheduling.
README contains a pipeline but no numerical paper result table. Search tracked
files for an actual paper/table artifact. If none exists, report fresh measured
results and pipeline coverage; numerical agreement with an unavailable reference
is `UNVERIFIED`, not successful reproduction of an invented target.

Verified source inventory:
- `data/c4_train.jsonl`: 393 docs; calibration: 20; C4 test: 550; BookSum: 450.
- 19 shipped verifiers: K4 M={8,12,16,24,32,48}; K8 and K16 M={8,12,16,24,32};
  sentence M={8,16,48}. Recount files and records, hash source inputs.
- Published `key/secret_key.json` is a scientific watermark key for reproduction,
  distinct from account credentials. Preserve its bits and document IDs.
- OPT-1.3B generator, gte-modernbert-base embedder, Qwen2.5-7B-Instruct PPL judge.
- Default K4/M32, 300 tokens, 64 candidates, temperature .7, top-p .95,
  generation seed 43. Training pool seeds 42/chunk, 43/sentence. Preserve all
  source defaults, calibration, filters, tokenizer and sampling conventions.
- Environment pins Python3.11, torch2.6.0, vllm0.8.5.post1, transformers4.55.2,
  sentence-transformers5.1.0, sklearn1.7.1, scipy1.16.1, numpy1.26.4,
  nltk3.9.1, openai1.100.1, pytest9.1.1. Check compatibility rather than silently
  upgrading the scientific stack. Record any necessary deviation and its effect.

## First actions: guardian before long experiments

1. Read controller `CLAUDE.md`, `README.md`, `docs/RALPH_DISCORD_RELEASE.md` and
   `docs/DISCORD_RELEASE_REVIEW.md`. Inspect live jobs/leases before touching a VM.
   Preserve the dirty controller and task history; tasks 30-33 are not this campaign.
2. Start a real persistent supervisor and a dedicated Claude guardian. Run its
   first review immediately, then every 900 seconds. Store PID/start identity,
   native guardian session/model, first completed review, `last_review_at`,
   `next_review_at`, decisions and a restart/handover command under the campaign.
   Expose `guardian_receipt.json` so the handing-off assistant can verify readiness.
3. Guardian receipt must prove an actual model review, not merely a timer/PID.
   Include the supervisor PID, guardian native ID and actual model, schedule,
   first review decision and timestamp, next due time and evidence paths. Commit
   these atomically. A shell sleeping forever is not a human-proxy agent.
4. Code stays alive between reviews; invoke/resume the guardian at each tick.
   Guardian diagnoses independently while operator works, then sends decisions;
   dispatch operators in separately supervised processes so a long operator turn
   cannot block the 900-second guardian cadence. Prove one executed Ralph item
   and verify its artifact, beyond a native acknowledgement, before handover.
   avoid concurrent edits. Persist decisions before applying them and reconcile
   ambiguous dispatches. Use a singleton lease and never replay a job with an
   unknown outcome. No hard 30-minute kill for healthy computation.
5. Survive CLI exits with durable state. Restart the supervisor through an available
   supported service mechanism; document host/VM sleep/reboot limitations honestly.
   Before long GPU work, place a detached cloud supervisor/checkpoint/export loop
   alongside it so a disconnected local CLI does not kill the computation.
6. Review both code liveness and scientific progress: last completed doc/step,
   checkpoint/artifact changes, process start identity, GPU usage, logs, exceptions,
   remaining budget, native session and permissions. A heartbeat alone is not work.
   If stalled, ask the suspected agent for a nonce-bearing check-in, consult a
   separate bounded diagnostic agent, then retry/resume at a verified checkpoint
   when justified. Record missed ticks; do not report unattended uptime as proven.
7. Launch/reuse genuine Opus, Sonnet and AGY roles, prove communication, then run
   CPU tests and one small A100 acceptance before scheduling full cohorts.

## Ralph research/execution queue

Planner creates `experiment_matrix.json` with stable experiment IDs, priority,
source of each setting, expected doc IDs/counts, seeds, verifier provenance,
required positive/negative cohorts, dependencies, output paths and state.
Use a fixed primary matrix denominator; record later extensions separately.
The initial two-item queue (inventory, CPU feasibility) is bootstrap work only.
Before it is exhausted, persist the next bounded items covering role readiness,
manifest execution, checkpoint equivalence, A100 acceptance and actual benchmark
cohorts. Refill/decompose the queue using the Opus research handover whenever
needed. An empty bootstrap queue is not campaign completion and must not lead to
indefinite WAIT. Include remaining mission and blockers in guardian evidence.

| Priority | Work | Completion evidence |
|---|---|---|
| P0 | CPU tests, environment/model downloads, verified A100, tiny end-to-end smoke | Native/tool receipts, actual GPU name/memory, raw generation/detection, independent check |
| P1 | C4 K4/M32 SMA, all 550 docs + matched unwatermarked negatives; clean detection, word-D/S .3, PPL, rep4/pool health | Full doc-ID coverage, raw artifacts, independently recomputed metrics |
| P2 | BookSum K4/M32, all 450 docs, paired negatives/attacks/evaluation | Same checks; keep domain transfer separate |
| P3 | Expand across shipped K/M/sentence settings; Hamming vs SMA and hard vs soft decoding | Protocol-backed matrix, same prompts/cohorts, explicit compute coverage |
| P4 | Rebuild training/calibration pools and train verifiers; repeat critical measured settings | Separate from shipped-verifier reproduction, train counts/calibration/hash evidence |
| P5 | Additional supported attack strengths/seeds and paraphrase when authorized credentials/budget exist | Predeclared settings, cost/skip accounting, equal treatment of positives/negatives |

Prioritize complete representative cohorts over many tiny unlabelled partial runs.
Probe throughput and remaining Colab budget to decide order. If full training is
expensive, retain shipped-verifier results and report training reproduction partial.
Do not claim these proposed priorities are exact paper tables. Opus must trace any
paper-level claim to an actual available source. User's aim is breadth with validity.

Repo script entrypoints, from target root (inspect `--help` before use):

```bash
python -m pytest -q tests
python -c "import nltk; [nltk.download(p) for p in ('punkt_tab', 'wordnet')]"
python scripts/generate.py --verifier verifiers/chunk_K4_M32.npz --split c4_test --cache_dir OUT/pools --output OUT/generated.jsonl
python scripts/generate_unwatermarked.py --split c4_test --output OUT/negative.jsonl
python scripts/detect.py --verifier verifiers/chunk_K4_M32.npz --input OUT/generated.jsonl --output OUT/detect.json
python scripts/detect.py --verifier verifiers/chunk_K4_M32.npz --input OUT/negative.jsonl --output OUT/detect_negative.json
python scripts/attack.py --attack word_d --ratio .3 --input OUT/generated.jsonl --output OUT/word_d_03.jsonl
python scripts/perplexity.py --input OUT/generated.jsonl --output OUT/perplexity.jsonl
python scripts/evaluate.py --generated OUT/generated.jsonl --perplexity OUT/perplexity.jsonl --detect OUT/detect.json --negatives OUT/detect_negative.json
```

Apply each attack to both cohorts, detect both, pair evaluation files in the same
order. Keep raw unfiltered results primary; separately label `--rep4_max .2` or
other subset results and show retained N. Keep zero-unit/failed/skipped docs in
coverage accounting even when the source metric excludes them. PPL is continuation
given prompt under the specified judge; report aggregation and valid N.

## Reliability and scientific validity gates

`generate.py` accumulates records and writes only after the final doc. Its per-step
candidate cache helps exact recovery but does not provide durable completed-doc
output. Before a long run implement a campaign-owned append/atomic completion
wrapper or reviewed patch; retain original IDs/order/seeds and document-level
records, fsync/atomic manifests and idempotent recovery. Compare uninterrupted
versus interrupted/resumed execution on a small cohort before scaling. Do not
blindly shard stochastic scripts: word-D uses one RNG stream per whole file and
unwatermarked batching can affect generation. Prove equivalence or declare a
separate variant. Export checkpoints outside ephemeral `/content` regularly.

Validator checks source/input/model/verifier hashes, leakage boundaries, message
derivation, tokenizer and unit reconstruction, actual output IDs/counts/duplicates,
negative construction, attack parity/seeds, missing/failed units, metric definitions,
PPL/rep4 filtering and cache provenance. Recompute from raw data, not supplied
aggregate summaries. Give PASS/PARTIAL/FAIL/UNVERIFIED with evidence. Permission
denials or successful job exit do not imply completed experiments.

Metrics: BER, BRR, exact match; zero-bit AUC and TPR@1%/5%FPR using -soft BER;
PPL, rep4 and pool health with cohort counts. Do not tune against test data or
silently change defaults to match a number. Separate bug fixes and protocol
variants, pilot and full cohorts, shipped and retrained verifiers, old and fresh
results. No synthetic success, fabricated measurements, or reuse of old task #33
aggregates as this run's results.

Use explicit `WAITING_RESOURCE`, `WAITING_AUTH`, `RUNNING`, `UNKNOWN`,
`PARTIAL`, `VERIFIED` states. Retry resource checks with bounded backoff; never
reset an active job to probe A100. Stop/release idle GPU when there is no runnable
GPU work, retaining the guardian on a cheaper available host. Do not free a lease
until actual remote process/runtime state is reconciled. Budget limits should
checkpoint at a safe boundary and requeue remaining work, not declare failure.

## Existing implementation: reuse with honest limits

Host Python is `.conda/python.exe`. Read `core/remote_claude.py`,
`scripts/remote_claude.py`, `scripts/remote_claude_worker.py`, `core/remote_agent.py`,
`core/colab_runtime.py`, `core/resource_gate.py` before changing remote dispatch.
The existing Claude runner ONLY implements `smoke` and a hardcoded two-role CPU
`pair` pilot. Extend/test a real project manifest runner before using it for SMILE.
It does not already run arbitrary repos. Do not rerun that completed pilot.

Previous CPU evidence: `storage/remote-claude/claude-pair-20261107-1/verification.json`;
two genuine Claude sessions, 6 native SendMessage calls, 13 responses, independent
5/5 repaired checks, 37.476 seconds. No A100 research reproduction was demonstrated.
Previous W&B publication was post-run verification, not live experiment tracking.
All live account/runtime state must be checked again.

`CLAUDE_CODE_OAUTH_TOKEN` is user-provisioned in env; load through controller
`load_dotenv` without showing it to a model/log/notebook. Existing encrypted remote
injection uses ephemeral RSA-OAEP/AES-GCM and memory/pipe delivery. Reuse it; never
dump environment, copy credential caches indiscriminately, or export auth files.
Avoid Claude `--bare`, which excludes OAuth. Keep credentials out of artifacts.

One managed personal Colab runtime, including CPU actions. Reuse authenticated
bridge/resource fencing. Available skill `.agents/skills/colab-coccoc-playwright/`
documents `ensure-browser`, `auto-confirm --connect`, `set-runtime --hardware A100`.
Changing runtime replaces the VM; export state first and rebuild/authenticate
native Claude after boot. Remote AGY auth might not survive. No public tunnel.
Rented GPU adapters in this controller are contracts, not working provisioners.

Useful existing pieces: `core/experiment_progress.py` (local/W&B receipts),
`core/supervision.py` (nonce check-in and diagnostic), `core/monitoring.py`,
`core/role_memory.py` (native memory/rotation), `scripts/verify_remote_claude.py`.
Existing scheduler's frequent code poll is not itself a 15-minute human proxy.
Inspect reference implementation under `archive/ref/agentic_workflow/Work/lab/lab`:
`agents.py`, `cli.py`, `bin/labrun`. Borrow detached jobs, durable identities,
status/progress/heartbeat and independent checks; do not assume every ref agent
already runs on cloud or copy any secrets from its key handling.

## Live reports, acceptance and handover

Live initial handover sessions (2026-11-08 local date): Sonnet coordinator
`117e8b7d-e4d0-4ba7-a42f-b99d0d065f1a` (`claude attach 117e8b7d`), guardian
`73128194-acfa-4424-9b5b-c041f4bc94c9`, and action operator
`29308ec0-3e76-4267-811d-8b8b920c6faa`. Reconcile current process identities from
receipts; do not assume a historical PID is still running. The coordinator's
isolated worktree is `.claude/worktrees/smile-campaign`; ignored campaign data and
host Python remain at the original controller root.

A genuine read-only Opus planner already inspected the handover and source:
session `ad0cf789-b320-464b-9969-9e1446fb55b4`, actual model `claude-opus-5-5`,
no permission denials. Read its protocol and risks from campaign
`planner_opus.raw.json` (the `result` field). Resume/reuse that role session
serially rather than treating a separate Opus nonce smoke as completed research.

Publish `campaign_status.json` and a concise Vietnamese `status_vi.md` on every
checkpoint and guardian review. Include current role/native model/session,
experiment and remote job IDs, A100 state, completed/expected documents and matrix
rows, queued/blocked work, latest progress, guardian last/next review, active issue
and autonomous decision, artifact locations and validator verdict.

Track separately: campaign elapsed time; VM allocated A100 time; productive GPU
job wall time; setup/download/idle/retry time; per-agent native active/elapsed time
and aggregate agent time. Overlapping agent durations must not be described as
campaign wall time. Do not equate provider cost estimates with billed money.

Add live W&B progress through the existing authenticated integration; log counts,
experiment IDs, rates, metrics and verified artifact manifests without secrets.
Persist offline on connectivity loss and synchronize idempotently. Include actual
run URL and last sync in status; never present a post-run upload as live tracking.
If login is genuinely missing, send the requested Discord login notice via the
existing project channel/context, then continue local receipts and other work.

The handing-off assistant may stop after verifying `guardian_receipt.json` against
the live supervisor and real first Claude review. This is acceptance of autonomous
supervision, not a claim of completed benchmarks. Continue the campaign afterward.
When exhausted/completed, export raw data, metric recomputation and timing ledger,
release GPU and produce a Vietnamese summary of what fraction was reproduced,
which numbers are comparable, remaining gaps and material threats to objectivity.
