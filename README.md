# Autonomous research workflow

Give a coding or research task to a small team of headless agents. The controller
queues requests, creates isolated workspaces, and runs **Research → Execute →
Check → Report**. Failed checks trigger a bounded repair loop. The planner
chooses whether the task needs milestones; simple work stays simple.

Every role can use a different CLI or API model. Antigravity, Codex and Claude
Code support native workflow hooks. OpenCode supports named JSON sessions;
OpenRouter and compatible APIs use the controller's tool loop.

The **planner** makes a plan, the **operator** runs commands and changes files,
the **validator** checks the result, and the **communicator** reports it. Each
task gets its own workspace and role history. You can start with one provider
for every role and configure different models later.

## Install and check the setup

Use **Python 3.11+** (3.11 is tested), Git, and an installed/logged-in agent CLI.
Run these commands from the repository directory in PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock.txt
if (!(Test-Path config/workflow_config.json)) {
    Copy-Item config/workflow_config.example.json config/workflow_config.json
}
python main.py --no-env doctor
python main.py --no-env run "Check the workflow" --dry-run
```

On Linux/macOS, activate with `source .venv/bin/activate` and copy the example
configuration with `cp -n config/workflow_config.example.json config/workflow_config.json`.
The example leaves messaging and watchers off.

`doctor` reports providers, executables and dependencies without calling models.
The dry run exercises the controller with mock agents. A successful dry run should
finish with a completed task; real model access is checked in the next step.

Credentials belong in environment variables or an untracked `.env`. `--no-env`
skips loading `.env` files; omit it when you want to use credentials from them.

## Choose an agent and run your first task

Select the CLI you have installed and logged into. This example uses Codex:

```powershell
python main.py use-cli codex
python main.py verify --provider codex_cli
python main.py run "Implement and test the requested change" --provider codex_cli --source C:\path\to\project
```

Replace the prompt and project path with your own. `use-cli` sets all role defaults;
its other choices are `antigravity`, `claude` and `opencode`. `verify` runs a real
local smoke task in a separate database and uses your model account. For OpenCode
or mixed CLI/API roles, follow [provider configuration](docs/PROVIDERS.md).

For Antigravity root hooks, run `python scripts/setup_hooks.py --apply --check`
from the activated environment before verification. Setup binds launchers to this
checkout and interpreter, preserves policy, and backs up the original config.
Rerun after moving the checkout or replacing the environment.

You can supply documents and data separately:

```powershell
python main.py run "Analyze inputs/paper.pdf using inputs/data.json; save a report with checks" --input C:\path\to\paper.pdf --input C:\path\to\data.json
```

`--source` snapshots a project; without it, this checkout is the seed. `--input`
copies explicit files under `inputs/` in the task workspace. Originals stay
untouched. Include the desired output and how it should be checked in your prompt.

Results stay in `storage/workspaces/task_00001/` (using the assigned task ID).
Review the output there before copying changes back into your project; nothing
automatically merges. `--provider` overrides every role for that task. Omit it to
use your configured role defaults.

## Follow the work

```powershell
python main.py dashboard
python main.py status
python main.py task 1
```

Open **http://127.0.0.1:8787** for stages, verified milestones, attempts, checks and
named agent transcripts. This read-only view starts no workers. Two tasks run
concurrently; later requests queue. The configurable hard maximum is three.

For queued background work, submit a task and start the worker process:

```powershell
python main.py enqueue "Implement the change and run the project checks" --source C:\path\to\project
python main.py daemon
```

`enqueue` returns without starting work; `daemon` processes the queue. Keep the
worker running while tasks execute. Configuration changes require a worker restart.

## Use Discord (optional)

Set `DISCORD_BOT_TOKEN` and `DISCORD_ALLOWED_USER_IDS` in your environment or
untracked `.env`, and enable Message Content intent for the bot in Discord. Run
`python main.py discord-bot`. Send `!task ...` with optional attachments,
`!status`, or `!retry 12`. The listener includes workers; one listener is enough.
Run `python scripts/setup_discord.py --apply-env` to discover the existing
antigravity_playground category and safely synchronize only its channel-ID keys.
The three channels are task-proposal (chat/clarification and !task), approval list
(approval requests/decisions only), and task log (status/search on request).
DMs/mentions are conversation, not automatic task submissions. Reply to a task
message with feedback or use `!feedback <id> <change>`. Role-boundary updates,
worker-free waits and exact-action approvals are explained in the
[resumable workflow guide](docs/RESUMABLE_WORKFLOW.md).

## Configure and extend

- [Mix CLI/API models by role](docs/PROVIDERS.md)
- [Quota-aware routing across Codex, AGY and Claude Code (opt-in)](docs/QUOTA_ROUTING.md)
- [Architecture, Ralph loop and sophisticated tasks](docs/ARCHITECTURE.md)
- [Cloud, operation and recovery](docs/OPERATIONS.md)
- [Notebook execution and cloud setup](docs/OPERATIONS.md#notebook-execution)
- [Save and restore computation checkpoints](docs/CHECKPOINTS.md)
- [Waits, approvals, feedback and session recovery](docs/RESUMABLE_WORKFLOW.md)
- [W&B progress and recoverable stall supervision](docs/PROGRESS_MONITORING.md)
- [Reusable local AGY manager and Colab worker (opt-in)](docs/REMOTE_AGY.md)
- [Cloud Claude operator/validator CPU pilot and authentication](docs/REMOTE_AUTH.md#actual-claude-acceptance)
- [Future rented compute adapters](docs/COMPUTE_EXTENSIONS.md)

Start with local tasks before configuring Colab or Kaggle. Colab needs a signed-in
browser and authenticated bridge; its browser helper currently targets Windows
and Cốc Cốc. One personal Colab runtime is shared across tasks. Training code must
save model, optimizer, RNG and progress state to a configured durable destination
to resume after runtime loss.

If work stops, inspect `python main.py task ID` and `python main.py logs ID` for
the failing role, check output or wait reason. Resolve missing CLI/login/model
access before retrying. Inspect existing remote jobs before resubmitting them.

## Development and local files

Run `python -m unittest discover -s tests -v` after code changes. Tests use local
fixtures; the explicit `verify` command checks real model access.

This is a local workflow for trusted code, not a multi-tenant execution service.
Snapshots prevent file conflicts; they are not an OS sandbox. Model access and
cloud quotas depend on your accounts. Historical reports/experiments are
separate under `archive/`; persistent task history stays under `storage/`.
