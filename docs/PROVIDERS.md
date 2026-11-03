# Mix agents by role

Each role has its own provider, model, effort, timeout and tier. For example:

```powershell
python main.py configure-role researcher --provider claude_cli --model opus --effort high
python main.py configure-role operator --provider codex_cli --effort medium
python main.py configure-role validator --provider agy_headless_auto --effort medium
python main.py configure-role communicator --provider openrouter
python main.py doctor
python main.py verify --provider configured
```

Check model aliases against your account before pinning them. Config changes
apply after restarting workers. Existing role defaults are preserved here.

`run/enqueue --provider NAME` overrides **every role for that task**. Omit it to
use mixed roles. The override persists in SQLite across worker processes.
Discord supports `!task --provider codex ...` (or `antigravity`, `claude`,
`opencode`). `use-cli` intentionally changes every default and clears model
overrides; it is unnecessary for mixed configurations.

## Capabilities

| Adapter | Tools | Workflow observation | Current evidence |
|---|---|---|---|
| Antigravity CLI | Native | Native hooks required | Earlier real local/cloud proof; regressions |
| Codex CLI | Native | Native hooks + emitted thread ID | Current real four-role local proof |
| Claude Code CLI | Native | Native hooks required | Adapter checks; not installed here |
| OpenCode CLI | Native | Named JSON session; controller observation | JSON/permission contract tests; not installed here |
| OpenRouter / compatible API | Controller tool loop | Request/tool trace | Mocked tool execution checks |
| Text-only Gemini HTTP | Communication/watchers | Controller result | Rejected for execution roles |

No silent provider/model fallback occurs. Login/quota errors, incomplete output
and missing required native hooks are failures. OpenCode has no claim of native
workflow hooks; its saved controller evidence is labeled accordingly.

## Model and reasoning selection

Resolution: role `model_env` → role `model` → provider tier environment value →
tier default. Tiers are `research`, `worker`, `fast`. Prefixes are `AGY_`,
`CODEX_`, `CLAUDE_`, `OPENCODE_`, `OPENROUTER_`; e.g. `CODEX_RESEARCH_MODEL`.
Pin your strongest research model, suitable workers, and a fast communicator.
Empty CLI bindings use account defaults. Different effort values alone do **not**
guarantee different models or intelligence. OpenCode uses model-native settings
rather than the generic effort flag.

## OpenCode and OpenRouter

Install/login to OpenCode separately. Set an explicit model in its
`provider/model` form:

```powershell
python main.py configure-role communicator --provider opencode_cli --model "openrouter/<model-id>"
python main.py configure-role operator --provider opencode_cli --model "openrouter/<tool-capable-model-id>"
python main.py verify --provider configured
```

The adapter runs `opencode run --format json --title ...`, extracts text/error
events and records the emitted session ID. It requires an explicit model and
does not pick a paid default. Its role permissions deny questions/subagents,
deny non-operator edits, and allow shell only for operator/validator. These are
tool settings, not OS security: shell commands can still edit files. Installed
OpenCode plugins and global account configuration remain external dependencies.

For direct API access, configure `OPENROUTER_API_KEY` and tier model variables,
then select `openrouter` per role. `free_only: true` constrains routing to zero
prompt/completion prices; unavailable/rate-limited routes fail with no paid
fallback. This constraint does not govern independent OpenCode requests: select
your free model there explicitly. Execution models need working tool calls. API researchers can read paginated PDF text with `read_document` and continue long text files with `read_file(start_char=...)`; image-only pages need separate visual/OCR tooling.

## Codex checks

Run `python main.py verify --provider codex_cli` in your normal logged-in host
terminal. It uses a separate verification DB, creates a tiny local program,
independently validates it and reports actual hook/skill evidence. It sends no
Discord messages and allocates no GPU. `doctor` checks installation, not login.
Run `codex login` yourself when authentication is required.
`WORKFLOW_CODEX_EXECUTABLE` can select an explicit installation.

Codex receives hash-checked generated hooks through invocation configuration;
unknown global hooks are not silently trusted. Automatic approval review remains
enabled for operator/validator, with no sandbox bypass. A parent application's
restricted terminal can impose additional restrictions; treat them as environment
failures rather than weakening permissions.

References: [Codex execution](https://learn.chatgpt.com/docs/non-interactive-mode),
[Codex commands](https://learn.chatgpt.com/docs/developer-commands),
[OpenCode CLI](https://opencode.ai/docs/cli/),
[OpenCode permissions](https://opencode.ai/docs/permissions/).
