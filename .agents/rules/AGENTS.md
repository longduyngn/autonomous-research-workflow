# Antigravity Autonomous Workspace Rules

This workspace runs a hybrid event-driven multi-agent system where each agent is spawned ephemerally for a single role (`researcher`, `operator`, `validator`, `communicator`, `workspace_watcher`, `world_watcher`) and shut down immediately upon completing its step.

## Core Guidelines for Ephemeral Agents
1. **Strict Role Scoping**: Follow the instructions injected from `roles/<role>.md`. Do not perform another role's job.
2. **Protect System Infrastructure**: Unless explicitly instructed by the user's task, do not delete or corrupt `core/`, `watchers/`, `gateways/`, `config/workflow_config.json`, or `storage/events.db`.
3. **Validator Verdict Contract**: When running as the `validator` role, always include `VERDICT: PASS` or `VERDICT: FAIL` clearly in your output.
4. **Concise Context Output**: Your stdout is captured and appended to `storage/logs/task_<id>.log` and `storage/events.db`. Keep outputs structured and high-signal.
