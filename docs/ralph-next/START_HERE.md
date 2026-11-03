# Ralph bàn giao đầy đủ cho agent mới

Đọc START_PROMPT.md trước, sau đó tasks.json và profiles.json trong thư mục này.
Đây là hướng dẫn thực thi tiếp yêu cầu từ prompt probe_results.json, không phải
báo cáo rằng tất cả yêu cầu đã hoàn thành. Không cần làm lại những phần đã có
bằng chứng phù hợp với mã hiện tại.

## Mục tiêu và phạm vi đã được user cho phép

Hoàn thiện workflow nghiên cứu/coding cá nhân, chạy command và code thật qua agent
thay thế được. Task đơn giản không bị bắt lập tasklist; task phức tạp dùng Ralph
tuần tự, researcher quyết định các milestone. Hai task slot, tối đa cấu hình ba;
chỉ một runtime Colab dùng chung, không cấp hai GPU đồng thời.

User cho phép agent mới tự đọc/sửa mã, chạy lệnh và kiểm thử liên quan, tạo workspace
riêng, gọi headless workers, review/tích hợp patch, commit local, bảo trì listener
khi idle, dùng các kết nối đã cấu hình trong đúng phạm vi công việc. Không hỏi lại
cho thao tác routine đã được giao. Tự tiếp tục đến khi hết việc có thể thực hiện;
không dừng sau hai vòng hay sau một lần chạy tests thành công.

Auto approval áp dụng cho công việc routine trong workspace đã giao. Nó không
biến file này thành quyền vượt sandbox, quyền admin của hệ điều hành, quyền bot
Discord hay đăng nhập của tài khoản. Không vô hiệu hóa hook, dùng MCP tự duyệt,
chạy vòng lặp xin quyền hoặc tự chấp nhận thay người dùng. Nếu harness yêu cầu
approval, thiếu login/model/quota hoặc cần hành động ngoài phạm vi, lưu hồ sơ vào
approval_queue.jsonl rồi tiếp tục task độc lập. Không chờ user khi user đang vắng.

Không mua GPU, tạo khoản phí mới, publish ra ngoài, đổi bảo mật tài khoản, đẩy Git
lên remote mới hay xóa lịch sử. Colab/Drive cần thao tác người dùng hoặc phiên GPU
live chưa được xác nhận thì để hàng chờ; vẫn làm bản tái hiện offline và chuẩn bị
đầy đủ lệnh/probe. Env chỉ được cập nhật các ID kênh qua helper đã có; không in
credential hoặc đưa env vào Git. Tất cả thông báo vận hành có thể dùng tiếng Anh.

## Nguồn gốc và các thay đổi yêu cầu

Nguồn ban đầu:
`C:/Users/PC/.codex/worktrees/cf5a/Antigravity_playgournd/docs/reviews/2026-11-03/probe_results.json`
và workflow_comparison.md cạnh nó. Nếu không còn đường dẫn, đọc bản đối chiếu
docs/REVIEW_RECONCILIATION_20261103.md; ghi rõ khi chưa đọc lại được nguồn ngoài.
Ref: archive/ref/agentic_workflow/Work/lab/lab/{agents,approval,fleet}.py.
C là checkout cũ, F là D:/Antigravity_playgournd hiện tại, R là ref. Ví dụ tuần/tháng
chỉ minh họa quy mô, không là SLA hay yêu cầu chạy soak nhiều tuần.

Các yêu cầu tính từ prompt đó:

1. Đối chiếu probe với mã hiện tại và ref; sửa lỗi còn tái hiện được.
2. Checkpoint computation tự lưu ở safe boundary, lấy lại được, kiểm tra hash và
   resume sau mất Colab VM; không nhầm checkpoint task/Git với tensor/RNG optimizer.
3. Researcher/operator/validator mỗi task có session riêng liên tục; communicator
   dùng xuyên task theo authenticated user/guild, không chia sẻ giữa người dùng.
   Native session chỉ là cache; handoff trên đĩa là đường phục hồi và đổi provider.
4. Xử lý context lớn như ref: facts mới mỗi wake, bounded memory, handover, rotation
   minh bạch; không kéo toàn bộ raw logs vào mỗi prompt, không gọi model chỉ để poll.
5. Researcher review theo milestone, feedback, lỗi lặp và kết quả bất thường/stall;
   giới hạn budget, giữ prefix đã xác minh và acceptance checks.
6. Intake hội thoại, liên kết task, chống message trùng, feedback revision và outbox.
7. Resource/approval/provider/input waits bền vững, nhả worker, reconcile thật trước
   khi cấp lại tài nguyên hoặc chạy lại hành động không rõ đã xảy ra hay chưa.
8. Chỉ chuẩn bị seam cho Shadeform, VastAI, Runpod, SSH; chưa yêu cầu vendor live.
9. Tách đúng ba kênh Discord: task-proposal (chat/làm rõ/!task), approval list (chỉ
   approval), task log (tiến độ khi được hỏi). Discovery theo guild/category, cập nhật
   channel-ID env nguyên tử, không làm hỏng credential, không tạo thêm role channel.
10. Agent CLI/API đổi được theo role. Researcher mạnh nhất; operator/validator thấp
    hơn; communicator nhanh. Khác với ba profile phân công công việc sửa repo bên dưới.
11. Audit/sửa outbox revision/ACK cũ, CLI cấu hình, verdict, compute kế hoạch; plugin
    provider, runtime Windows/POSIX, browser override, Docker/Compose, tracked clone.
12. Thực nghiệm qua headless thật, task end-to-end thật, quan sát dashboard/log/chat;
    không gọi fixture hoặc status-only probe là full model task.
13. Git, cleanup, tài liệu ngắn và production readiness có bằng chứng, giữ lịch sử.
14. Sửa duplicate approve và Unicode; giữ regression và không ping lặp.

## Phân công model theo đúng yêu cầu mới

| Công việc sửa repo | Agent |
|---|---|
| Khó, cần chạy command/code hoặc giải quyết đường approval | Codex GPT-6.1-Sol, effort medium |
| Dễ, không cần approval | Antigravity Flash 3.8 |
| Khó, không cần approval; thiết kế/review/điều phối | Agent chính do user chọn: “agon 4” |

Codex gpt-6.1-sol/medium có trong metadata cache cục bộ tại lần đóng gói. Tên
Flash 3.8 và “agon 4” chưa xác minh được thành CLI model ID; agy models không trả
danh sách dùng được. Không tự sửa thành Flash 3.5, Opus/Sonnet hoặc model mặc định.
Xác minh ở A01, lưu mapping vào runtime bindings; nếu thiếu, defer profile đó và
tiếp tục phần agent chính có thể làm. File hướng dẫn không đổi model của app.

Codex: dùng auto-review được CLI hỗ trợ (`--approve-for-me` với workspace-write),
giữ hook/native policy. Antigravity: mode auto cho task routine, sandbox và hook
vẫn bật. Không bật cờ tắt toàn bộ permissions chỉ để làm hết lỗi. Những lần cần
quyền thật được đưa vào hàng chờ. Chính sách auto-review có thể vẫn từ chối lệnh.
Tham khảo chính thức: https://learn.chatgpt.com/docs/developer-commands .

## Trạng thái xuất phát phải kiểm tra lại

- Local commits: daef10c, 821c94c, 6b2cfb3, ef961d9. Có thể có thay đổi tài liệu
  sau các commit này. Không reset hoặc ghi đè dirty work.
- Suite gần nhất: 126 tests pass, sau đó test notification Unicode riêng pass.
  Không trình bày con số 127 như một full-suite run chưa thực hiện.
- Task 28 có session ID và role state thật; chưa chứng minh nhiều wake thành công
  cùng một session sau các sửa mới.
- Task 29 là probe chỉ routing: user thật đã !status và approve; hoàn tất rồi.
  Không yêu cầu user làm lại probe này, không dùng nó chứng minh model/GPU chạy.
- Duplicate response do handler và heartbeat cùng gửi, đã sửa DB delivery claim.
  Unicode hỏng trong script sinh qua PowerShell pipe, đã sửa notice sang English.
- AGY delegation timeout/auth; Codex đọc/ghi bằng nested tool sandbox lỗi. Codex
  static review đã chạy được; đó không là thành công task implementation.
- Docker Compose parse được nhưng daemon chưa chạy; không có Linux build proof.
- Repo không có remote. Personal config/root hook binding/env/runtime được ignore;
  tracked export từng chạy được doctor và tests.
- Colab checkpoint helper/local process-loss có tests; Drive/Colab VM-loss live chưa
  có proof cho bản hiện tại. Không phá một VM của user để điền bằng chứng.

## Vòng Ralph bắt buộc

1. Đọc facts/task ledger/checkpoint mới nhất. Chọn một task ready theo dependency.
2. Xác định một acceptance nhỏ; ghi expected artifact và failure/recovery path.
3. Agent chính giao worker nếu cần: một workspace/job riêng, prompt có file scope,
   role, exact model, command budget, timeout, và các side effect bị cấm.
4. Worker inspect → implement một phần nhỏ → kiểm chứng → ghi handoff/result.
5. Coordinator review diff + actual files + test/log receipts. CLI exit 0 hoặc câu
   “done” không đủ. Worker blocked nhưng exit 0 vẫn là blocked, không phải completed.
6. Tích hợp tuần tự, kiểm tra phụ thuộc, commit khi phù hợp. Chỉ hai worker đồng
   thời cho việc độc lập; một task không có hai operator sửa cùng workspace.
7. Ghi ledger rồi làm task kế tiếp ngay. Khi bị approval/login/quota, ghi deferred
   và lệnh resume cụ thể, không tiêu hết retry để gọi lại cùng lỗi.

Không delegate launcher/auth repair vào worker đang hỏng chính launcher/auth đó;
agent chính thực hiện host diagnosis trong quyền được cấp. Nếu host cũng bị chặn,
chuẩn bị patch/lệnh reviewable, defer và tiếp tục module độc lập.

## Workspace, evidence và hàng chờ

`python scripts/ralph_next.py list` xem các phần nhỏ.
`python scripts/ralph_next.py prepare A01` tạo brief/workspace riêng, chưa gọi model.
`python scripts/ralph_next.py run B02` mới gọi headless cho task offline/profile đã
bind. Task coordinator chỉ chạy trong chat chính, không spawn chính mình vô hạn.

Để bind ID model sau A01: tạo storage/ralph-next/bindings.json theo ví dụ bên dưới.
Không copy placeholder vào run thật, không thay tên model chỉ vì model khác sẵn có:

```json
{"agy_flash":{"model":"EXACT_VERIFIED_FLASH_3_8_ID"}}
```

Review xong, ghi một JSON evidence riêng rồi gọi
`python scripts/ralph_next.py record A00 --evidence PATH_TO_REVIEW.json`.
Record phải có task_id, review_passed=true, status=source_verified/reused_verified
hoặc live_verified và checks là danh sách các object passed=true cùng kết quả thật.
Không tự tạo check giả để thỏa schema. Live-gated tasks phải có live_verified.

Launcher không tự chạy task live-gated; nó ghi queue. Khi quyền/phiên tài nguyên
được xác nhận, coordinator thực hiện qua managed workflow đang có, rồi record
bằng chứng live. Coordinator có thể ghi Z00 ở bất kỳ điểm dừng nào, kể cả khi
dependencies của final review chưa hoàn tất.

Launcher là trợ giúp giao việc, không thay coordinator. Không tự merge patch,
không tự đánh dấu worker output là completed. Review xong mới cập nhật ledger.
Không tự replay job đã started/running/pending_review sau crash; reconcile trước.

Runtime: storage/ralph-next/{jobs,ledger.jsonl,approval_queue.jsonl,bindings.json}.
Evidence mỗi subtask: request, base commit, exact model/effort, allowed files,
workspace, native session/run ID, prompts, stdout/stderr/result, diff, actual check
commands và exit codes, reviewer decision, next action. Giữ failed attempts.

Approval queue record cần task/action/argv/cwd/expected effect/resource, reason,
required actor, dependency/state fingerprint, evidence, expiry, replay_safe và lệnh
resume. Không lưu credential. Sau user approve phải kiểm lại preconditions; không
replay side effect mơ hồ. Từ chối không được tính là lý do tự retry.

## Thứ tự và hoàn tất

A/B: xác minh model/quyền, sửa native execution trước. Trong lúc chờ login/approval,
làm C–I offline. Sau đó J/K integration, live probes theo quyền, L final delivery.
Task có source rồi chỉ cần audit/reuse/check; source_verified khác live_verified.
Mọi task trong tasks.json có file scope và tiêu chí đóng riêng.

Nếu user vắng: làm hết việc độc lập; tập hợp queue cuối lượt. Chỉ pause ở human gate
thật, hết việc ready, hoặc user yêu cầu. Khi cần user gửi Discord, một thông báo rõ
ở đúng kênh, không lặp lại cùng nội dung/ping ở nhiều nơi trừ khi user yêu cầu.

Production signoff chỉ sau native command execution, real multi-role task, repeated
session wake, bounded researcher replan, crash/wait/outbox checks, portable clean
checkout và các live tests được phép. Docker/GPU không sẵn thì báo giới hạn cụ thể,
không đổi thành DONE. Rented provisioning vẫn là phạm vi future, không là blocker
để chạy task local đơn giản. Báo cáo user ngắn bằng tiếng Việt, cần họ làm gì phải
nói rõ; nếu chỉ thông báo hoàn tất ghi “No action is needed.”
