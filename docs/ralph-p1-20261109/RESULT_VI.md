# Kết quả đến gate review — chưa áp dụng vào hệ thống chính

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

Codex Sol đã chuẩn bị patch: communicator trả intent trong một lượt gọi; controller
kiểm actor/scope/task và lưu yêu cầu gốc trước khi submit/feedback. Có durable
dedup và xử lý restart, hỏi lại khi mơ hồ, từ chối proposal lỗi, đọc log task theo
yêu cầu có giới hạn/redaction. Log đầy đủ không đi vào mọi lượt chat.

Persistence giúp hiểu ý; DB claim vẫn ngăn cùng event tạo hai task. Không trao
shell/quyền duyệt hành động tùy ý cho communicator. R1 focused: 33 tests pass.

## “Watcher tự xử lý thay vì dừng và báo tôi”

Ứng viên R2 dùng watcher code; sau check-in, giao thẳng researcher sở hữu task,
không mặc định thêm một AI watcher rồi mới thêm researcher. Mỗi đợt lỗi có ID,
owner, giới hạn và bằng chứng kiểm lại. Telemetry thiếu không thành job FAILED;
UNKNOWN chỉ được đối chiếu read-only; confirmed FAILED dùng đường sửa/checkpoint
có sẵn. Quota chờ theo router hiện có, không đốt experiment retry. Phản hồi cũ bị
chặn khi tiến độ/task đã đổi. Một job lỗi không được làm dừng theo dõi job khác.

Đã sửa trong ứng viên việc hook không nhận tham số Path của tool AGY thật. Hook
và API path có tests giới hạn đọc. Watcher vẫn nằm trong controller: nếu cả máy
hoặc controller chết thì cần cơ chế restart ngoài process; đợt này không cài dịch vụ.

R2 focused: 120 tests pass. Bản ghép R1+R2: 68 focused pass; full suite 303/304
pass, lỗi duy nhất là Bash/WSL E_ACCESSDENIED đã tái hiện trên baseline gốc. Không
skip/sửa để che lỗi. Doctor --no-env exit 0. Chưa test điện thoại/Discord thật.

## “P2 thống kê lại thế nào?”

Đã có tool offline có 13 fixture tests và kết quả tái chạy ổn định. AGY review lần
đầu FAIL; Codex sửa đủ 5 findings, thêm 7 regression tests; AGY review lần hai PASS:

```powershell
python storage/ralph-p1-20261109/audit/tools/investigate_smile.py
python storage/ralph-p1-20261109/audit/tools/usage_report.py --manifest storage/ralph-p1-20261109/audit/PINNED_USAGE_MANIFEST.json --out storage/ralph-p1-20261109/audit/usage
```

Nó tách task/run/role/phase/provider/model, dedup response/message, phân biệt
schema Claude/Codex, counter epoch/cutoff và UNKNOWN. Không cộng ledger run totals
lên response totals. Kế hoạch producer instrumentation tương lai ở audit/P2_USAGE_PLAN.md;
chưa tuyên bố đã có phân bổ token chính xác theo mọi loại nội dung trong production.

## Trạng thái sau khi người dùng cấp quyền AGY

Người dùng đã cấp quyền: “cho phép agy truy cập tiến hành task”. Gói đã lọc được
gửi theo phạm vi đó. AGY R4-scoped-review1 PASS cho R1/R2/INTEGRATION; AGY
R3-scoped-review2 PASS cho tool thống kê sau khi sửa 5 findings. Không hỏi lại quyền
cũ. Patch và bản ghép nằm trong storage/ralph-p1-20261109/.

Automatic approval từ chối gửi bundle source/tests/receipt đầy đủ cho AGY vì phạm
vi dữ liệu được gửi chưa đủ cụ thể. Đã chuẩn bị gói hẹp hơn tại validation/prepared:
R4_FILTERED_REVIEW.md (diff R1/R2 + test mới + tóm tắt test) và R3_FILTERED_REVIEW.md
(tool/test mới). Không gồm raw chat/reasoning, credential/.env, dữ liệu nghiên cứu,
log nguyên bản, native session/account metadata hay unchanged full source files.
Lần từ chối trước được giữ làm bằng chứng lịch sử; không phải blocker hiện tại.

Codex đang tích hợp theo allowlist/hash của 15 file P1 và giữ nguyên sáu file quota
người dùng đã cập nhật đồng thời. Cấu hình quota mới có Claude model probe thật,
Codex app-server và AGY readiness probe; các fixture CLI cũ cần tách khỏi mặc định
này để unit tests không tự gọi provider. Một lượt test isolated chưa chặn probe
đã được dừng, receipt giữ nguyên, usage của probe UNKNOWN khi không có biên nhận.
Harness mới fail-closed với native provider và không bỏ test. Sửa fixture test
được tách review riêng trước khi áp dụng. Chưa có deploy, GPU, Discord hay restart
listener. Kết quả production integration cuối cùng sẽ ghi trong STATUS.json.

Lưu ý P2: quota_probe là một nhóm usage riêng vì probe Claude mới thực hiện lời
gọi model. Không mặc định việc đo quota là miễn token và không gán thay đổi mới
này làm nguyên nhân các mốc hết quota trong campaign cũ.
