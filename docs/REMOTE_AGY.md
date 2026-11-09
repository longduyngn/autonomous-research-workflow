# Local AGY manager and Colab worker

This is an opt-in two-host experiment. A local `remote_manager` role prepares
instructions, a Colab AGY worker executes a detached turn, and the same local role
reviews its receipt. Existing research tasks are not automatically migrated.

## Setup and login

Use the project interpreter and install `requirements.lock.txt`. Start Coc Coc
with the existing Colab CDP profile, then run from the controller checkout:

```powershell
.\.conda\python.exe scripts/remote_agy.py setup
.\.conda\python.exe scripts/remote_agy.py auth-check
```

The managed notebook must use CPU for this probe. `setup` uses the fenced personal
Colab bridge and preserves installed AGY; if missing, it downloads the installer
from Google's official site and records its SHA-256. No Windows credentials are
copied. The current account must expose the exact selected model; the default
is `gemini-3.8-flash-medium`. Use `--model` to explicitly select a different remote
model. Local role settings come from `roles.remote_manager`; the script uses AGY,
plan mode, sandbox enabled and inline text prompts for this management role.

For authentication tradeoffs across AGY, Codex and Claude, see
[REMOTE_AUTH.md](REMOTE_AUTH.md). Google OAuth authorization codes are one-time
inputs, not reusable API keys. The completed Colab login is reused by subsequent
CLI processes on that VM; it is not necessary to log in for every message.

If Colab reports `LOGIN_REQUIRED`:

```powershell
.\.conda\python.exe scripts/remote_agy.py login-start
.\.conda\python.exe scripts/remote_agy.py login-personal
.\.conda\python.exe scripts/remote_agy.py login-status
```

Open the returned official Google authorization URL on your own machine. Complete
the Google login/consent. Enter the returned code in a local interactive terminal:

```powershell
.\.conda\python.exe scripts/remote_agy.py login-code
.\.conda\python.exe scripts/remote_agy.py auth-check
```

The terminal hides input. An ephemeral RSA-OAEP key in the Colab kernel encrypts
the authorization code in transit through notebook cells; only ciphertext enters
cell source. Native credentials stay in AGY's own remote credential store. The
key is not saved. Do not paste credentials/codes into chat, source or Discord.
`INPUT_SENT` means only that input reached the interactive CLI; verify
`AUTHENTICATED` separately. Colab keyring persistence across VM replacement is not
promised. A fresh VM can require a fresh login.

After authentication, `login-stop` closes the onboarding TUI without logging out.
If login expires, use `login-stop`, `login-start`, `login-personal`, `login-status`
to obtain a new URL. Only use `notify-login` when the user explicitly asks for a
Discord login handoff. It deduplicates delivery by URL hash.

## Exchange messages and manage work

```powershell
# Two local role turns plus one remote turn; polling itself uses no model.
.\.conda\python.exe scripts/remote_agy.py chat --request-id cpu-smoke-1 --message "Remember label CPU-AGY-LINK. Report readiness for CPU-only verification." --wait 60

# Next turn uses each host's own previous native session, when still valid.
.\.conda\python.exe scripts/remote_agy.py chat --request-id cpu-smoke-2 --message "Recall the label and run a small CPU-only verification in this workspace; save the result and report evidence." --wait 60

# Detached work can outlive a client waiting window.
.\.conda\python.exe scripts/remote_agy.py poll --request-id cpu-smoke-2
.\.conda\python.exe scripts/remote_agy.py review --request-id cpu-smoke-2
.\.conda\python.exe scripts/remote_agy.py status

# Explicitly stop an active remote turn through its owning worker.
.\.conda\python.exe scripts/remote_agy.py cancel --request-id cpu-smoke-2
```

`send` delivers your instruction directly without calling the local management
model. `local-turn` tests just the persistent local role. A successful agent
message is a self-report; validate claimed files/metrics independently before
declaring research success. This worker has no automatic checkpoint/resume or
production task/job integration yet. Remote commands retain AGY's own permission
and sandbox behavior; a headless approval or Linux sandbox failure must be
diagnosed rather than silently bypassed.

Headless AGY can exit zero while soft-denying a command. The worker maps native
`denied_actions` to `WAITING_APPROVAL` and refuses to treat JSON metadata/empty
responses as completion. After inspecting a script and confirming it is in the
authorized scope, the host can add a permission for just that request, for example
`--allow-command "python3 artifacts/cpu_link_verification.py"`. This becomes a
session-only `permissions.allow` rule; it does not disable the sandbox or edit
global settings. No failed/blocked request is automatically replayed.

## Durability and recovery

- Host ledger: `storage/remote-agy/colab-personal/mailbox.db` (local SQLite only).
- Local portable/native memory: `manager/.workflow/roles/remote_manager/` inside
  that directory, separate from the user-facing communicator.
- Remote receipts/artifacts: `/content/agy-worker/requests/`, `session.json`, and
  bounded portable `history.jsonl`. Remote native sessions rotate after 12 good
  turns or model changes, carrying handover evidence. Local memory uses the
  existing workflow rotation policy; it is not one infinitely growing session.
- Request IDs identify immutable content. Repeating `send` does not rerun a
  submitted ID. Repeating `chat` also reuses its prepared exchange/review.
- A transport timeout, missing receipt, dead process or changed VM is `UNKNOWN`;
  inspect before explicitly creating a new request. Heartbeat means the worker
  process is alive, not that an experiment has scientific progress.
- No public server/tunnel is opened. The existing authenticated loopback Colab
  bridge transports cells/receipts. A remote agent never executes host commands.
- `cancel` writes a request flag. Its owning live worker stops its own child
  process group and preserves artifacts. It never kills a PID from a stale
  receipt. A dead worker/changed VM remains an inspection/reconciliation case.
- `--wait` limits how long the client polls, not how long remote work runs.
  `--timeout` is the explicit budget for that one remote turn (default and
  maximum 6h), not a 30-minute inactivity cutoff. Reaching it retains artifacts
  and reports `UNKNOWN`; there is no
  automatic retry. Long experiments should run detached and report checkpoints.

If the browser closed, first inspect the current resource/runtime. Reopen Coc Coc
using the Colab skill, then `setup --reconnect` explicitly creates a fresh control
cell and reconnects the official bridge. This does not replay old requests. It
does not restore files lost with an ephemeral Colab VM. Do not disconnect a
runtime owned by another active task. The manager keeps ownership of its CPU
runtime until it is explicitly disconnected and released.

Official references: [Linux installation](https://antigravity.google/docs/getting-started?tab=cli),
[remote SSH OAuth](https://antigravity.google/docs/install?tab=cli),
[official CLI repository](https://github.com/google-antigravity/antigravity-cli).
