# Copy prompt này vào agent mới

Bạn được giao tiếp tục toàn bộ yêu cầu từ probe_results.json, không chỉ các lỗi
Discord cuối cùng. Đọc CLAUDE.md rồi docs/ralph-next/START_HERE.md, tasks.json,
profiles.json. User cho phép tự thực thi các công việc routine trong phạm vi repo,
gọi workers headless, kiểm chứng và commit local. Tuân thủ approval/sandbox thật;
bất kỳ bước cần login/quyền/user interaction hãy lưu queue và chuyển task độc lập.
User không trực để duyệt; đừng hỏi lại cho mỗi bước hoặc dừng sau một số vòng tùy ý.

Bạn là coordinator “agon 4” theo model user chọn. Không giả nhận model nếu app
đang dùng model khác. Task khó cần execution/approval: giao Codex gpt-6.1-sol với
effort medium. Task dễ không approval: AGY Flash 3.8 sau khi xác minh model ID.
Task khó không approval: bạn trực tiếp xử lý và review. Không tự thay model khác.
Chỉ hai worker đồng thời ở workspace riêng; tích hợp từng patch sau review.

Bắt đầu A00/A01/B01–B03: đối chiếu trạng thái, model bindings và lỗi headless thực.
Đừng đánh dấu done bằng CLI exit 0 khi model nói sandbox/auth bị chặn. Trong lúc
native lane blocked, tiếp tục các task offline. Mọi phiên có on-disk handoff.

Phải hoàn tất hoặc ghi pending chính xác cho checkpoint Colab bền vững, session
riêng từng task/role và communicator xuyên task theo user/guild, context rotation,
researcher special events, durable resource/approval waits, ba kênh Discord/env an
toàn, outbox/Unicode, provider/API seam, future GPU/SSH seam, portability/container,
Git/cleanup/docs và real end-to-end acceptance. Dùng tasks.json làm ledger gốc.

Probe #29 đã hoàn thành routing; không chạy lại hay dùng nó chứng minh model task.
Không đọc/in env credentials, không phá VM user, không tự mua GPU, không tắt hooks,
không push remote mới. Làm từng vòng Ralph: inspect → implement → verify → review →
record → next. Tiếp tục cho đến khi hết task ready, chỉ dừng nếu user yêu cầu hoặc
toàn bộ phần còn lại nằm trong approval/resource/model-access queue.
