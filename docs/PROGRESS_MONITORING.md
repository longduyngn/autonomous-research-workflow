# Progress, W&B and recoverable supervision

The controller owns task/job identity and observed progress in SQLite. W&B is
optional telemetry, not the authority that declares research results valid.
`!status`, `main.py status`, `main.py task ID` and the dashboard expose the same
registered jobs: stage, completed/total, age of useful progress, telemetry state,
W&B link and diagnostic assessment. A completed job is distinct from a validated
task. Historical task 33 is not retroactively instrumented or rerun.

## Long-running Colab work

1. Prepare a dedicated code cell. Register before launch through the cloud tool:

   ```json
   {"request_key":"benchmark-v1","backend":"colab","external_id":"CELL_ID","total":80900,"stall_seconds":900,"budget_seconds":43200}
   ```

   The `80900` is illustrative: derive the actual denominator from the approved
   scope manifest, never a convenient smaller subset. Keep one job across its
   sequential stages. The returned `job.id` is durable. Repeating the request
   key with changed scope is rejected. A failed job cannot be replaced until
   explicitly reconciled; unknown submissions cannot be retried automatically.

2. Copy the generated `.agents/skills/workflow-progress/experiment_progress.py`
   helper to the VM and instrument the actual experiment loop:

   ```python
   from experiment_progress import ExperimentProgress

   progress = ExperimentProgress(
       "JOB_ID_FROM_CONTROLLER", "/content/progress", total=80900,
       project="antigravity-research", entity="YOUR_ENTITY", mode="online")
   try:
       for completed, batch in enumerate(batches, start=1):
           # Execute the real batch and save its actual output here.
           progress.update(completed, stage="detection", metrics={"loss": loss})
           # Separately save real resume state at safe boundaries.
       progress.finish()
   except Exception:
       # Save consistent failure/checkpoint evidence when possible.
       progress.finish(exit_code=1)
       raise
   ```

   Counters must be cumulative useful work across stages, not log lines or
   agent tool calls. The example counts batches, so set `total` to the number
   of batches if using it literally; if total counts document-condition rows,
   increment by the actual number of those rows completed instead.
   File receipts on `/content` are ephemeral; download/publish computation
   checkpoints to the configured durable destination. W&B resume does not
   restore model state, data cursor, files or a lost VM.

3. If desired, bind the SDK's `entity/project/run_id` through
   `experiment_job_track`. Online progress markers also bind this path.
   `colab_job_start({"job_id":"..."})` records uncertain submission before
   clicking Run and returns immediately. Repeated calls never click Run again.
   Return from the operator; do not wait in a 30-minute agent call.

4. The controller polls W&B or the prepared cell's `WORKFLOW_PROGRESS` output
   through the existing authenticated Colab bridge. A missing bridge, API
   outage or stale heartbeat does not resubmit or cancel the job. Polling does
   not allocate hardware or reconnect to a new notebook.

5. Compute completion wakes the operator for downloading existing outputs.
   Call `experiment_job_artifacts` with explicit downloaded file paths. It
   rejects incomplete declared cohorts and missing/empty files, hashes the
   files, and unbinds the compute job. The original checks and independent
   validator must still assess actual content, completeness and correctness.

The helper also emits a heartbeat every 60 seconds without advancing completed
work and, when available, samples `nvidia-smi` utilization and memory into numeric
metrics. A fresh GPU sample is a diagnostic signal, not proof of new results.
Pass `heartbeat_seconds=0` to disable it. At a main-loop update boundary, the
default 12-hour helper budget calls an optional `checkpoint_callback`, records
exit code 124 and raises a timeout; it never checkpoints model state from the
heartbeat thread. Set the helper budget to the same value as the registry.

For local experiments, write progress to `.workflow/progress/JOB_ID.json` in
the task workspace; local processes must be launched independently of the
agent if they need to survive agent termination. Merely registering a local
child process does not detach it. Kaggle jobs can register their existing
cloud job ID. In a registered task workspace, `kaggle_execute_notebook` now binds
the controller tracking ID before push and injects `WORKFLOW_EXPERIMENT_JOB_ID`
into the staged notebook. Initialize the progress helper from that environment
variable and pass the declared `total` when submitting. A standalone unregistered
cloud-tool invocation retains its legacy behavior and is not supervised.

## Watchdog and diagnostic agent

- Default useful-progress threshold: 900 seconds per job (configurable when
  registering it). Heartbeat refresh, stage-name changes and log chatter do
  not reset this timer. W&B heartbeat and completed counters are separate.
- Suspected stall: write a nonce-based `.workflow/checkin-request.json`, wait
  60 seconds for a cooperative `agent_checkin`, then consult a diagnostic agent.
  Check-ins are labeled unverified self-reports, not experiment evidence.
- Diagnostics use isolated memory and a read-only native hook policy; shell,
  cloud, write, cancel and messaging calls are denied. They return HEALTHY,
  SLOW, STALLED or UNKNOWN with evidence and a recommendation. One claim per
  progress epoch, maximum two consultations per job by default; late results
  are discarded if useful work advanced meanwhile. UNKNOWN is not proof of
  a dead process. The ordinary agent session is reviewed after 900 seconds.
- A STALLED or UNKNOWN assessment routes a bounded research review at a role boundary,
  retaining the same compute identity. No diagnostic can autonomously cancel,
  allocate, resubmit, weaken acceptance criteria or mark a task complete.
  If the same unresolved episode persists for three stall windows after review,
  the task enters WAITING_INPUT and queues a deduplicated notice on its original
  channel. Routine healthy progress does not send unsolicited Discord messages.
- At a soft agent deadline, fresh useful job progress or a recent healthy/slow
  diagnostic can grant a 300-second extension. Default hard agent ceiling is
  six hours, still bounded by the remaining task budget. The observer being
  temporarily unavailable grants short grace, not an unbounded lifetime.
- If the operator session ends but a registered job exists, the task enters
  WAITING_REMOTE without consuming an experiment retry. With unregistered
  uncertain effects it enters WAITING_INPUT for reconciliation, not a blind
  replay. Genuine job failure goes to the researcher within the attempt budget.
  A controller crash before the wait is committed also restores polling for a
  registered job; a stale worker is fenced from further native/API tool effects.
- `experiment_job_resume_checkpoint` reconciles only confirmed FAILED jobs,
  using a controller checkpoint whose metadata.job_id matches and whose payload
  hashes verify. Restore it explicitly and register a new request key afterward.
- The declared job budget triggers a WAITING_INPUT reconciliation at expiry.
  It does not forcibly cancel an unreachable Colab kernel or guarantee a remote
  shutdown. Checkpoint/stop confirmation remains necessary; no compute is
  silently released or assumed stopped on loss of telemetry.

Settings live under `monitoring` in config. Polling and diagnosis use separate
background slots so a slow model consultation does not block scheduling or
progress observations. This is not an OS-level watchdog for a dead controller;
the existing listener heartbeat must still be monitored externally for that.

## Login and validation

Run on the host:

```powershell
.\.conda\python.exe scripts/setup_wandb.py check --online
# Only when login is required, in your own interactive terminal:
.\.conda\python.exe scripts/setup_wandb.py login
```

Do not paste a key in Discord/chat or source. The SDK resolves host credentials
internally. A Colab/Kaggle VM needs its own `WANDB_API_KEY` platform secret; host
login does not authenticate it. Use offline mode if that secret is absent and
sync its SDK files later. Only explicit numeric metrics are logged; console,
source-code and git capture are disabled by the helper. Local receipts remain
available when W&B is absent or unavailable. Raw datasets/checkpoints are not
automatically uploaded to W&B.

Explicit synthetic probes (no GPU computation):

```powershell
.\.conda\python.exe scripts/verify_monitoring.py
.\.conda\python.exe scripts/verify_monitoring.py --online
.\.conda\python.exe scripts/verify_stall_diagnostic.py
```

Online probes create a clearly labeled synthetic run in the configured/default
account and `antigravity-research` project. Evidence lives under
`storage/verification-local/`. Real Colab training with this new instrumented
job wrapper is a separate live verification; no task-33 rerun is implied.

## Design references

The archived reference at `archive/ref/agentic_workflow/Work/lab/lab/` was read:
`daemon.py` excludes running-job check-ins from idle streaks; `fleet.py` separates
heartbeat loss from job failure and records anomalies; `watchdog.py` keeps a
separate controller heartbeat and deduplicates alerts; `progress.py` builds
reports from recorded facts. This implementation retains those principles
without copying rented-GPU fleet or systemd assumptions into the personal setup.

Primary W&B sources: [SDK settings](https://github.com/wandb/wandb/blob/main/wandb/sdk/wandb_settings.py),
[Public API runs](https://github.com/wandb/wandb/blob/main/wandb/apis/public/runs.py),
[login](https://github.com/wandb/wandb/blob/main/wandb/sdk/wandb_login.py).
The installed SDK version was tested because API/run-path representation can
differ across releases. The host's current service endpoint may display W&B
under CoreWeave Forge; use the actual SDK-returned run URL.
