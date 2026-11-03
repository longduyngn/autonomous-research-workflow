# Đề xuất: hội thoại liên tục và Ralph tuần tự

Trạng thái: tài liệu đề xuất gốc (đã triển khai các vòng Ralph 01–09; xem trạng thái đối chiếu hiện tại tại `docs/PROPOSAL_STATUS_20261103.md` và `docs/RALPH_RESUMABLE_20261103.md`).

## Quyết định kiến trúc đề xuất

Một đầu mối hội thoại theo user/channel/thread, có lịch sử bền vững, nhận mọi
tin nhắn hợp lệ gửi cho bot. Không tự đọc mọi hội thoại trong server. Đầu mối này
trả lời câu hỏi, tìm task cũ, chuyển feedback và chỉ tạo task khi có ý định giao
việc. Researcher có bộ nhớ theo task; không dùng chung context toàn cục với
operator/validator. Native session chỉ là cache tùy provider; DB và tệp bàn giao
là nguồn phục hồi khi đổi model, đổi CLI hoặc mất session.

Giữ hai task slot như hiện tại, tối đa cấu hình ba. Trong mỗi task, mặc định một
nhánh tuần tự: researcher chọn bước, operator thực hiện, checker/validator đánh
giá, ghi checkpoint, researcher xem lại khi cần. Không thêm parallel branches.

## Thứ tự Ralph đề xuất

1. Lưu conversation/messages và intent events trước khi tạo task. Phân biệt chat,
   status, clarification, task update, new task, rerun, cancellation và approval.
   Chống lặp bằng message ID; câu giống nhau chỉ là gợi ý tìm task cũ, không phải
   lý do tự bỏ qua yêu cầu chạy lại. Một message có thể có nhiều intent.
2. Liên kết thread/reply với task; feedback có revision và checkpoint áp dụng.
   Yêu cầu mơ hồ giữa nhiều task cần hỏi rõ; không sửa prompt đang chạy âm thầm.
   Dùng outbox cho phản hồi và lưu sự kiện trước khi dispatch để khôi phục crash.
3. Tạo bộ nhớ task nhỏ: mục tiêu/phạm vi, quyết định có lý do, hiện trạng có
   evidence refs, thử nghiệm đã làm, câu hỏi mở và một next action. Tách ghi chú
   của model khỏi state/evidence do controller xác nhận. Mỗi checkpoint có
   revision; đọc state trước rồi mới truy xuất raw logs liên quan.
4. Researcher review theo sự kiện: milestone xong, kết quả bất ngờ, feedback mới,
   stall hoặc lỗi cần đổi chiến lược. Không gọi model cho từng dòng stdout.
   Đóng băng tiêu chí chấp nhận theo revision; validator phân biệt lỗi bắt buộc,
   thiếu bằng chứng bắt buộc và gợi ý bổ sung. Không tự đặt yêu cầu chặn mới.
5. Thêm WAITING_RESOURCE/WAITING_APPROVAL và hồ sơ action bền vững. Khi chờ,
   nhả worker slot nhưng giữ trách nhiệm tài nguyên/action. Tách active execution
   budget, thời hạn job remote và thời hạn chờ người dùng.
6. Thêm resource leases và phục hồi thực tế: Colab capacity=1, owner/task/run,
   generation/fencing token, remote identity, heartbeat và trạng thái unknown.
   Sau restart/lease expiry, reconcile VM trước khi cấp lại. Kiểm tra token tại
   executor gateway; không chỉ ghi token trong DB. Shutdown chưa xác minh thì
   quarantine tài nguyên. Nếu sandbox không chặn đường truy cập trực tiếp,
   fencing chỉ bảo vệ các thao tác đi qua gateway được quản lý.
7. Approval gắn đúng actor, task, action, workspace/revision, action hash,
   expiry và decision. Không cho phép model tự duyệt. Native permission bên ngoài
   vẫn có hiệu lực. Sau phê duyệt, kiểm tra preconditions rồi chạy đúng action;
   thay đổi command/đầu vào thì cần quyết định mới. Side effect không rõ đã xảy
   ra hay chưa phải reconcile thay vì tự replay. Denial không đồng nghĩa retry.
8. Claude: giữ contract role/provider độc lập; thêm JSON session metadata và
   explicit resume khi thực sự cần, một invocation tại một thời điểm mỗi session.
   Chạy smoke test hooks/skills/MCP/permissions bằng account thật trước khi công
   bố hỗ trợ live. OpenCode và so sánh chất lượng model để sau.

Mỗi mục triển khai riêng và kiểm tra trước khi chuyển mục tiếp theo. Kiểm thử
ưu tiên: follow-up không tạo task mới; duplicate/rerun phân biệt đúng; feedback
đến giữa hai checkpoint; crash trước/sau submit; chờ không chiếm cả hai slot;
Colab không có hai owner; approval cũ/sai người/sai action bị từ chối; chuyển
provider vẫn phục hồi đủ ngữ cảnh; validator không mở rộng phạm vi âm thầm.

## Căn cứ tham khảo

- Ralph gốc: https://ghuntley.com/ralph/
- Simple composable patterns: https://www.anthropic.com/research/building-effective-agents
- Long-running harnesses: https://www.anthropic.com/research/effective-harnesses-for-long-running-agents
- Context/memory: https://www.anthropic.com/research/effective-context-engineering-for-ai-agents
- Persistence: https://docs.langchain.com/oss/python/langgraph/persistence
- Human interrupts: https://docs.langchain.com/oss/python/langgraph/interrupts
- Claude headless: https://code.claude.com/docs/en/headless
- Claude project instructions: https://code.claude.com/docs/en/memory

Các nguồn trên gợi ý nguyên tắc; không chứng minh một kiến trúc tối ưu cho mọi
bài toán và không buộc repo phải đổi sang LangGraph hay một dịch vụ hosted.
