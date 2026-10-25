# Antigravity Autonomous Ephemeral Workspace

An event-driven, multi-agent workspace where **every agent is spawned exclusively for its assigned step and immediately shut down upon completion**. Context is persisted in per-task Markdown log files (`storage/logs/task_<id>.log`), and every lifecycle transition, spawn, shutdown, and watcher trigger is recorded in a local **SQLite (WAL)** database (`storage/events.db`).

---

## Architecture & Working Loop

```mermaid
flowchart TD
    subgraph External["External Channels & Outside World"]
        MSG["Messaging Apps\n(Discord / Zalo / Messenger / Console)"]
        FEEDS["Outside World\n(RSS / REST APIs / Webhooks)"]
    end

    subgraph Daemons["Lightweight Non-LLM Daemons (Zero Idle Token Cost)"]
        GW["Messaging Gateway & Webhook Server\n(gateways/)"]
        ORCH["Hybrid State-Machine Orchestrator\n(core/orchestrator.py)"]
        WS_D["Workspace Monitor Daemon\n(watchers/workspace_watcher.py)"]
        WL_D["World Poller Daemon\n(watchers/world_watcher.py)"]
    end

    subgraph Storage["Persistent State & Context"]
        DB[("SQLite Event & Task DB\nstorage/events.db")]
        LOGS["Per-Task Context Logs\nstorage/logs/task_XXXXX.log"]
    end

    subgraph Agents["Ephemeral Agents (Spawn -> Execute -> Shut Down)"]
        R["1. Researcher"]
        O["2. Operator"]
        V["3. Validator"]
        C["4. Communicator"]
        W_WS["5. Workspace Watcher Agent"]
        W_WL["6. World Watcher Agent"]
    end

    MSG <-->|Inbound / Outbound| GW
    GW -->|Enqueue Task| DB
    ORCH <-->|Read / Update State| DB
    ORCH <-->|Read / Append Context| LOGS

    ORCH -->|Step 1: Spawn & Kill| R
    ORCH -->|Step 2: Spawn & Kill| O
    ORCH -->|Step 3: Spawn & Kill| V
    V -.->|If FAIL & retries < 3| O
    ORCH -->|Step 4: Spawn & Kill| C
    C -->|Deliver Final Reply| GW

    WS_D -->|On Anomaly Only| W_WS
    FEEDS --> WL_D
    WL_D -->|On New Signal Only| W_WL
```

### Why this Hybrid Loop Works Best
1. **Zero Idle Token Burn**: The 2 Watchers (`workspace_watcher` and `world_watcher`) use lightweight Python checks for file changes, stuck SQLite tasks, and RSS/API deduplication, spawning an LLM agent **only** when an anomaly or unseen external item is found.
2. **Bounded Validation Loop**: If `Validator` outputs `VERDICT: FAIL`, the Orchestrator routes the feedback back to `Operator` up to `max_validation_retries` (default `3`) before escalating to the user via `Communicator`.
3. **Role-Agnostic Pluggable Providers**: By default, roles run via **headless Antigravity CLI (`agy --print`)** which requires **no API key**. Any role can be switched independently to OpenAI/Ollama (`http_llm`), Gemini REST (`gemini_http`), or any custom CLI in `config/workflow_config.json`.

---

## Quickstart Commands

### 1. Test the Full Ephemeral Loop (Dry-Run Mode)
Verify the state machine, SQLite event logging, and context log generation instantly:
```powershell
python main.py run "Create a utility script" --dry-run
```

### 2. Run a Real Task with Headless Antigravity (`agy`)
Executes `researcher` -> `operator` -> `validator` -> `communicator` using headless `agy`:
```powershell
python main.py run "Create a Python script fib.py that prints the first 10 Fibonacci numbers"
```

### 3. Inspect SQLite Events & Tasks
```powershell
python main.py status
python main.py logs 1
```

### 4. Change the Agent Provider for Any Role
Switch any role to another provider defined in `config/workflow_config.json`:
```powershell
python main.py set-role-provider researcher gemini_rest_api
python main.py set-role-provider researcher agy_headless_safe
```

### 5. Start the 24/7 Autonomous Daemon & Messaging Webhook Server
```powershell
python main.py daemon
```
Once running, you can send tasks via HTTP/Discord/Zalo/Messenger webhooks:
```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8080/webhook/task" `
  -ContentType "application/json" `
  -Body '{"prompt": "Check workspace status", "channel": "console", "user": "admin"}'
```
