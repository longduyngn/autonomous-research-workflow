# Conversation, research memory and resumable execution

Start with README.md. The controller runs two tasks concurrently. Each task follows
one sequential Ralph loop; the researcher decides whether it needs milestones.

## Discord

DM or mention the bot to talk. When a communicator provider is configured, ordinary
chat uses its own persistent history per user/channel. It cannot authorize work or
approvals. Set `conversation.model_replies: false` for deterministic command-only
intake. Status/search never need an LLM.

| Message | Effect |
|---|---|
| `!task reproduce this experiment` + attachments | New isolated task |
| `!status`, `!search RSA` | Your tasks in this channel |
| Reply to a task message: `use seed 42` | Feedback for that task |
| `!feedback 19 use seed 42` | Explicit task feedback |
| `!retry 19` | New run linked to the old request; retains old history |
| `!cancel 19` | Requests stopping at a role boundary; does not claim a remote VM stopped |
| `!approvals` | Recorded exact actions and decisions |
| `!approve <id>`, `!deny <id>` | Decide only your own action in its original channel |

Use one command per message. Ambiguous text becomes conversation, not a new task.
Identical message IDs are deduplicated. Identical text in a new explicit task is
allowed and may point to a matching older request. Failed delivery remains queued;
a crash between send and acknowledgement can still duplicate the response.
Do not run multiple Discord listeners. Legacy tasks without verified sender IDs
remain inspectable from the local dashboard/CLI; ownership is not guessed.

## Role memory and review

Each task has `.workflow/roles/{researcher,operator,validator,communicator}/`.
Explicit provider sessions resume only for the same role, provider/model and
instructions. A per-role OS lock prevents concurrent resumes. Rotation or provider
changes clear native identity and retain portable history. Conversation front-end
memory is separate, under `storage/conversations/<scope-hash>/`.

Read `.workflow/state.json` first, then the referenced handoff/evidence. It records
controller facts at a timestamp; role summaries are labeled unverified notes.
Plans have immutable `plans/plan_0001.json` snapshots. Feedback is applied between
role calls and never changes the original request or a running prompt. Pending
feedback prevents a stale final report from completing the task. An outstanding
remote job is reconciled before feedback can trigger replanning.

Researcher reviews occur after verified milestones, new feedback and repeated
validation failures. `orchestrator.research_review_at_milestones: false` disables
the milestone review. Existing checks, verified steps, backend and budgets remain
preserved. A change that removes an original acceptance requirement should use a
new explicit task. Validators must separate required failures from suggestions.

## Waiting and resource ownership

`WAITING_REMOTE`, `WAITING_RESOURCE`, `WAITING_APPROVAL`, `WAITING_PROVIDER` and
`WAITING_INPUT` release the worker. Active execution uses the original start time
minus accumulated waits; a separate wait deadline prevents indefinite silent waits.
Remote polling retains the submitted job ID and downloads results before validation.
No operator call is repeated merely to check a running job.

Personal Colab has one owner across CPU/GPU operations. A fresh bridge must observe
an idle Colab notebook with **no active sessions** before granting ownership; open
the intended notebook in the configured browser first. An unknown observation is
not proof of availability. Ownership survives restart and transport closure. A
generation token and OS lock are checked at managed MCP/browser execution. Direct
scripts/browser access outside this gateway are outside that fencing boundary.

`colab_release` observes actual shutdown. If the VM still runs, it retains ownership
so the current task can perform explicit cleanup. After independently stopping an
orphaned VM, host inspection can reconcile it:

```powershell
python main.py control reconcile-colab
```

This reads browser state; it does not disconnect another task or allocate compute.
Do not mix protocol-1 and protocol-2 bridge processes. Stop/reconcile old work before
restarting the host bridge. No automatic service restart is performed by an update.

## Approvals and provider waits

Restricted shell actions from native hooks/API tools are denied while approval is
pending. The role returns to the scheduler and the task waits. A matching approval
permits one matching hook invocation; native permissions still apply. Changed command
arguments or source/plan/feedback fingerprints require a new decision. Fingerprinting
excludes controller logs/state/secrets and is bounded to 64 MiB/10,000 files; larger
workspaces need host review. A consumed approval followed by an uncertain failure
requires reconciliation. Do not retry unknown side effects blindly.

```powershell
python main.py control list
python main.py control show <approval-id>
python main.py control approve <approval-id> --actor <recorded-owner-id>
python main.py control deny <approval-id> --actor <recorded-owner-id>
python main.py control cooldown <provider-name> --seconds 120
```

The local CLI is a trusted administrator interface, not remote authentication.
Agents have no approval-decision tool. This remains a trusted local code workflow;
same-user native shell access is not a security boundary against hostile agents.
First HTTP request rate limits can wait safely before tools run. Later API failures
and CLI quota errors may follow effects and are not automatically replayed.

## Computation recovery and future compute

See [CHECKPOINTS.md](CHECKPOINTS.md). Task memory/Git checkpoints do not save GPU
tensors. Training saves model, optimizer, scheduler/scaler, RNG, step and data cursor
at a safe boundary to an explicitly configured durable destination. The helper
publishes verified manifests and restores matching code/data/config hashes into a
fresh directory. No Drive authorization/mount is silently performed.

[Compute extensions](COMPUTE_EXTENSIONS.md) defines disabled adapters for Shadeform,
VastAI, Runpod and SSH. No rented machine can be purchased by changing a config flag.

Use `python main.py task <id>` or the read-only dashboard to inspect waits, approval
details, plan/feedback revisions and role transcripts. Terminal state remains based
on actual execution and validation, never on a conversation model's claim.
