# Ralph P1 — communicator, watcher tự phục hồi và truy vết quota

Người điều phối: agent đang trao đổi trực tiếp với người dùng. Implementation:
Codex gpt-6.1-sol, medium. Independent validation: AGY gemini-3.8-flash-medium.
Không tự thay model khi không truy cập được. Catalog không đồng nghĩa chạy thật.

## Yêu cầu người dùng và phạm vi đã được phép

- Truy lại lần campaign gần nhất: Claude/Codex hết quota lúc đang làm task nào,
  phân biệt usage quan sát, lỗi quota thực và những điều không truy được.
- Giữ phần auto quota routing đang được người dùng triển khai; không viết lại.
- Communicator persist theo actor/conversation, có thể gửi task và chuyển feedback
  của người dùng cho researcher, đọc trạng thái/log liên quan để trả lời hữu ích.
- Watcher theo dõi bằng code, gọi agent khi cần và tiếp tục vòng sửa/kiểm thay vì
  ngay lập tức dừng và báo người dùng. Prompt là một phần; quyền/state/claim phải
  được code kiểm. Không thêm các agent heartbeat vô ích.
- P2: xuất thống kê theo task/run/role/phase/provider/model và sự kiện quota.
- Chỉ local implementation và tests/fake adapters. Không gửi Discord thật, chạy
  lại campaign đóng, mở GPU, thuê VM, cài dịch vụ/startup hay push/deploy.
- Không đọc/in credential, .env, auth.json, .credentials hay gửi raw hội thoại
  sang provider khác. Worker auditor được đọc ledger campaign theo phạm vi pin;
  AGY reviewer chỉ nhận source/patch/tests/numeric evidence được chọn.

## Trình tự Ralph và acceptance

| Checkpoint | Codex làm | AGY 3.8 kiểm độc lập | Điều kiện chuyển tiếp |
|---|---|---|---|
| R0 | Snapshot dirty baseline, truy quota, inventory code hiện có | Scope, dedup, timeline, không giả token/quota | Có bản đồ incident hoặc UNKNOWN với lý do |
| R1 | Communicator intent/task/feedback + state/log summary giới hạn | Actor/scope, dedup, ambiguity, không giả queued | Test submit một task; replay không trùng; feedback đúng revision |
| R2 | Watcher → operator/researcher recovery có ngân sách, evidence | Không replay job UNKNOWN, không sửa acceptance, không report quá sớm | Fake transient hồi phục; lỗi cần reasoning được route; quota chờ đúng; terminal không thức |
| R3 | Báo cáo usage và ghi attribution phục vụ P2, tái dùng quota ledger | Per-provider schema, cutoff, missing/null, no A/B claims | Import idempotent, output task/phase rõ, raw content không export |
| R4 | Integration giữ dirty baseline + focused/full required checks | Source hashes, test receipts và limitations | Chỉ tích hợp patch đã review; regression tương thích quota hiện có |

R0 auditor và R1 implementation có thể chạy song song vì khác file scope. Chỉ
tối đa hai worker đồng thời. Implementation làm từng checkpoint và dừng tại
review gate; reviewer FAIL → đúng worker sửa rồi review lại. Không tái chạy những
probe live cũ đã đủ bằng chứng. Ledger riêng cho đợt này; không reset catalog cũ.

## Mô hình vận hành mong muốn

Communicator hiểu và diễn đạt ý người dùng, controller commit intent vào inbox/
task/feedback qua API có kiểm quyền. Conversation memory không thay được DB claim.
Watcher code quan sát progress thật; log chatter/heartbeat không chứng minh tiến
triển. Lỗi quen thuộc có recovery recipe; lỗi cần suy luận giao role đang sở hữu
task hoặc researcher. Không cho watcher và operator cùng mutate một job.

Incident mới gửi gói nhỏ: task/run/job, error type, last useful progress, log tail,
artifact/checkpoint refs, attempts, authority và next action. Dedup theo episode;
cùng lỗi không liên tục gọi lại model. Khi phục hồi phải ghi evidence kiểm lại.
Chỉ báo user khi cần quyền/tài nguyên/quyết định thật hoặc đã hết ngân sách phục
hồi; vẫn tiếp tục công việc độc lập. Quota provider không phải experiment failure.

## Quy tắc workspace và bằng chứng

Worker làm trong snapshot riêng tại storage/ralph-p1-20261109/worker. Baseline giữ
file hashes của dirty tree hiện tại, không dựa vào HEAD vì có nhiều thay đổi người
dùng chưa commit. Chỉ integrate file nếu root vẫn khớp hash lúc snapshot; nếu đã
đổi thì reconcile từng hunk, không overwrite. Giữ failed attempts. Không commit
gộp các thay đổi không thuộc đợt này.

Artifact mỗi checkpoint: request, exact model/effort, session/run ID, file scope,
baseline hash, patch, actual test command/exit/output, reviewer receipt/verdict,
next action. Source-only PASS không được trình bày là phone/live acceptance.

## Tình trạng ban đầu

Đã xác minh gpt-6.1-sol trong model cache. AGY models trong sandbox gặp lỗi profile
write/auth/network; cần kiểm bằng native execution được cấp quyền, không đổi
provider/model và không sao chép token. Chưa gọi inference để kiểm catalog.
