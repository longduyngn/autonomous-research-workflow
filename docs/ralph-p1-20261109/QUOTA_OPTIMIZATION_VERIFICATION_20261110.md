# Tối ưu quota/tool — nghiệm thu 2026-11-10

Tiếp tục từ bản tạm dừng 2026-11-09. Người dùng đã xác nhận **giữ soft budget**
trong lượt tiếp tục: không thêm `--max-turns` vào launcher vận hành, không dừng agent
chỉ vì vượt số lượt khuyến nghị. Giới hạn thời gian và giới hạn tool/token của API
vẫn là chốt cứng. Không commit, khởi động lại supervisor, chạy experiment hay cấp GPU.

## Kết quả triển khai

| Mục | Kết quả |
|---|---|
| 1. Artifact | AgentTools register/verify, CLI dùng chung; size/SHA-256 theo block, path trong workspace; receipt cũ vẫn đọc được. Bundle workflow-artifacts đưa lệnh ngắn lên đầu và legacy verify.py dùng backend chung. |
| 2. Colab snippet | Cloud schema và CLI; thực thi dưới fence của bridge, cần ownership có sẵn, từ chối job đang chạy/chưa đối chiếu và UI chưa xác nhận idle. Không đổi hardware/cấp VM. Cleanup khi có kết quả cuối; timeout/lỗi truyền tải giữ cell_id, không interrupt/replay. |
| 3. Fetch repo | Public GitHub HTTPS, clone depth 1, fallback tarball có giới hạn dung lượng/path; FILE_TREE.txt ghi kích thước, không thực thi mã tải về. |
| 4. Log | tail seek từ cuối, giới hạn byte/dòng; search regex trả số dòng/kết quả hữu hạn, cắt dòng dài. Sáu role prompt nhắc dùng views có giới hạn. |
| 5. Vai trò/budget | Giữ soft defaults communicator 5, validator 15, researcher 25, operator 35, guardian 20. Claude communicator/guardian Read/Glob/Grep; AGY communicator plan, deny command/write/MCP. Vượt budget là signal, không tự coi là crash. |
| 6. Context/usage | Claude runner ghi usage/turn/cost vào quota_ledger và agent_runs cả khi router tắt; run_key chống ghi trùng. Có digest <=2048 byte, helper update-status/checkpoint, skill core + reference, guardian luôn phiên mới, operator resume có rotation; helper export/bundle tái dùng mã hiện có. |

SQLite chỉ thêm cột/index, giữ nguyên dữ liệu cũ. Invocation run_key khác native session ID:
một session resume nhiều lần vẫn có nhiều invocation hợp lệ. Ledger token estimator tiếp tục
dùng input + cache creation + output; cache read lưu riêng trong usage_json. Không quy đổi
token hoặc native cost estimate thành hóa đơn/% quota. Không tự backfill lịch sử từ tổng audit.
Nếu telemetry thất bại, structured_data.telemetry_error giữ lỗi; không replay hành động.

## Campaign được sửa lúc không chạy

Đã kiểm tra PID **và create_time** của supervisor/child trong các supervisor.json trước
khi sửa: không có identity campaign nào còn khớp tiến trình sống. DB đọc mode=ro cho thấy
17 task COMPLETED, 16 ESCALATED, không có task active tại mốc kiểm tra. Không dừng CLI khác.

Launcher hiện dùng là `claude_roles/supervisor.py` (Sonnet/Opus) và
`coordination/supervisor.py` (AGY reviewer/guardian), không phải launcher AGY legacy.
Đã cập nhật hai file này, giữ bản trước tại
`storage/handoffs/quota-optimization-20261110/campaign-before/`.
Các file runtime state, checkpoint khoa học và script lịch sử không được ghi lại.
`tools/operator_helper.py` dẫn tới helper chung; export_row/build_bundle_row được tái dùng,
không chạy export/validator thật để kiểm thử. Helper cũ vẫn giữ giới hạn riêng của campaign SMILE.

Skill cốt lõi giảm từ **21.094 byte xuống 2.212 byte** (~89,5%). Toàn văn cũ được giữ
ở `references/campaign-details.md`; không mặc định nạp reference vào mỗi lần kiểm tra.
Digest campaign đo được **1.866 byte**, có đường dẫn/mtime và ghi rõ nguồn cũ không phải
quan sát live. Không coi state RUNNING trong snapshot cũ là bằng chứng job hiện đang chạy.

## Kiểm thử và bằng chứng

- 18 focused tests ban đầu PASS; bổ sung audit/fence và lỗi snippet để thành 22 regression tests.
- Full suite lần đầu: 350 test, 349 PASS, một lỗi môi trường WSL Bash E_ACCESSDENIED.
  Không sửa/skip test để che lỗi. Chạy lại với Git Bash có sẵn được ưu tiên trong PATH:
  **352/352 PASS**. Sau hai regression cuối cho trạng thái snippet không chắc chắn,
  chạy full suite cuối: **354/354 PASS**, 139,171 giây; kết quả ở
  `full-tests-final.log` trong gói bằng chứng. 22 regression tests mới đều nằm trong suite này.
- `main.py --no-env doctor`: exit 0; `git diff --check`: PASS (chỉ cảnh báo LF/CRLF).
- `skill-creator/scripts/quick_validate.py`: Skill is valid.
- CLI helper help và biên dịch hai supervisor campaign PASS.

Lệnh full suite đúng shell môi trường này:

```powershell
$env:PATH = 'C:\Program Files\Git\bin;' + $env:PATH
.conda/python.exe -m unittest discover -s tests -v
.conda/python.exe main.py --no-env doctor
git diff --check
```

Dùng skill-creator cho việc tách skill; chỉ template bundle sinh ra được thay đổi,
không ghi vào thư mục `.agents/.codex/.git` do môi trường quản lý.

## Audit và phiên thật

Audit mới bỏ SELF hardcoded, thêm --root/--exclude-session và provenance/cutoff;
dedup message ID, lấy max counter trên các fragment và dedup tool-use ID.
Các bản audit cũ vẫn được giữ trong handoff gốc; snapshot script cũ đã chạy trước khi sửa
ở `storage/handoffs/claude-optimization-before-20261110.json`.

Đã chạy audit cùng phiên bản mới trước/sau pilot:

```powershell
.conda/python.exe scripts/claude_usage_audit.py storage/handoffs/quota-optimization-20261110/audit-before-pilot.json
.conda/python.exe scripts/verify_quota_optimization.py --live
.conda/python.exe scripts/claude_usage_audit.py storage/handoffs/quota-optimization-20261110/audit-after-pilot.json
```

Pilot thật: native `claude-sonnet-5-5`, session
`3e5d16f9-4d59-46ab-93af-1583fa7846a7`, invocation
`4a4debae99ba435b82a3fd9aeb6d3797`.

| Số đo | Kết quả |
|---|---:|
| Thời gian runner | 6,828 giây |
| Turns | 1 |
| Input thường | 2 |
| Cache creation | 59.393 |
| Cache read | 0 |
| Output | 178 |
| Ledger tokens (input + creation + output) | 59.573 |
| Native cost estimate, USD | 0,239356 |
| Dòng quota_ledger / agent_runs | 1 / 1 |
| Telemetry error / permission denial | Không / không |

Audit native transcript khớp usage của result và DB; `AUDIT_COMPARISON.json` giữ đối chiếu.
Đã xác minh run key không tạo hai dòng khi router cũng ghi nhận; fixture tests kiểm tra
hai invocation khác nhau cùng session vẫn được tính riêng.

Đối chiếu lịch sử: audit lại session `29308ec0` xác nhận **301 turns**, context đỉnh
**965.304**, cache read **138.139.925** cho riêng session này (khác tổng hai guardian
152,7M của audit gốc). Counter mới đếm 84 chuỗi Get-FileHash trong tool calls đã dedup;
116 trong bản bàn giao gốc là số liệu do audit cũ cung cấp, khác phép đếm, **không phải
mức tiết kiệm**. Baseline headless ~8,5M/87,3% input và guardian 54% cache read vẫn được
giữ theo audit gốc, không thay bằng tổng account có thêm các phiên không thuộc campaign.

Pilot là **phiên native thật với fixture giả rất ngắn**, không phải cùng workload với
guardian 301 turns. Vì vậy không suy phần trăm tiết kiệm từ 301 -> 1 hay cache read -> 0.
Khoảng 59K cache creation cho một prompt nhỏ cũng cho thấy còn overhead native/global
context cần đo tiếp nếu muốn tối ưu account thực tế. Một campaign A/B sau triển khai
là bước nghiệm thu workload còn chưa thực hiện.

## Giới hạn và auto-review

Phiên trong sandbox với digest thật timeout 90 giây vì API retry, không có final usage
để xác nhận. Auto-review từ chối lần thử qua mạng với digest thật vì metadata nghiên cứu
riêng tư chưa được phép gửi cho payload đó. Không gửi lại payload bị từ chối.
Đã thay bằng fixture giả công khai và cwd tạm ngoài project; lần này được duyệt và PASS.
Chỉ fixture được gửi trong phiên thành công; không gửi digest/source campaign để thử lại.
Không tiếp tục gửi payload thật khi chưa có quyền rõ cho payload/destination đó.

Colab snippet chỉ có unit/fake-bridge/HTTP fence tests; chưa nghiệm thu trên runtime thật,
không cấp lại A100 chỉ để thử helper. UI/schema không nhận diện được thì fail closed.
Shallow fetch được kiểm thử bằng git/archive fixtures, chưa clone mạng thật trong lần này.
Campaign launcher đã sửa nguồn và kiểm tra cú pháp; chưa khởi động lại để nghiệm thu live.
Đây là hoàn thành mã/helper và kiểm thử cục bộ, không tuyên bố deployment hay replication khoa học.

Các sửa đổi P1 đồng thời từ thread khác được bảo toàn; phối hợp bằng hash bốn file giao nhau,
không tạo agent mới hay rollback thay đổi của thread đó. Gói snapshot/diff là trạng thái
tích hợp trên commit nền `d94c8d9`, không dùng làm patch độc lập cho checkout khác khi chưa đối chiếu.
