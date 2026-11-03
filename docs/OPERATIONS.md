# Operation and recovery

`run` starts workers and waits for its request; it can also process queued work.
`enqueue` only submits. `daemon` runs workers, enabled watchers and authenticated
webhooks. `discord-bot` includes its own workers. `dashboard` starts no workers.
Use one Discord listener. Restart processes after configuration/source changes.

Defaults: two concurrent tasks (maximum three), three total attempts per item,
one researcher replan after two failures, 86400 seconds per task. Set
`max_replans: 0` to disable replanning. Replanning never resets the attempt/time
budget. Ctrl+C stops local children; submitted remote jobs may continue.

## Inputs and transcripts

`--source` snapshots a project; `--input` supplies explicit files under task
`inputs/`. Discord downloads attachments from authorized task messages. Inputs
are limited to 50 MB total; duplicate/traversal/credential filenames are rejected.
Ingress never extracts or executes attachments. Retries retain the same hashed
inputs. Reference large datasets explicitly rather than copying them into prompts.

Headless runs are grouped by task/step/role/attempt, with original prompts,
streamed stdout/stderr and final receipts. Codex/OpenCode IDs are retained when
emitted. OpenCode also receives the run name as its native session title. API
tool traces are available at completion; API calls are not streamed here.

The dashboard binds only to `127.0.0.1`, rejects foreign Host/Origin requests and
has no mutation API. It displays local task content/logs, not an arbitrary file
browser. Keep it local; remote-user authentication is not implemented. The
list shows the most recent 200 tasks; CLI `task ID` can inspect older tasks.

## Messaging

Discord requires `DISCORD_BOT_TOKEN`, `DISCORD_ALLOWED_USER_IDS` and Message
Content intent enabled in Discord. `DISCORD_CHANNEL_ID` is optional. Supported
commands are `!task ...`, `!status`, `!retry ID`. The outbox retries delivery;
a crash after send but before acknowledgement can duplicate a message.

Generic webhooks require a bearer token. Zalo/Messenger routes require an
authenticated forwarding service; direct vendor-signature verification is not
implemented. Watchers and their automatic task creation/self-healing are optional.
The portable example configuration disables these optional services.

## Notebook execution

Local notebooks run in one persistent Jupyter kernel, preserve outputs, stop on
cell failure and shut down the kernel. All shell/API execution is trusted code
under your account; file snapshots are not an OS sandbox.

Personal Colab requires `backend: colab`, an authenticated loopback bridge and a
signed-in browser. The controller prepares the bridge before starting the
operator. This checkout can use its stable local MCP runtime; new installs use
the revision-pinned `uvx` bootstrap with Python 3.13. Download/bootstrap is needed
only when explicitly using Colab. The controller itself needs Python 3.11+.
The bundled browser helper currently targets Windows/Cốc Cốc with CDP. Other
browsers/platforms need adapter work; this is not a portable VM provisioning API.

Discover schemas through `scripts/cloud.py --workspace PATH list` and then
`colab_tools` after connecting. Hardware metadata is intent. Unavailable requested
hardware fails explicitly. Save real outputs, disconnect unused compute, and
verify stopped-runtime and zero-active-session evidence. Bridge release alone
does not stop a VM. A personal browser connection is a serialized shared resource.

Kaggle uses batch submission, durable IDs, polling and artifact download.
`WAIT_FOR_JOB: ID` avoids model polling; that task still holds a worker slot.
Uncertain submissions are not automatically repeated. Use datasets for large
inputs. Quotas/hardware access belong to the account/platform. Cancellation is an
explicit operation, never an incidental cleanup action.

## Maintenance

- Inspect `task ID`, logs, checks and actual output files before recovery.
- `!retry ID` creates linked recovery work and preserves the previous outcome.
- `archive-task ID` moves an unowned failed workspace with hashes; unresolved
  remote state or locked files prevent the move.
- Back up SQLite using its backup API, or stop workers before copying DB/WAL
  state. Keep logs, workspaces and input snapshots with the DB for recovery.
- `archive/legacy-20261102/` contains old reports, notebooks and manual scripts
  with a move/hash manifest. It is excluded from snapshots and Git by default.
- One legacy controller Kaggle record still says `running`; cleanup did not
  query or cancel that job. Inspect it before reusing the job.

Run `python -m unittest discover -s tests -v` and `python -m pip check` after
changes. Live provider checks are opt-in `main.py verify`; normal tests never
call models, send messages or allocate GPUs.
