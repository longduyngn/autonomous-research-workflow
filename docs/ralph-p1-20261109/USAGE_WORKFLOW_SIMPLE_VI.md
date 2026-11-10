# Usage và workflow — bản đọc nhanh

Đây là hướng dẫn chọn cách vận hành. Trạng thái nghiệm thu và các mốc test ở [RESULT_VI.md](RESULT_VI.md); dữ liệu truy vết ở [QUOTA_FINDINGS_VI.md](../../storage/ralph-p1-20261109/audit/QUOTA_FINDINGS_VI.md). Không có phép đo A/B về tiết kiệm quota.

## “Input/output token đang có những nhóm nào; giữ phần nào, bỏ phần nào?”

Cần phân biệt hai cách chia. **Counter của provider** cho biết số token; **loại nội dung** giúp quyết định tối ưu. Hiện chưa có mapping chính xác giữa mọi token và từng loại nội dung.

| Counter | Cách đọc đúng |
|---|---|
| Codex input | Đã gồm cached input; phần mới = input − cached input nếu cả hai trường có mặt. |
| Claude input chuẩn hóa trong audit | New input + cache creation + cache read. Giữ riêng ba counter để đối chiếu. |
| Output | Nội dung model sinh; reasoning là phần con nếu schema ghi như vậy, không cộng lần hai. |
| Counter tích lũy | Chỉ lấy delta trong đúng session/scope/epoch; không cộng các snapshot resume. |
| Thiếu counter | UNKNOWN, không điền 0. Không cộng ledger run totals với response totals của cùng run. |

Ví dụ có bằng chứng: trong 30 phút trước mốc Claude operator bị chặn quota lúc 11:10 ngày 08/11, 12 response có **26 new input + 41.692 cache creation + 11.137.413 cache read**, output **18.312**. Đây là context được xử lý qua nhiều response trong cùng phiên, không phải hơn 11 triệu token mới cho một task. Không thể đổi con số này thành phần trăm quota tài khoản hay mức tiết kiệm.

| Loại nội dung đi vào agent | Giữ | Giảm hoặc chuyển sang tool |
|---|---|---|
| Hướng dẫn nền, quyền, skill | Quyền, phạm vi, tiêu chí đạt; skill cốt lõi ngắn | Đưa hướng dẫn hiếm dùng sang reference; đọc khi cần. Native/global context vẫn có overhead, chưa được phân rã đầy đủ. |
| Yêu cầu và lịch sử chat | Nguyên văn yêu cầu gốc được lưu, quyết định hiện hành, task/run ID | Gửi summary có nguồn; tránh chép cả hội thoại qua từng agent. Memory không thay claim chống trùng. |
| Source/tài liệu truy xuất | Đoạn liên quan và đường dẫn/hash | Đọc theo câu hỏi; không mặc định quét lại repo hay toàn bộ skill. |
| Log và output của tool | Lỗi, tiến độ thực, log tail/search có giới hạn, receipt | File lớn nằm trên đĩa; tool trả kết quả ngắn và đường dẫn. Không đẩy toàn bộ JSONL/hash list vào chat. |
| Trao đổi giữa các role | Task, mục tiêu, checkpoint, bằng chứng và next action | Một chủ sở hữu; không thêm AI watcher rồi gọi researcher cho cùng một lỗi. |
| Suy luận và báo cáo | Suy luận khi quyết định chưa rõ; kết luận có bằng chứng | Dùng code cho poll/hash/dedup/thu thập trạng thái; không gọi model chỉ để hỏi job còn chạy không. |

Các hàng trên là **đề xuất tối ưu nội dung**, chưa phải số token tiết kiệm đã đo được. Session mới giúp tránh mang lịch sử không liên quan; operator chỉ resume khi vẫn cùng công việc và context còn cần thiết.

## “Có cần thêm role, có cần đẩy agent lên cloud không?”

Đề xuất hiện tại: **chưa thêm role AI thường trực**. Giữ communicator → researcher → operator; validator đánh giá kết quả. Watcher là code, chỉ gọi researcher khi có một vấn đề mới cần suy luận. Một incident có một owner và ngân sách xử lý; phản hồi muộn phải bị chặn sau khi task đã tiến triển.

Communicator chạy ở Windows để nhận yêu cầu; researcher có thể chạy local. Operator/validator ở nơi có dữ liệu và tài nguyên cần dùng: local, Colab hoặc VM. Chuyển phần compute lên cloud không tự làm giảm token của LLM. Chưa cần đưa tất cả agent lên cloud chỉ để phân vai.

Workflow đề xuất: điện thoại → Discord → inbox bền vững trên Windows → communicator hiểu ý → controller kiểm quyền và claim → researcher/operator làm việc → validator kiểm kết quả → communicator báo ngắn. Watcher code quan sát sự kiện/progress; khi agent review kết thúc, host gọi một headless continuation thay vì model polling.

Nếu Windows/controller chết, watcher trong cùng process cũng dừng. Muốn chạy liên tục cần cơ chế khởi động lại ngoài process và thử recovery sau restart. **Đợt này chưa cài dịch vụ, chưa nghiệm thu điện thoại/Discord thật.**

## “P1 chỉ cần prompt tốt hơn có được không?”

Prompt giúp communicator hiểu ý và agent biết khi nào đọc log. Những việc như một event chỉ tạo một task, ai được sửa task, owner nào còn hợp lệ, job UNKNOWN có được replay không và kết quả muộn có bị chặn không phải do code/DB kiểm. Persistent chat giúp hiểu context; durable claim mới xử lý được giao task trùng sau restart.

UNKNOWN chỉ đối chiếu read-only. Lỗi đã xác nhận mới đi qua checkpoint sửa và kiểm lại. Hết quota đi vào trạng thái chờ của router hiện có, không coi experiment thất bại rồi chạy lại. Agent cần báo người dùng khi thiếu quyền/tài nguyên/quyết định hoặc hết ngân sách phục hồi.

## “Tool deterministic khác skill thế nào?”

| Thành phần | Vai trò | Ví dụ |
|---|---|---|
| Tool deterministic | Thực thi thao tác cụ thể bằng code, kiểm điều kiện và trả receipt/lỗi | tail/search log có giới hạn, hash/verify artifact, claim task, đọc digest, thống kê usage. |
| Skill | Hướng dẫn agent khi nào và cách dùng tool, đọc reference và kiểm kết quả | Quy trình kiểm checkpoint, lấy đúng log khi lỗi, bàn giao nghiên cứu. |
| Agent | Hiểu ngôn ngữ tự nhiên, đánh giá tình huống chưa rõ và chọn hành động trong quyền | Làm rõ yêu cầu mơ hồ, chẩn đoán lỗi mới, đánh giá kết quả nghiên cứu. |

Skill không tự chạy nền và không thay scheduler, permission check hay DB transaction. Đặt thao tác lặp vào tool; giữ skill ngắn để agent biết dùng đúng tool.

## “P2 thống kê lại thế nào?”

Giữ nhóm **task → run/attempt → role → phase → provider/model**, kèm response ID, nguồn, cutoff và epoch. Báo riêng dispatch lỗi quota, retry, quota probe, guardian review và recovery; không mặc định một message lỗi tương ứng một native call.

Tool offline đã có 13 fixture tests và AGY review PASS. Producer telemetry mới của task tối ưu quota được bàn giao riêng tại [QUOTA_OPTIMIZATION_VERIFICATION_20261110.md](QUOTA_OPTIMIZATION_VERIFICATION_20261110.md). Hai phần phải được đối chiếu theo run ID; không backfill số đo thiếu bằng suy đoán. Phân bổ chính xác token theo system prompt/chat/log/source vẫn cần dữ liệu producer thích hợp; độ dài ký tự chỉ là proxy.

Để đo hiệu quả sau này, chọn workload tương đương trước/sau, ghi cùng provider/model, cấu hình, tiêu chí thành công và nguồn usage. Báo cả chất lượng kết quả, thời gian, số invocation, input/cache/output/retry. Cho đến khi có A/B phù hợp, không công bố hệ số tiết kiệm.

| Ưu tiên | Cần giữ/chốt | Acceptance |
|---|---|---|
| P0 | Đếm đúng và tránh tác động sai lifecycle | Không double-count; missing ≠ 0; UNKNOWN không replay; quota không đốt experiment retry. |
| P1 | Communicator + watcher code + recovery | Replay event sau restart chỉ tạo 1 task; actor/scope sai không mutation; một recovery owner; phản hồi cũ bị fenced; log bounded/redacted, không path escape. Xem receipt và review hiện hành trong RESULT. |
| P2 | Attribution và theo dõi hiệu quả | Dedup/conflict/counter-reset/cutoff theo schema; join đúng run; tách missing; chưa có A/B thì không gán tiết kiệm. |

Sau khi chốt review cục bộ, bước vận hành tiếp theo là một thử nghiệm điện thoại/Discord nhỏ: gửi một task, gửi lại cùng event để kiểm chống trùng, restart controller để kiểm khôi phục và thử một lỗi giả để kiểm recovery. Bước đó cần được người dùng cho phép riêng trước khi chạy live.

Usage của chính các lượt nghiệm thu cuối, gồm hai lần chưa chốt được và lần PASS, được tách riêng tại [CLOSURE_USAGE_VI.md](CLOSURE_USAGE_VI.md). Bảng này phân biệt invocation, counter Claude và counter AGY chưa rõ scope; không quy thành quota hoặc hệ số tiết kiệm.
