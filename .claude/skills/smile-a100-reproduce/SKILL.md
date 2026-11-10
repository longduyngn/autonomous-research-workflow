---
name: smile-a100-reproduce
description: Execute or review the SMILE A100 reproduction campaign with bounded role sessions and evidence-based handoffs.
---

# SMILE campaign

Read the compact digest once per session:
`.conda/python.exe scripts/campaign.py --workspace . --campaign storage/campaigns/smile-pre-publish status_digest`
Do not reread this skill when it is already in context. Inspect only changed evidence.

Use one bounded Ralph item per role invocation: reconcile identity, act, verify, checkpoint, exit.
Current routing and activation evidence live in `role_routing.json`, `agent_roles.json`,
and native handover receipts. Historical session IDs are not proof of live agents.
Operator: Claude Sonnet; planner: Claude Opus; independent validator: AGY Argon;
coordinator/communicator/guardian: AGY Gemini 3.8. Preserve explicit user overrides.

Use deterministic helpers from the controller root:
- Artifact: `.conda/python.exe scripts/artifacts.py register PATH --workspace . --name NAME`
- Verify: `.conda/python.exe scripts/artifacts.py verify --workspace .`
- Logs: `.conda/python.exe scripts/logs.py tail_log PATH --limit 50` or `search_log PATH --pattern REGEX --limit 20`.
- Campaign updates: use the same `--workspace . --campaign storage/campaigns/smile-pre-publish` flags before `update-status --changes JSON` or `checkpoint`.
- Operator row export/bundle: `scripts/smile_operator.py --help`; reuse existing row helpers.

Respect leases and existing jobs; never restart or replay an unknown job. Use existing allocation only;
no new spending, credential access or permission bypass. Keep validator independent.
Execution rc0, artifact hashes and reviewer PASS do not establish scientific replication.
Preserve PARTIAL/P6 uncertainty and provenance; don't rerun completed rows to test workflow changes.

Read [campaign-details](references/campaign-details.md) only for the relevant section:
`Fixed inputs`, `Ralph research/execution queue`, `Reliability and scientific validity gates`,
`Existing implementation`, or `Live reports`. It preserves historical detail; current digest,
verified receipts and latest user instructions determine present work.
