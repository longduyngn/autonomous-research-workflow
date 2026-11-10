# Quyết định của người dùng sau khi đọc PLANNER_PLAN_VI.md (2026-11-10)

Các mục dưới đây **ghi đè** phần tương ứng trong `PLANNER_PLAN_VI.md` và `PLAN_DRAFT_VI.md`.

## D4' — Phạm vi duyệt plan (thay D4)

- Lần nâng cấp P2 này: duyệt plan trước khi thực thi.
- Task thông thường: **không** dừng chờ duyệt plan mặc định. Chỉ chờ `WAITING_APPROVAL` khi người dùng
  yêu cầu rõ trong task (ví dụ "duyệt plan trước khi chạy").
- Hệ quả cho P2.6-01: cổng duyệt là tùy chọn theo task (`plan_approval: required|none`, mặc định `none`),
  không phải bước bắt buộc của state machine. Các yêu cầu về hash/revision/owner/chống trùng vẫn áp dụng
  khi cổng được bật.

## D1' — Fallback trong AGY cho communicator / workspace_watcher / world_watcher

Model ID đã xác nhận bằng `agy models` ngày 2026-11-10. Mọi fallback đều đi **qua AGY**
(`agy_headless_safe`), không dùng `claude_cli`/`codex_cli`.

1. `gemini-3.8-flash-low` (chính)
2. `claude-sonnet-5-5-low` — Claude Sonnet 5.5 (Low) của AGY
3. `gpt-oss-120b-medium` — GPT-OSS 120B (Medium) của AGY
4. Toàn bộ AGY lỗi → tin nhắn mẫu không dùng LLM.

Thay thế đề xuất của planner (`claude-sonnet-5-5-medium`, `claude-opus-5-5-high`) ở mục 4.3.

## D9 — Provider API đầu tiên: OpenRouter, tự chọn model miễn phí

- Smoke live P2.1-07 dùng OpenRouter với model miễn phí, không cần người dùng chỉ định model cụ thể.
- Thêm vào P2.1 (iteration con P2.1-02b): khám phá model miễn phí tự động
  - Lấy danh sách `GET https://openrouter.ai/api/v1/models` (không tốn quota inference).
  - Lọc: giá prompt = completion = 0 (thường có hậu tố `:free`), hỗ trợ `tools` trong
    `supported_parameters` với role cần tool, context đủ lớn theo role.
  - Xếp hạng theo context/năng lực; lưu snapshot có thời gian; smoke một lượt rẻ trước khi đưa vào route.
  - Khi 429 / hết hạn mức ngày: cooldown đúng model/account rồi chuyển model free kế tiếp.
  - Giữ ràng buộc `free_only` (max_price 0): không bao giờ âm thầm rơi sang model trả phí.
- Key qua biến môi trường `OPENROUTER_API_KEY` do người dùng tự đặt; agent không đọc/in giá trị.

## D10 — Google Drive qua rclone

- Remote rclone tên `gdrive`; thư mục kho chung do người dùng tạo và báo tên/ID (không phải credential).
- Colab dùng `/content/drive/MyDrive/<thư mục>`; rclone dùng `gdrive:<thư mục>` hoặc `root_folder_id`.
- Scope OAuth: **`drive`** (người dùng chọn 2026-11-11) để tương thích hoàn toàn với `drive.mount`.
- Thư mục kho chung: folder ID **`1MbxfEz_tOOUknq-M5A0ELe4hMWzOUv-_`** (đặt làm `root_folder_id`
  của remote `gdrive`). Trên Colab cần xác định đường dẫn tương ứng dưới `/content/drive/MyDrive/`
  bằng tên thư mục, kiểm tra qua probe publish/restore.
- Trạng thái 2026-11-11: `OPENROUTER_API_KEY` đã có trong `.env` (chỉ kiểm tra tồn tại);
  rclone chưa cài, remote `gdrive` chưa cấu hình.
