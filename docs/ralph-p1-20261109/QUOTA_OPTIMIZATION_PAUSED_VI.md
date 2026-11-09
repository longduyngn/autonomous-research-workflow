# Bàn giao tối ưu quota — tạm dừng theo yêu cầu người dùng

Thời điểm: 2026-11-09, Asia/Bangkok. Trạng thái: **WIP, chưa sẵn sàng chạy thực tế**.
Người dùng yêu cầu tạm dừng và đóng gói; không tiếp tục triển khai sau mốc này.
Không commit, không khởi động lại listener/supervisor, không chạy experiment hay gọi model.

## Yêu cầu và nguồn

Đặc tả đầy đủ: `docs/QUOTA_OPTIMIZATION_HANDOFF_PROMPT.md`, Task 1–6.
Giữ audit AGY gốc và audit Claude làm baseline; không suy mức tiết kiệm khi chưa có phiên mới.
Đã đọc quy tắc workspace, CLAUDE.md, README, tài liệu provider/quota và schema receipt hiện có.
Workspace vốn có nhiều thay đổi chưa commit trước khi bắt đầu. Snapshot mã và diff trong gói
là trạng thái tích hợp hiện tại, **bao gồm thay đổi có trước**, không phải patch sạch của riêng lượt này.
Không áp toàn bộ diff lên checkout khác mà không đối chiếu.

## Đã viết trong lượt này

- Mới: `core/artifacts.py`, `scripts/artifacts.py`: băm theo block, receipt giữ các trường
  `path`, `bytes`, `sha256`, bổ sung tùy chọn name/metadata; register/verify trong workspace.
- Mới: `core/log_tools.py`, `scripts/logs.py`: tail có giới hạn byte/dòng;
  search regex theo phần nhỏ, trả số dòng và giới hạn số kết quả.
- `core/tools.py`: thêm schema/dispatch cho artifact và log; communicator chỉ còn
  read_file/list_files/tail_log/search_log. Đã đặt schema/dispatch status_digest/fetch_repo,
  nhưng backend hai chức năng này **chưa viết**.
- `core/configuration.py`, example config: mặc định communicator 5, validator 15,
  researcher 25, operator 35, guardian 20; kiểm tra giá trị cấu hình.
- `core/providers.py`: thêm Claude --max-turns và --tools/--allowedTools theo vai trò;
  ép AGY communicator plan, bỏ skip permissions; chặn extra_args ghi đè các cờ được bảo vệ.
  Giữ stream-json đang có để bắt quota events. Lấy num_turns/total_cost_usd từ result,
  đánh dấu error_max_turns và ghi handoff. HTTP loop lấy min của tool rounds/role budget.
  Guardian trong runner bỏ native session mỗi lần gọi.
- `core/database.py`: migration cộng thêm usage_json/num_turns/total_cost_usd cho agent_runs.
- `core/orchestrator.py`: truyền WORKFLOW_AGENT_RUN_ID; khi budget_exhausted thì ghi event,
  WAITING_INPUT để đối chiếu checkpoint, không đốt retry.
- `core/role_memory.py`: cộng cache_creation_input_tokens khi tính context rotation.

Các mục trên mới là mã nháp; chưa có unit test chứng minh hành vi.

## Thiếu quan trọng trước khi chạy

1. **core/providers.py đã gọi `core.quota.persist_claude_run`, nhưng hàm này chưa tồn tại.**
   Chạy Claude với database_path ở trạng thái này có thể lỗi import sau khi native run kết thúc.
   Cần hoàn thiện hàm ghi usage vào quota_ledger/agent_runs và chống ghi trùng khi controller
   cũng gọi record_result; hoặc tháo dây nối nháp trước khi dùng production.
2. Chưa có `core/campaign_tools.py` (status_digest) và `core/repo_fetch.py` (fetch_repo).
   Không gọi hai tool này trước khi triển khai backend. Chưa có CLI fetch/digest.
3. Chưa làm colab_run_snippet hay subcommand cloud. Cần giữ fencing/lease xuyên thao tác,
   xác minh runtime đang có, từ chối job đang chạy; không cấp capacity mới để thử.
4. Chưa sửa `core/integrations.py` để bundle workflow-artifacts dùng helper mới;
   chưa cập nhật roles/*.md hoặc tài liệu provider/quota.
5. Chưa tách `.claude/skills/smile-a100-reproduce/SKILL.md` thành core + references.
6. Chưa sửa supervisor campaign, chưa gom operator helper. Đã thấy helper có tham số
   `row_tool.py`, `export_row.py`, `build_bundle_row.py` có sẵn: đọc và tái dùng trước khi viết mới.
7. Chưa viết/chạy unit tests, doctor, full suite, diff --check hoặc audit trước/sau.
   Trong gói chỉ có kiểm tra compile cú pháp và tính toàn vẹn ZIP, không phải nghiệm thu.

## Phát hiện cần giữ khi tiếp tục

- Adapter hiện dùng stream-json, khác mô tả json trong đặc tả; giữ khả năng bắt rate_limit_event.
- Trước khi sửa campaign đã kiểm tra PID + create_time trong supervisor.json:
  không có identity supervisor/child nào khớp tiến trình còn sống tại lúc kiểm tra.
  Có CLI AGY/Claude khác trên máy; không dừng hay sửa chúng. Phải kiểm lại khi tiếp tục.
- DB đọc bằng SQLite mode=ro: 17 COMPLETED, 16 ESCALATED; không có task active tại mốc kiểm tra.
- `agy/supervisor.py` là launcher cũ. Routing mới thấy trong skill và mã hiện tại:
  Sonnet/Opus ở `claude_roles/supervisor.py`, AGY reviewer/guardian ở `coordination/supervisor.py`.
  Không sửa các bản legacy/recovery làm bằng chứng lịch sử.
- Test `test_agy_log_conversation_id_and_accept_edits_mode` đang kỳ vọng communicator
  accept-edits: phải đổi kỳ vọng sang plan theo yêu cầu mới, giữ kiểm tra native session ID.
- Xem lại quyết định WAITING_INPUT khi hết budget để đáp ứng handoff an toàn mà không
  tự replay hành động; không coi bounded stop là thành công khoa học hay crash.
- Kiểm tra quyền denied_actions và hook errors không bị che bởi nhánh error_max_turns.

## Trình tự tiếp tục

Hoàn thiện các backend đang thiếu và telemetry trước; chạy focused tests; sau đó làm
cloud snippet, shallow fetch, digest, bundle helper và campaign changes khi campaign idle.
Viết regression tests đúng danh sách trong đặc tả, gồm path ngoài root/symlink, receipt cũ,
hash mismatch/missing, log lớn/regex lỗi, role permissions, max-turns và budget handoff,
ledger không trùng, bridge giả lập và cleanup khi timeout.

Lệnh nghiệm thu dùng `.conda/python.exe`:

```powershell
.conda/python.exe -m unittest discover -s tests -v
.conda/python.exe main.py --no-env doctor
git diff --check
.conda/python.exe scripts/claude_usage_audit.py <out.json>
```

Audit hiện loại trừ session SELF hardcoded; cần đổi thành tham số có provenance.
Chỉ đối chiếu turns/cache-read thực tế sau một phiên mới có giới hạn; không nhận số audit
lịch sử không đổi làm bằng chứng tiết kiệm. Không commit trừ khi người dùng yêu cầu.
