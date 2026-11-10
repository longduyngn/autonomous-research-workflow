# Kế hoạch cải tiến P2 (2026-11-10)

Trạng thái: **ĐÃ CHỐT ĐIỂM MỞ — giao cho planner (Codex) lập kế hoạch thực thi chi tiết.**
Nguồn: trao đổi trong session Claude Code ngày 2026-11-10 (tương đương một đoạn chat Discord).
Planner chỉ lập kế hoạch, KHÔNG sửa code. Kế hoạch được chuyển nguyên văn cho người dùng duyệt.

## 0. Mục tiêu

1. Không bị dừng khi một agent hết quota: luân phiên Codex / AGY / Claude Code / API.
2. GPU: trước mắt **Colab + Kaggle**. VM thuê (Vast, Shadeform...) chỉ giữ interface.
3. Workflow rõ ràng: planner → người dùng duyệt → Ralph loop → validator kiểm tra chéo → communicator.
4. Duy trì được: lõi nhỏ, adapter có contract test, một task mẫu chạy end-to-end.

## 1. Quyết định người dùng đã chốt

| # | Quyết định |
|---|---|
| D1 | Communicator, workspace_watcher, world_watcher **cố định Gemini 3.8 qua AGY** (`gemini-3.8-flash-low`), không tham gia quota routing. Khi Gemini 3.8 hết quota/lỗi: fallback sang **model Claude/GPT bên trong AGY** (người dùng không dùng phần quota đó cho việc khác). Không fallback ra ngoài AGY. |
| D2 | Đổi tên `researcher` → `planner` toàn bộ code/test/config/docs hiện hành; alias cho dữ liệu cũ; không sửa biên bản có ngày. Planner bắt buộc output spec + tiêu chí chấp nhận đo được + danh sách việc cho Ralph loop (+ ngân sách chỉ khi người dùng nêu). |
| D3 | Chuỗi provider của planner: **codex_cli (gpt-6-astra) → claude_cli (opus) → agy gemini-4-argon-high**. |
| D4 | Planner lập plan thành file; plan chuyển thẳng cho người dùng duyệt (không cần review trung gian); chỉ sau khi duyệt mới thực thi. |
| D5 | Commit baseline phần việc đang dở sau khi test pass. |
| D6 | **Không giới hạn mặc định** giờ GPU/thời gian; chỉ áp dụng budget khi người dùng nêu rõ trong yêu cầu task. |
| D7 | Checkpoint/artifact nặng lưu ở **kho chung ngoài máy**; máy local chỉ nhận file nhẹ. Đề xuất chọn Google Drive (5 TB) làm kho chính, HF Hub tùy chọn (xem P2.5). |
| D8 | **Ưu tiên cao nhất sau baseline: provider API hoàn chỉnh dùng một agent loop chung** (kiểu OpenCode). |

## 2. P2.0 — Baseline

- Chạy `python -m unittest discover -s tests -v`, `python main.py --no-env doctor`, `git diff --check`; commit baseline.
- Không có task đang chạy trước khi thay listener/daemon.

## 3. P2.1 — Provider API hoàn chỉnh (ưu tiên cao nhất)

Hiện trạng: `core/providers.py::_run_http` đã có tool loop OpenAI-compatible tối thiểu
(tối đa 24 vòng, trần 100k token, chỉ xử lý 429 ở lượt đầu, không streaming, không nén context,
không resume session, không hook). `opencode_cli` có adapter nhưng OpenCode **chưa cài** trên máy.

Planner cần đánh giá và đề xuất một trong hai (khuyến nghị A):
- **A. OpenCode làm agent loop chung cho mọi model API** (OpenRouter, OpenAI, Anthropic, Gemini,
  DeepSeek, local...). Hệ thống chỉ cần adapter `opencode_cli` hoàn chỉnh: cấu hình provider/model
  theo role, phát hiện quota/rate limit theo từng nhà cung cấp, session resume, quyền tool,
  transcript, handoff. Ưu: loop trưởng thành, ít code tự duy trì.
- **B. Nâng cấp loop nội bộ `_run_http`**: quản lý context/nén, retry/backoff, phát hiện quota
  mọi lượt, resume từ handoff, giới hạn token theo role, streaming. Giữ làm fallback tối thiểu.

Yêu cầu chung: đăng ký vào quota router như account mới (`api:<vendor>`), có usage/cost theo task,
contract test bằng fake server, một smoke test live rẻ; key chỉ qua env, không log.

## 4. P2.2 — Đổi tên researcher → planner (D2)

- Config key `roles.planner`, alias `researcher` (cảnh báo deprecated).
- `roles/researcher.md` → `roles/planner.md`, cập nhật nhiệm vụ theo D2.
- Code/tests (~188 lần xuất hiện, 67 file): đổi tên, giữ hằng alias; bản ghi SQLite/handoff/native
  session cache cũ map sang `planner`, không viết lại lịch sử.
- Kiểm tra: test pass; `researcher` chỉ còn ở alias/migration/docs lịch sử.

## 5. P2.3 — Kênh Discord cố định Gemini 3.8 (D1)

- Cờ `pinned` cho role; quota router bỏ qua role pinned; chuỗi fallback riêng chỉ trong AGY.
- Hết quota ở role khác chỉ làm task hiện `WAITING_PROVIDER`; Discord vẫn trả lời.
- Nếu toàn bộ AGY không dùng được: tin nhắn mẫu không dùng LLM (trạng thái + đường dẫn).
- Kiểm tra: giả lập Codex+Claude hết quota → Discord vẫn phản hồi.

## 6. P2.4 — Provider contract và quota failover

- Kết quả chuẩn: `{status, output_path, handoff_path, quota_hit, reset_at, usage, error_class}`.
- Failover chỉ ở ranh giới vòng Ralph, dựa trên handoff ngắn có cấu trúc.
- Validator khác provider với operator khi có thể.
- Contract test fake cho từng adapter; smoke probe live hằng ngày; pin phiên bản CLI.

## 7. P2.5 — Compute (Colab + Kaggle) và kho lưu trữ chung

- `ComputeAdapter` chung: `submit / status / logs_tail / fetch_outputs / cancel`; job spec gồm
  script, config, môi trường, `checkpoint_uri`, outputs.
- Colab: bọc skill có sẵn `.agents/skills/colab-coccoc-playwright` + `colab-bridge`
  (tự approve kết nối MCP, chọn GPU, chạy nền, đọc log, ngắt runtime, kiểm tra Drive mount).
- Kaggle: tool chính thức (`kaggle kernels push/status/output`) + skill `kaggle-playwright-cancel`.
- **Poller**: tiến trình thường (không LLM) kiểm tra job định kỳ, chỉ đánh thức agent khi
  xong/lỗi/mất kết nối/metric bất thường. Tận dụng `core/monitoring.py`, `experiment_progress.py`.
- Checkpoint/resume bắt buộc; chuyển Colab ↔ Kaggle được.
- **Kho chung (D7)** — đề xuất:
  - Google Drive làm kho chính cho checkpoint và artifact nặng: Colab dùng `drive.mount`;
    Kaggle/local/VM dùng `rclone` (token OAuth lưu ở Kaggle Secrets / env, scope tối thiểu,
    thư mục riêng `workflow-runs/<task>/`). Lưu checkpoint thành ít file lớn, giữ N bản gần nhất.
    Lưu ý giới hạn upload ~750 GB/ngày của Drive.
  - HF Hub tùy chọn cho model/kết quả cuối muốn chia sẻ hoặc version hóa.
  - Máy local chỉ nhận: metrics, log đã tóm tắt, ảnh mẫu, báo cáo, và `manifest.json` trỏ tới URI
    trên kho chung. Tải checkpoint về chỉ khi người dùng yêu cầu.
- VM thuê: chỉ interface trong `docs/COMPUTE_EXTENSIONS.md`.

## 8. P2.6 — Workflow và tiết kiệm quota

- Planner → người dùng duyệt → Ralph loop (class worker, failover) → validator (provider khác) →
  communicator (Gemini 3.8).
- Subagent chỉ cho đọc/khám phá/review/tóm tắt log. Song song chỉ cho task độc lập (workspace riêng).
- Không dùng caveman cho handoff.
- Đánh giá (không bắt buộc) công cụ nén output lệnh: RTK (hook PreToolUse cho Claude Code; Codex chỉ
  qua hướng dẫn) — đo bằng `rtk gain` trên task mẫu trước khi bật mặc định.
- Rà plugin/MCP đang bật mỗi tháng (mô tả tool chiếm context cố định).

## 9. P2.7 — Task mẫu và bảo trì

- Mini-DDPM (CIFAR-10, vài epoch) trên Kaggle và Colab, checkpoint lên Drive, giả lập hết quota
  giữa chừng; kết quả nhẹ + manifest về máy; Discord báo trạng thái.
- Mọi thay đổi lớn phải pass task mẫu. Gom docs; biên bản có ngày vào `docs/archive/`; cập nhật CLAUDE.md.

## 10. Thứ tự

P2.0 → P2.1 (API provider) → P2.2 → P2.3 → P2.4 → P2.5 → P2.7; P2.6 làm xen kẽ.

## 11. Yêu cầu với output của planner

File `docs/ralph-p2-20261110/PLANNER_PLAN_VI.md` gồm: danh sách iteration Ralph (mỗi iteration một
việc, file sẽ chạm, cách verify, tiêu chí xong), rủi ro, điểm cần người dùng quyết định, và
lựa chọn A/B cho P2.1 kèm lý do. Không sửa file nào khác.
