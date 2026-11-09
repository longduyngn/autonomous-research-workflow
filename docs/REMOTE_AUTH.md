# Remote CLI authentication without repeated browser login

Verified against official provider documentation on 2026-11-07. AGY and an opt-in
two-role Claude CPU pilot are implemented on Colab. The normal research task
defaults have not been migrated to the pilot; Codex remains a deployment option.

| Provider | Subscription authentication | Noninteractive bootstrap on a new VM |
|---|---|---|
| AGY | Google OAuth and the OS credential store; subsequent CLI processes reuse the login on that VM | The docs reviewed do not expose a reusable personal subscription key/environment variable. A new VM may require a new OAuth login. |
| Codex | ChatGPT login cache in `auth.json` or the OS credential store | Officially supports seeding a trusted headless runner with its own auth cache. Persist the refreshed cache securely across VM replacements. Do not share one cache across concurrent jobs/machines. |
| Claude Code | Subscription OAuth | `claude setup-token` creates a one-year OAuth token. Supply it as `CLAUDE_CODE_OAUTH_TOKEN` to the native CLI on the VM. Initial token generation opens a browser approval flow; each later VM needs no new browser login while the token remains valid. |

## Practical choice

For the requested “one secret and automatic bootstrap on ephemeral Colab” design,
Claude Code's documented long-lived OAuth token is the closest match. It uses
the Claude subscription, not an Anthropic API key. A token still expires or can
be revoked. It cannot establish Claude's own Remote Control sessions or access
claude.ai connectors; our separate notebook/mailbox transport does not depend
on those features.

The one-time provisioning command is `claude setup-token`. A private host secret
would then be named:

```dotenv
CLAUDE_CODE_OAUTH_TOKEN=<subscription OAuth token>
```

The host must explicitly load the secret and inject it into the remote native
CLI environment; writing `.env` alone does not make an arbitrary CLI read it.
Use encrypted transfer and a secret store. No token values belong in notebook
source/output, logs, repository files, or chat. Initially no token was present.
The user subsequently configured it in env; the controller transferred it as an
AES-GCM/RSA-OAEP capsule and provided it to native CLI children through memory and
a pipe, without another browser login on Colab. The live probe selected
`oauth_token`, with no inherited Anthropic API-key fallback.

Codex also supports headless subscription use, but its advanced CI guidance
requires one `auth.json` per runner/serialized stream. Restore it only when
missing, let Codex refresh it, and persist the updated version. Do not repeatedly
restore an old seed. Copying the active desktop cache to several simultaneous
machines can race refresh-token rotation; it is not a robust permanent key.
The local Codex account was verified signed in, but its cache was not cloned to
Colab. Enterprise Codex access tokens are a distinct enterprise capability, not
assumed available on a personal subscription.

Gemini API keys and Anthropic/OpenAI API keys have their own model availability,
usage and billing rules. They are not exports of the personal subscription.
AGY supports Gemini API-key mode, but it requires `modelProvider: "gemini"` as
well as `GEMINI_API_KEY`; setting that variable alone does not switch providers.

If the priority is eliminating any new remote model-login step, keep the already
authenticated CLI(s) on the current host and use Colab as a code/experiment
worker through the existing bridge. The experiment can execute remotely while
model orchestration stays local. That arrangement does not provide an independent
remote LLM agent while the host is offline. Initial authorization, revocation,
MFA/consent policy and token expiry cannot be promised away by the controller.

## Actual AGY acceptance

- User completed remote OAuth; `auth-check` returned `AUTHENTICATED` before and
  after the onboarding TUI was stopped. No further user action was needed for
  the three live exchanges.
- Native remote session remained `2de25d58-5054-4b9c-8074-44ef378ea412`.
- The second request's exit-zero permission denial was reconciled to
  `WAITING_APPROVAL`, preserving its original receipt.
- The third request used one reviewed command grant and executed the CPU script.
  Independent host readback/recomputation found `sum_squares=333383335000`,
  `n=10000`, `gpu_present=false` and recalled label `LINK-7C91-BETA`.
- Live evidence: `storage/remote-agy/verification/live/receipt.json`. This is a
  CPU communication/execution probe, not a full research benchmark.

## Actual Claude acceptance

The user requested the test after provisioning `CLAUDE_CODE_OAUTH_TOKEN`.
Claude Code 2.1.319 was installed via the official npm package after the native
installer URL returned HTTP 403 from Colab. The model smoke probe succeeded with
`claude-sonnet-5-5` under `oauth_token` authentication.

Two independent Claude Code sessions and the Python task supervisor then ran on
CPU Colab. Six native `SendMessage` calls and 13 native final responses are saved.
The disclosed off-by-one baseline failed four of five independent cases; the
operator repaired it and all five cases passed. The host downloaded 19 files,
verified their SHA-256 hashes, safely evaluated both tiny metric functions, and
independently recomputed `333383335000`. The workflow took 37.476 seconds after
launch, excluding installation and the authentication probe. Both CLI processes
were confirmed stopped after completing the task.

Evidence: `storage/remote-claude/claude-pair-20261107-1/verification.json`.
The W&B publication is explicitly a post-run verification summary, not a live
progress trace. Native session continuity across turns is verified; VM replacement
and production research-task dispatch are not proven by this CPU pilot.

Host entry points (load project env automatically):

```powershell
# Setup, encrypted token injection and a deduplicated remote launch in one command.
.\.conda\python.exe scripts/remote_claude.py run --kind pair --job-id <new-id>
.\.conda\python.exe scripts/remote_claude.py status --job-id <id>
.\.conda\python.exe scripts/remote_claude.py collect --job-id <id>
.\.conda\python.exe scripts/verify_remote_claude.py --job-id <id>
```

The pair runner is deliberately the bounded fault-injection acceptance workflow,
not an arbitrary project executor. Reusing a job ID returns its existing receipt;
it never replays a completed or ambiguous workflow. The existing Colab bridge and
resource fencing are shared with the AGY pilot; no second runtime or public
server is opened. Credentials remain outside the artifact export allowlist.

Official sources:
- [AGY authentication](https://antigravity.google/docs/install?tab=cli)
- [AGY headless permissions](https://antigravity.google/docs/cli/headless/)
- [Codex authentication](https://learn.chatgpt.com/docs/auth)
- [Codex account auth in CI/CD](https://learn.chatgpt.com/docs/auth/ci-cd-auth)
- [Claude Code long-lived tokens](https://code.claude.com/docs/en/authentication#generate-a-long-lived-token)
- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)
