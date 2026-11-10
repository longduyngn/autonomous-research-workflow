# Workflow maintainer entry point

Read README.md, docs/RALPH_DISCORD_RELEASE.md and docs/DISCORD_RELEASE_REVIEW.md.
Real user-origin Discord status/approval acceptance for probe #29 is verified.
Duplicate approval confirmation was reproduced as an immediate-handler/heartbeat
race; DB delivery claims now serialize both paths. See docs/DISCORD_DELIVERY_FIX.md.
Saved native-provider execution receipts are reviewed in
docs/ralph-next/VERIFICATION_20261104.md; current account access requires a live check.

## Current contract

- Exactly three Discord channels: task-proposal for conversation/clarification and
  !task; approval list only for decisions; task log only for requested progress.
- Communicator memory is per authenticated user/guild across tasks. Planner,
  operator and validator memory is per task/role. Native IDs are caches; current
  DB facts and on-disk handoffs remain authoritative. Rotation writes handover.
- Two task slots, maximum configurable three. Sequential Ralph within a task;
  milestones optional. One managed personal-Colab runtime, including CPU actions.
- Real CLI sessions can require reload after configuration changes. Earlier AGY
  auth/timeout and Codex nested-tool failures have later successful probe receipts.
  Claude Code is installed locally; a two-role CPU Colab pilot has live evidence
  under storage/remote-claude/. Saved probes do not establish current account readiness.

## Maintaining source

Use small Ralph iterations: inspect, implement one item, verify, record and continue.
Preserve original requests, task history, failed observations and user changes.
Do not inspect/print credentials or secret env content. The user authorized safe
channel-ID env updates only; use scripts/setup_discord.py, which preserves secrets.
Root .agents/.codex/.git permissions are environment-owned. Hook setup changes only
launcher bindings and preserves policy. Never bypass genuine permissions via MCP.

Task bundles have their own scoped instructions; they cannot edit the controller.
core/orchestrator.py owns role transitions and checks; database/scheduler/control
own leases, waits and delivery. conversation.py owns intake; providers.py and
provider_registry.py own adapters. role_memory.py owns cache/rotation. cloud.py,
resource_gate.py and colab_bridge.py own managed cloud operations. Rented adapters
are extension contracts, not implemented vendor provisioning.

## Verification

Python 3.11+, local interpreter .conda/python.exe:

    python -m unittest discover -s tests -v
    python main.py --no-env doctor
    git diff --check

Latest verification is recorded in docs/ralph-next/VERIFICATION_20261104.md.
Historical Docker build/doctor/dashboard receipts exist under storage/ralph-next/probes/I03.
Live GPU/account history remains in dated docs. Before replacing listeners, verify
no active tasks; never restart another experiment merely to perform acceptance.

Personal config is ignored; fresh clones use config/workflow_config.example.json.
Root hooks are generated with scripts/setup_hooks.py --apply --check after checkout
or environment relocation. No remote is configured, so local commits are not pushes.
