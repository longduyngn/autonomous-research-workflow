# Kết quả P0/P1/P2 — P1 đã tích hợp, checkpoint bốn file đã review PASS (phạm vi hẹp)

Hướng dẫn ngắn: [vai trò, workflow và cách đọc usage](USAGE_WORKFLOW_SIMPLE_VI.md).

Số liệu của chính các lượt review/Claude tiếp nối: [CLOSURE_USAGE_VI.md](CLOSURE_USAGE_VI.md). Biên nhận kiểm chứng cuối của host: [FINAL_VERIFICATION.json](../../storage/ralph-p1-20261109/implementation/FINAL_VERIFICATION.json).

15 file P1 đã áp dụng sau AGY R6 PASS. Full suite tại phiên bản đã áp dụng: **326/326 PASS**, cutoff 09/11/2026 16:06:02 UTC. Đây là kết quả lịch sử, không phải full suite của source hiện tại. Bốn file cập nhật sau đó đã có **13/13 kiểm tra cục bộ PASS**. Review R7 lần 1 và lần 2 trả **NEEDS_EVIDENCE** (lần 1 thiếu biên nhận 13 test; lần 2 do hook/ranh giới phiên, không phải finding code) và được giữ nguyên làm lịch sử.

**Kết quả thực tế (cập nhật 10/11/2026): R7-four-checkpoint-review3 = PASS, không finding; finalizer đã chạy và trả `P1_FROZEN_CHECKPOINT_REVIEWED_AND_VERIFIED`.** [Phụ lục receipt-only](../../storage/ralph-p1-20261109/validation/prepared/R7_FOUR_RECEIPTS_SUPPLEMENT.md) (SHA256 `df7ccd19…ca00`) được gửi một lần tới AGY `gemini-3.8-flash-medium` (resume phiên `c192d78b…`, COMPLETED, exit 0, 33,5 giây). Xem [receipt](../../storage/ralph-p1-20261109/validation/R7-four-checkpoint-review3/receipt.json) và [verdict](../../storage/ralph-p1-20261109/validation/R7-four-checkpoint-review3/verdict.json). Phạm vi: bốn file đóng băng (orchestrator `fc781b48`, tools `a54ec021`, operator.md `5ee8994c`, researcher.md `3330abbc`) cùng hai backend đã capture, 13 test cục bộ; finalizer không thấy drift. Hash đã áp dụng (R6), hash đã review/đóng băng và hash quan sát được ghi riêng trong [CLOSURE_FOUR-freeze.json](../../storage/ralph-p1-20261109/implementation/CLOSURE_FOUR-freeze.json). Full326 vẫn là kết quả lịch sử, không gắn nhãn lại thành full suite hiện tại. Phone/live **NOT_RUN**; **không có global current-tree signoff**.

## “Hết quota lúc đang làm gì?”

Đã tìm được năm mốc có bằng chứng trong campaign SMILE. Giờ Việt Nam/Bangkok
ngày 08/11; token là cùng phiên trong 30 phút trước mốc, không phải toàn tài khoản.

| Giờ | Agent / việc đang làm | Input / output trước mốc |
|---|---|---:|
| 11:10 | Claude operator: trước đó ước lượng compute/status và kiểm snapshot/export/matrix; hành động cuối chuyển vấn đề ngân sách P1 bị quota chặn | 11.179.131 / 18.312, 12 response |
| 11:25 | Claude guardian: kiểm tiến độ/ngân sách P1 | 738.654 / 3.605 |
| 15:10 | Claude guardian: đối chiếu quan sát VM đã cũ, P1/P2 chưa xác nhận | 203.842 / 681 |
| 17:40 | Codex coordinator: viết phần bàn giao sang Claude/Gemini; snapshot account đạt 100% | 780.995 / 12.093 |
| 22:13 | Claude operator: P6/PPL, kiểm PID của lượt Argon review | 236.540 / 1.036 |

Mốc 11:10 có 11.137.413 cached input trên 11.179.131 input. Phần lớn lượng token
xử lý là đọc lại context. Không gán cả cửa sổ token cho riêng hành động lỗi cuối;
không suy % quota/cost hoặc mức tiết kiệm từ cached tokens.

Codex operator riêng hoàn tất trước snapshot account 100%, chưa có bằng chứng nó
bị từ chối trong turn cuối. Guardian có nhiều message lỗi quota, chưa chứng minh
số native invocation tương ứng. AGY gemini-3.8-flash-medium đã PASS review R0
trong phạm vi evidence bundle; không phải review toàn bộ source hay live pipeline.

Bằng chứng: storage/ralph-p1-20261109/audit/QUOTA_FINDINGS_VI.md và
QUOTA_INCIDENTS.json/.csv.

## “Cho communicator persist và giao task thay tôi”

Communicator tạo intent; controller kiểm danh tính inbox, scope và task trước khi lưu/submit/feedback. Yêu cầu gốc được giữ cùng intent chuẩn hóa. Durable claim chống tạo task trùng sau restart; actor/scope do model đề xuất không thay thế danh tính đã xác thực. Task terminal không nhận feedback làm thay đổi lịch sử; retry mới phải rõ ràng.

Log task chỉ được đọc khi có yêu cầu hoặc task đã chọn: nguồn cho phép, giới hạn dòng/ký tự, redaction và nhãn nguồn/thời điểm. Không cấp shell hay đường dẫn tùy ý. Điện thoại/Discord thật: **NOT_RUN**.

## “Watcher tự xử lý thay vì dừng và báo tôi”

Watcher dùng episode bền vững, một owner và số lần sửa có giới hạn; giao trực tiếp researcher/operator tại ranh giới vai trò. UNKNOWN chỉ đối chiếu read-only, không replay job. Quota chờ router hiện có; heartbeat/log chatter không tự gọi thêm model. Kết quả sửa cũ bị chặn khi task có tiến độ/trạng thái mới. Lỗi một job không dừng theo dõi các job khác.

Watcher nằm trong process controller. Nếu cả controller/máy chết, cần cơ chế restart bên ngoài; đợt này không cài dịch vụ hay restart listener.

| Mục | Tiêu chí acceptance | Bằng chứng / giới hạn |
|---|---|---|
| P0 | Đối chiếu mốc quota với phiên và việc đang làm | Source audit; không suy quota/cost tài khoản |
| P1 | Duplicate event, kể cả restart → 1 task | Fixture-tested |
| P1 | Actor/scope sai → 0 mutation | Fixture-tested |
| P1 | UNKNOWN → read-only, không replay | Fixture-tested |
| P1 | Incident lặp → một recovery owner, bounded attempts | Fixture-tested |
| P1 | Quota → chờ router hiện có | Fixture-tested |
| P1 | Recovery cũ bị fenced sau tiến độ mới | Fixture-tested |
| P1 | Log bounded/redacted, không path escape | Fixture-tested; 13 kiểm tra checkpoint mới PASS |
| P2 | Dedup response, schema/counter reset, tách UNKNOWN | 13 fixture tests; AGY R3 review2 PASS |
| P1/P2 | Phone/live acceptance | **NOT_RUN**; không suy từ fixture |

Các receipt 32 và 68 targeted PASS được giữ riêng, có phần coverage trùng; không cộng thành full suite. Lỗi Bash/WSL cũ và các lần kiểm tra thất bại vẫn được giữ. Full326 đã dùng Git Bash trong môi trường process con, không skip test.

## “P2 thống kê lại thế nào?”

Tool offline tách task/run/role/phase/provider/model, dedup response/message, xử lý schema Claude/Codex, counter epoch/cutoff và UNKNOWN. Review1 FAIL có 5 findings; bản sửa đã được AGY R3-scoped-review2 PASS. Không cộng ledger totals lên response totals; cache-read và cache-create giữ riêng theo schema, reasoning là phần con của output thì không cộng lần hai. Thiếu dữ liệu vẫn là UNKNOWN.

Producer telemetry mới (ledger/agent_runs) của chủ sở hữu khác là phạm vi riêng: [bàn giao kiểm chứng quota optimization](QUOTA_OPTIMIZATION_VERIFICATION_20261110.md). Chủ sở hữu báo full354 PASS cho task đó; không đổi nhãn thành suite P1. Kế hoạch/importer offline tại `storage/ralph-p1-20261109/audit/P2_USAGE_PLAN.md` giữ cutoff riêng. Chưa có A/B hoặc phép tính quota phần trăm/chi phí đáng tin cậy. `quota_probe` là usage category riêng; probe đã dừng trong lần test cũ có usage/cost **UNKNOWN** khi thiếu receipt, không phải zero.
