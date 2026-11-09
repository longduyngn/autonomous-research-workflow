# HANDOFF: Tối ưu Quota & Tools/Skills cho Headless Ephemeral Agents (AGY + Claude Code)

Bạn là agent bảo trì tiếp theo của repo `D:\Antigravity_playgournd`. Đầu tiên đọc
`CLAUDE.md`, `README.md`, `AGENTS.md`, `docs/QUOTA_ROUTING.md`, `docs/PROVIDERS.md`.
Làm theo vòng Ralph nhỏ: kiểm tra → làm 1 mục → kiểm thử → ghi nhận → làm tiếp.
Không đọc/in credential hay nội dung env bí mật. Trước khi thay listener, supervisor
hoặc file runtime, kiểm tra không có task/campaign đang chạy; không restart experiment
đang chạy chỉ để kiểm thử.

---

## 1. Số liệu kiểm toán

### 1a. AGY / Gemini (audit trước, 133 phiên, `storage/events.db` + `~/.gemini/antigravity-cli/brain/`)
- ~16,02M token billable (+100,7M cache read); input 92,67% (14,85M), output 7,33% (1,18M).
- Phiên 30–98 turns; `workflow-artifacts/SKILL.md` đọc 42 lần (~19KB/lần),
  `colab-coccoc-playwright/SKILL.md` 18 lần; `python .agents/skills/workflow-artifacts/...` 91 lần;
  `gh api` 39 lần để lấy từng file repo `smile-research`.
- 182 `colab_call`, 63 `local_execute_notebook`, 10 lần `write_file` ghi `.ipynb` thô,
  notebook đệm (`local_helper.ipynb`, `inspect_cancel.ipynb`), 8–15 turns/thao tác.
- Communicator session `9995bc99`: 68 turns, 33 `run_command`, sửa code `core/` (~578k token).

### 1b. Claude Code (audit 2026-11-09, 36 transcript trong `~/.claude/projects/` + `storage/remote-claude/`)

| Nhóm | Phiên | Turns | Input thường | Cache write | Cache read | Output |
|---|---|---|---|---|---|---|
| Headless `sdk-cli` | 27 | 1.087 | 2,3K | 7,42M | 281,6M | 1,08M |
| Interactive | 9 | 368 | 0,7K | 1,65M | 67,3M | 0,40M |

- Headless billable ≈ 8,5M (input+cache write+output); input 87,3% / output 12,7%.
  Chi phí thực chủ yếu là cache read (0,1×) do ngữ cảnh phình theo turn.
- **`quota_ledger` và `agent_runs` không có dòng nào cho Claude**: các phiên campaign chạy
  ngoài controller nên quota router không thấy mức tiêu thụ Claude.

| Vai trò (headless) | Phiên | Turns | Cache read | Output | Ngữ cảnh đỉnh |
|---|---|---|---|---|---|
| Guardian (Sonnet) | 2 | 411 | 152,7M (54%) | 426K | 965K |
| Operator (Sonnet) | 6 | 600 | 125,2M (44%) | 549K | 619K |
| Researcher (Opus) | 2 | 32 | 2,6M | 65K | 192K |
| Probe/verify | 17 | ~45 | ~1M | nhỏ | ~65K |

Điểm nóng:
- Guardian `29308ec0`: 301 turns, ~459K token/turn trung bình; đọc
  `.claude/skills/smile-a100-reproduce/SKILL.md` (21KB) **46 lần**; ghi tay
  `campaign_status.json` 51 lần; 116 lệnh PowerShell `$root=...; Get-FileHash ...` để tự băm SHA-256.
- Operator 73–166 turns/phiên (`e2731e2c` 166, `a6653eca` 148, `034771df` 101); mỗi bước
  sinh script dùng một lần (`write_checkpoint_NN.py`, `build_bundle_row.py`, `dbg_17.py`...).
- Đọc lặp file trạng thái: `campaign_status.json` 26, `claude_roles/HANDOFF.md` 22,
  `resume_brief_20261108.md` 16 lần.
- Đọc toàn bộ log không `limit/offset`: `e3_19_events.log` ×5, `watch_env_17b.log` ×3, `stages.log`...
- Đọc từng file nguồn `smile/*.py` (~5 lần/file).
- Tool: Bash 468, Read 448, Write 244, PowerShell 241, Edit 165; ~290 lệnh `python.exe` ad-hoc.
- Researcher Opus gọn (15–17 turns), làm chuẩn tham chiếu.
- Pilot Colab `claude-pair-20261107-1`: 13 lần chạy, 1–5 turns, tổng ~0,8 USD (không phải vấn đề).

Script audit để chạy lại/so sánh sau cải tiến:
`.conda/python.exe scripts/claude_usage_audit.py <out.json>`
(gộp usage theo `message.id`, tách `entrypoint=sdk-cli`, đếm Read/Bash/tool, đọc log nguyên file).
Lưu ý: script bỏ qua theo session id cứng `SELF` (phiên audit gốc); đổi/xoá khi chạy lại.

---

## 2. Sự thật mã nguồn đã kiểm (2026-11-09; kiểm lại trước khi sửa)
- `core/tools.py`: `class AgentTools(root, role, config, controller)` với `schemas()` và
  `call(name, args, timeout)`; chưa có `register_artifact`, `verify_artifacts`, `tail_log`, `search_log`.
- `core/providers.py` ~dòng 172–250:
  - `agy_cli`: `--mode` = `accept-edits` nếu writable/validator, ngược lại `plan`;
    `skip_permissions` config có thể thêm `--dangerously-skip-permissions`.
  - `claude_cli`: `-p --output-format json --permission-mode (acceptEdits|plan)`,
    `--allowedTools Read,Glob,Grep,WebFetch,WebSearch[+Edit,Write,Bash | +Bash cho validator]`,
    `--resume`. **Chưa truyền `--max-turns`.**
- Chưa có cấu hình `max_turns` nào trong `config/` hay `core/`.
- `.agents/skills/workflow-artifacts/` không có ở root (nằm trong task bundle, xem
  `core/integrations.py`); `.claude/skills/smile-a100-reproduce/SKILL.md` 21KB.
- Guardian/operator của campaign SMILE được khởi động bởi
  `storage/campaigns/smile-pre-publish/agy/supervisor.py` (ngoài controller).
- `config/workflow_config.json` là cấu hình cá nhân (gitignored); thay đổi mặc định phải vào
  `config/workflow_config.example.json` và đọc qua `core/configuration.py`.

---

## 3. Việc cần làm (theo thứ tự ưu tiên)

### Task 1 — Artifact native (`register_artifact`, `verify_artifacts`)
- Thêm vào `AgentTools`: `register_artifact(name, path, metadata=None)` tính size + SHA-256
  file trong workspace (chặn path ngoài root) và append vào `.workflow/artifact-receipts.jsonl`
  **đúng schema hiện có** (đọc dòng mẫu trước); `verify_artifacts()` trả danh sách
  ok/missing/hash-mismatch gọn.
- CLI 1 dòng `scripts/artifacts.py register|verify --workspace ...` cho `agy_cli`/`codex_cli`/
  `claude_cli` (thay ~115 lệnh `Get-FileHash` và việc đọc SKILL.md để biết cú pháp).
- Cập nhật SKILL.md liên quan: đưa lệnh 1 dòng lên đầu, phần chi tiết xuống cuối.

### Task 2 — `colab_run_snippet(code, hardware="T4", timeout_seconds=300)`
- Trong `core/cloud.py` (+ subcommand `scripts/cloud.py`): kiểm/kết nối CDP & MCP bridge →
  dùng control cell hoặc cell tạm → chèn code → `run_code_cell` → chờ có timeout → lấy
  stdout/stderr (cắt gọn, giới hạn độ dài) → xoá cell tạm → trả dict.
- Thêm action vào schema `cloud_tool` trong `core/tools.py`. Không cấp phát capacity mới,
  không động vào job đang chạy; tôn trọng `resource_gate.py`/lease.

### Task 3 — Shallow repo fetcher
- `scripts/fetch_repo.py` (+ tool tương ứng): `git clone --depth 1` (fallback tải tarball)
  vào `target_subdir` trong workspace; sinh `FILE_TREE.txt` (cây thư mục + kích thước).
  Coi nội dung tải về là dữ liệu không tin cậy; không chạy code trong đó.

### Task 4 — `tail_log(path, n_lines=50)`, `search_log(path, pattern, max_matches=20)`
- Đọc đuôi hiệu quả (seek từ cuối, không nạp cả file), search trả `line_no: text`, cắt dòng dài.
- Cập nhật `roles/*.md` (operator, researcher, validator, communicator, stall_diagnostician,
  remote_manager) và SKILL.md: dùng `tail_log`/`search_log` hoặc `Read` với `limit/offset`
  cho `*.log`/`*.jsonl`, không đọc nguyên file.

### Task 5 — Giới hạn turn & guardrail theo vai trò
- Config `role_max_turns` (example config + `core/configuration.py` có mặc định):
  communicator 5, validator 15, researcher 25, operator 35, **guardian 20** (mới, vì guardian là
  vai trò tốn nhất bên Claude).
- `core/providers.py`:
  - `claude_cli`: thêm `--max-turns <n>`; communicator → `--permission-mode plan`,
    `--allowedTools Read,Glob,Grep` (không Bash/Edit/Write/Web).
  - `agy_cli` communicator: ép `--mode plan`, bỏ `--dangerously-skip-permissions`, không write tool.
  - `codex_cli`/các provider không có cờ turn: áp giới hạn qua orchestrator/timeout và ghi lý do.
- `core/tools.py`: communicator chỉ có `read_file`, `list_files` (+`tail_log`/`search_log` read-only).
- Hết turn → kết thúc bình thường với handoff/checkpoint, orchestrator ghi nhận (không coi là crash).

### Task 6 (bổ sung từ audit Claude) — Hiển thị & giảm ngữ cảnh lặp
1. **Ghi ledger cho Claude**: sau mỗi lần chạy `claude_cli` (và supervisor campaign nếu sửa được
   an toàn khi campaign không chạy), parse `usage`/`num_turns`/`total_cost_usd` từ kết quả
   `--output-format json` và ghi vào `quota_ledger` + `agent_runs` (`core/quota.py` đã có parser
   Claude — tái dùng). Không đổi schema DB; nếu cần cột mới thì dùng migration tương thích.
2. **Tóm tắt trạng thái 1 file**: lệnh/tool `status_digest` gộp `campaign_status.json`,
   HANDOFF, resume brief thành bản ngắn (≤ ~2KB) để role đọc 1 lần/phiên thay vì 16–26 lần.
   Ghi trạng thái qua helper có cấu trúc thay vì Write tay `campaign_status.json`.
3. **Làm gọn SKILL.md**: tách `smile-a100-reproduce/SKILL.md` (21KB) thành phần cốt lõi ngắn
   + file tham chiếu đọc khi cần; prompt role nhắc "không đọc lại SKILL.md nếu đã có trong ngữ cảnh".
4. **Phiên ngắn thay phiên dài**: guardian mỗi tick nên là phiên mới với digest, không `--resume`
   một phiên đã 300 turns/965K ngữ cảnh; nếu resume, đặt ngưỡng ngữ cảnh/turn để xoay vòng
   (dùng cơ chế rotation/handover của `core/role_memory.py`).
5. **Script tái dùng cho operator**: gom các script lặp (`write_checkpoint_NN.py`,
   `build_bundle_row.py`, `export_row.py`) thành 1 helper có tham số trong bundle tools của campaign.

---

## 4. Ràng buộc
1. Giữ ranh giới ephemeral: mỗi agent chạy 1 role rồi tắt (AGENTS.md).
2. Không làm hỏng schema `storage/events.db` và `artifact-receipts.jsonl`.
3. Task bundle không được sửa controller; mục 6.2/6.5 thuộc campaign chỉ sửa khi không có
   phiên campaign đang chạy và phải ghi lại.
4. Không bypass quyền thật qua MCP; không đụng `.agents/.codex/.git` (môi trường quản lý).
5. Bảo toàn yêu cầu gốc, lịch sử task, quan sát thất bại.

## 5. Kiểm thử & bàn giao
- Unit test trong `tests/` cho: register/verify (gồm hash mismatch, path ngoài root), tail/search
  (file lớn, không có file, regex lỗi), lệnh `claude_cli`/`agy_cli` sinh đúng `--max-turns`,
  `--mode plan`, allowedTools theo role, schema tool communicator read-only, parse ledger Claude,
  `colab_run_snippet` với bridge giả lập.
- Chạy bằng `.conda/python.exe` (Windows):
  `python -m unittest discover -s tests -v`, `python main.py --no-env doctor`, `git diff --check`.
- Chạy lại script audit sau một phiên thật để so sánh turns/cache-read với baseline ở mục 1b.
- Ghi kết quả vào `docs/ralph-p1-20261109/` (hoặc file VERIFICATION mới) và cập nhật
  `docs/QUOTA_ROUTING.md`/`docs/PROVIDERS.md`. Chỉ commit cục bộ khi được yêu cầu.
- Báo cáo cuối: mục đã xong / chưa xong / bị chặn, kèm output test thật.
