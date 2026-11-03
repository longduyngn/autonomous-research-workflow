# Autonomous research workflow

Give a coding or research task to a small team of headless agents. The controller
queues requests, creates isolated workspaces, and runs **Research → Execute →
Check → Report**. Failed checks trigger a bounded repair loop. The researcher
chooses whether the task needs milestones; simple work stays simple.

Every role can use a different CLI or API model. Antigravity, Codex and Claude
Code support native workflow hooks. OpenCode supports named JSON sessions;
OpenRouter and compatible APIs use the controller's tool loop.

Roles keep separate task-scoped disk histories and use explicit native session
resume when an ID is available. Provider/instruction changes rotate that cache;
handoffs and current evidence remain authoritative. For VM-loss recovery see
[computation checkpoints](docs/CHECKPOINTS.md); training must save its own state
to a configured durable destination. Implementation progress is tracked in
[the current Ralph checklist](docs/RALPH_RESUMABLE_20261103.md).

## Start in five minutes

Use **Python 3.11+** (3.11 is tested), Git, and an installed/logged-in agent CLI. In PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock.txt
python main.py --no-env doctor
python main.py --no-env run "Check the workflow" --dry-run
```

For Antigravity root hooks, run `python scripts/setup_hooks.py --apply --check`
using the workflow environment. Setup binds launchers to the current checkout and
interpreter, preserves policy, and backs up the original config. Rerun after moving
the checkout or replacing the environment. This avoids relying on the hook's cwd.

On Linux/macOS, activate with `source .venv/bin/activate`. A dry run tests the
controller using mock agents; it is not a real experiment. Existing checkouts
keep their settings. For a fresh installation, copy
`config/workflow_config.example.json` to `config/workflow_config.json`; the
example leaves messaging/watchers off. Credentials belong in environment variables
or an untracked `.env`. `--no-env` skips loading those files.

Check a real agent, then submit work:

```powershell
python main.py verify --provider codex_cli
python main.py run "Implement and test the requested change" --provider codex_cli --source C:\path\to\project
python main.py run "Solve the assignment using the supplied data" --input C:\path\to\paper.pdf --input C:\path\to\data.json
```

`--source` snapshots a project; without it, this checkout is the seed. Explicit
inputs appear under `inputs/` in the task workspace. Originals stay untouched.
Results stay in `storage/workspaces/task_00001/`; nothing automatically merges.

## Follow the work

```powershell
python main.py dashboard
python main.py status
python main.py task 1
```

Open **http://127.0.0.1:8787** for stages, verified milestones, attempts, checks and
named agent transcripts. This read-only view starts no workers. Two tasks run
concurrently; later requests queue. The configurable hard maximum is three.

For background work, use `enqueue` plus `daemon`. For Discord, set
`DISCORD_BOT_TOKEN` and `DISCORD_ALLOWED_USER_IDS`, then run
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
- [Architecture, Ralph loop and sophisticated tasks](docs/ARCHITECTURE.md)
- [Cloud, operation and recovery](docs/OPERATIONS.md)
- [Audit findings and verification](docs/READINESS.md)
- [Seven proposals: implementation and limits](docs/PROPOSAL_STATUS_20261103.md)
- [Current Ralph checklist](docs/RALPH_RESUMABLE_20261103.md)
- [Future rented compute adapters](docs/COMPUTE_EXTENSIONS.md)

This is a local workflow for trusted code, not a multi-tenant execution service.
Snapshots prevent file conflicts; they are not an OS sandbox. Model access and
cloud quotas depend on your accounts. Run tests with
`python -m unittest discover -s tests -v`. Historical reports/experiments are
separate under `archive/`; persistent task history stays under `storage/`.
