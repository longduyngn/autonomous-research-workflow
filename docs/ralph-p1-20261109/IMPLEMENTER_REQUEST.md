# Codex Sol implementation request

Read PLAN_VI.md and CLAUDE.md; follow Ralph inspect→small edit→test→AGY review.
You are the implementation worker, gpt-6.1-sol medium. Root is coordinator.

Work ONLY in D:/Antigravity_playgournd/storage/ralph-p1-20261109/worker and evidence
under storage/ralph-p1-20261109/implementation. The snapshot includes current dirty
quota routing work. Do not edit root source or .git, do not deploy/restart listener,
call GPU/cloud/provider/Discord, read secrets or run new agents. Tests use the root
.conda/python.exe with your snapshot as cwd. Preserve quota code; coordinate any
necessary integration seams rather than rewriting core/quota*.py.

Start by inspect and propose concrete R1 file scope/acceptance. Then implement R1:
persistent communicator can return structured natural-language intent to submit
authorized task/steer feedback/status/read bounded evidence. Controller commits
only validated intent with exact actor/scope/task identity and intake dedup. User
explicitly authorized communicator to submit task on their behalf. Do not make
user confirm every clear request. Ask for target only if ambiguous. Preserve
legacy !commands, replies, approvals, original request/history and current quota
routing. Persistence does not replace idempotency. Model output is not authority.
Avoid an extra model call when existing communicator can return intent+reply.

Tests must include clear natural-language submit, repeated event, concurrent intake
or durable claim equivalent, actor/channel isolation, ambiguous target, feedback
same task, malformed/failed model output cannot mutate, bounded log data not secrets.
Write patch/evidence/status R1_READY_FOR_REVIEW; message coordinator. Do not start
R2 or integrate until root returns AGY verdict.

Next (only after follow-up): R2 watcher stable recovery loop, known safe repair
actions or operator/researcher review at role boundary, incident dedup/bounded
budget, validation after repair, retain job identity when telemetry/side effects
unknown, no repeated LLM polling. Reuse core/supervision.py/core/monitoring.py.
Review current code before proposing changes: some routing and protections exist.
Prompt updates should explain responsibilities, handoff and evidence; code must
enforce state and identity. Do not force universal limits into research protocol.

Keep concise on-disk status and commands/exit receipts. If blocked, finish
independent local work and report exact blocker. No approval bypass or silent
model substitution. No touching unrelated dirty changes.
