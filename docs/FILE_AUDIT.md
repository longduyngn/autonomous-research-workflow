# File audit coverage

95 maintained files inventoried individually. `.env*` / `*.env` files were excluded without opening. Installed environments, dependency caches, Git internals, IDE metadata, generated runtime trees and historical task snapshots are outside this source inventory. Relevant runtime ownership/history was inspected separately.

Hashes/parse classifications: [audit-inventory.json](audit-inventory.json). Behavioral evidence and scope: [READINESS.md](READINESS.md). Hashing does not prove correctness. Archived material has a separate move manifest.

| File | Purpose | Structural check |
|---|---|---|
| `.gitignore` | Entrypoint/setup | Text reviewed |
| `README.md` | Entrypoint/setup | Text reviewed |
| `main.py` | Entrypoint/setup | Python parsed |
| `requirements.lock.txt` | Entrypoint/setup | Text reviewed |
| `requirements.txt` | Entrypoint/setup | Text reviewed |
| `.agents/hooks.json` | Active native hooks/skills/plugins | JSON parsed |
| `.agents/plugins/ck/hooks.json` | Active native hooks/skills/plugins | JSON parsed |
| `.agents/plugins/ck/mcp_config.json` | Active native hooks/skills/plugins | JSON parsed |
| `.agents/plugins/ck/plugin.json` | Active native hooks/skills/plugins | JSON parsed |
| `.agents/plugins/ck/skills/colab-kaggle-workflow/SKILL.md` | Active native hooks/skills/plugins | Text reviewed |
| `.agents/rules/AGENTS.md` | Active native hooks/skills/plugins | Text reviewed |
| `.agents/skills/colab-coccoc-playwright/SKILL.md` | Active native hooks/skills/plugins | Text reviewed |
| `.agents/skills/colab-coccoc-playwright/scripts/colab_playwright.py` | Active native hooks/skills/plugins | Python parsed |
| `.agents/skills/kaggle-playwright-cancel/SKILL.md` | Active native hooks/skills/plugins | Text reviewed |
| `.agents/skills/kaggle-playwright-cancel/scripts/kaggle_cancel.py` | Active native hooks/skills/plugins | Python parsed |
| `.github/workflows/tests.yml` | CI | Text reviewed |
| `.workflow/cloud_jobs/62e32435f09d4ebfa7347a1fb85d70c1/job.json` | Entrypoint/setup | JSON parsed |
| `.workflow/cloud_jobs/62e32435f09d4ebfa7347a1fb85d70c1/stage/hello_colab_cpu.ipynb` | Entrypoint/setup | Text reviewed |
| `.workflow/cloud_jobs/62e32435f09d4ebfa7347a1fb85d70c1/stage/kernel-metadata.json` | Entrypoint/setup | JSON parsed |
| `.workflow/cloud_jobs/9fbd0cdd093347889118894598be3bb8/job.json` | Entrypoint/setup | JSON parsed |
| `.workflow/cloud_jobs/9fbd0cdd093347889118894598be3bb8/stage/ddpm-git.ipynb` | Entrypoint/setup | Text reviewed |
| `.workflow/cloud_jobs/9fbd0cdd093347889118894598be3bb8/stage/kernel-metadata.json` | Entrypoint/setup | JSON parsed |
| `config/workflow_config.example.json` | Nonsecret settings | JSON parsed |
| `config/workflow_config.json` | Nonsecret settings | JSON parsed |
| `core/__init__.py` | Controller/runtime | Python parsed |
| `core/cloud.py` | Controller/runtime | Python parsed |
| `core/colab_bridge.py` | Controller/runtime | Python parsed |
| `core/colab_runtime.py` | Controller/runtime | Python parsed |
| `core/configuration.py` | Controller/runtime | Python parsed |
| `core/dashboard.py` | Controller/runtime | Python parsed |
| `core/database.py` | Controller/runtime | Python parsed |
| `core/diagnostics.py` | Controller/runtime | Python parsed |
| `core/executables.py` | Controller/runtime | Python parsed |
| `core/inputs.py` | Controller/runtime | Python parsed |
| `core/integrations.py` | Controller/runtime | Python parsed |
| `core/logger.py` | Controller/runtime | Python parsed |
| `core/maintenance.py` | Controller/runtime | Python parsed |
| `core/mcp_client.py` | Controller/runtime | Python parsed |
| `core/models.py` | Controller/runtime | Python parsed |
| `core/network.py` | Controller/runtime | Python parsed |
| `core/observability.py` | Controller/runtime | Python parsed |
| `core/orchestrator.py` | Controller/runtime | Python parsed |
| `core/plans.py` | Controller/runtime | Python parsed |
| `core/processes.py` | Controller/runtime | Python parsed |
| `core/providers.py` | Controller/runtime | Python parsed |
| `core/reports.py` | Controller/runtime | Python parsed |
| `core/scheduler.py` | Controller/runtime | Python parsed |
| `core/tools.py` | Controller/runtime | Python parsed |
| `core/workspaces.py` | Controller/runtime | Python parsed |
| `docs/ARCHITECTURE.md` | Current documentation | Text reviewed |
| `docs/OPERATIONS.md` | Current documentation | Text reviewed |
| `docs/PROVIDERS.md` | Current documentation | Text reviewed |
| `docs/RALPH_READINESS.md` | Current documentation | Text reviewed |
| `docs/READINESS.md` | Current documentation | Text reviewed |
| `docs/ROADMAP.md` | Current documentation | Text reviewed |
| `examples/research_task.md` | Example brief | Text reviewed |
| `gateways/__init__.py` | Messaging/ingress | Python parsed |
| `gateways/base.py` | Messaging/ingress | Python parsed |
| `gateways/discord_bot.py` | Messaging/ingress | Python parsed |
| `gateways/discord_gateway.py` | Messaging/ingress | Python parsed |
| `gateways/webhook_server.py` | Messaging/ingress | Python parsed |
| `gateways/zalo_messenger_gateway.py` | Messaging/ingress | Python parsed |
| `mcp_servers/notebook_cloud_mcp.py` | Tool protocol | Python parsed |
| `roles/communicator.md` | Agent instructions | Text reviewed |
| `roles/operator.md` | Agent instructions | Text reviewed |
| `roles/researcher.md` | Agent instructions | Text reviewed |
| `roles/validator.md` | Agent instructions | Text reviewed |
| `roles/workspace_watcher.md` | Agent instructions | Text reviewed |
| `roles/world_watcher.md` | Agent instructions | Text reviewed |
| `scripts/agent_hooks.py` | Operations/verification | Python parsed |
| `scripts/check_bot.py` | Operations/verification | Python parsed |
| `scripts/cloud.py` | Operations/verification | Python parsed |
| `scripts/cloud_hooks.py` | Operations/verification | Python parsed |
| `scripts/colab_timed_probe.py` | Operations/verification | Python parsed |
| `scripts/pre_tool_approval_hook.py` | Operations/verification | Python parsed |
| `scripts/setup_colab_runtime.py` | Operations/verification | Python parsed |
| `scripts/verify_workflow.py` | Operations/verification | Python parsed |
| `scripts/workspace_report.py` | Operations/verification | Python parsed |
| `tests/test_cloud.py` | Regression coverage | Python parsed |
| `tests/test_discord_recovery.py` | Regression coverage | Python parsed |
| `tests/test_execution.py` | Regression coverage | Python parsed |
| `tests/test_ingress_delivery.py` | Regression coverage | Python parsed |
| `tests/test_integrations.py` | Regression coverage | Python parsed |
| `tests/test_providers.py` | Regression coverage | Python parsed |
| `tests/test_readiness.py` | Regression coverage | Python parsed |
| `tests/test_recovery_evidence.py` | Regression coverage | Python parsed |
| `tests/test_scheduler.py` | Regression coverage | Python parsed |
| `tests/test_watchers.py` | Regression coverage | Python parsed |
| `tests/test_workflow.py` | Regression coverage | Python parsed |
| `ui/app.js` | Dashboard | Text reviewed |
| `ui/index.html` | Dashboard | Text reviewed |
| `ui/style.css` | Dashboard | Text reviewed |
| `watchers/__init__.py` | Optional monitors | Python parsed |
| `watchers/workspace_watcher.py` | Optional monitors | Python parsed |
| `watchers/world_watcher.py` | Optional monitors | Python parsed |
