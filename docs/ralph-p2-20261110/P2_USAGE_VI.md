# Hướng dẫn sử dụng sau nâng cấp P2 (2026-11-11)

## 1. Các role và provider

| Role | Chạy bằng | Khi hết quota |
|---|---|---|
| planner (tên cũ researcher) | Codex `gpt-6-astra` | → Claude Opus → AGY Gemini 4 Argon high (đúng thứ tự, không đảo) |
| operator / validator | class `worker` của quota router | chuyển sang provider khác ở ranh giới vòng Ralph, có handoff |
| communicator, workspace_watcher, world_watcher | AGY Gemini 3.8 Flash low | AGY Claude Sonnet 5.5 low → AGY GPT-OSS 120B medium → trả lời không dùng LLM |

- Validator ưu tiên tài khoản khác operator để kiểm tra chéo.
- Model API miễn phí: provider `opencode_api_openrouter` (OpenCode + OpenRouter, tự chọn model free tốt nhất,
  không bao giờ dùng model trả phí). Xem danh sách: `python scripts/openrouter_free.py list`.
- Kiểm tra route hiện tại: `python main.py quota route planner --json`.

## 2. Giao task

- Mặc định task chạy luôn, không chờ duyệt plan.
- Muốn duyệt plan trước: thêm `--approve-plan` (CLI) hoặc viết "duyệt plan trước" / "approve the plan first"
  trong yêu cầu. Duyệt bằng `!approve <id>` trên Discord hoặc
  `python main.py control approve <id> --actor <owner>`.
- Không có giới hạn thời gian mặc định. Muốn giới hạn: nói rõ trong yêu cầu (planner ghi `budget_seconds`).

## 3. Lưu trữ và kết quả

- Checkpoint/file nặng: Google Drive qua rclone, thư mục của bạn
  (`gdrive:` + folder ID trong `config/workflow_config.json` → `storage`), dưới `workflow-runs/<task>/<run>/`.
- Máy local chỉ nhận file nhẹ + `manifest.json` + `artifact_index.json` tại `storage/results/<task>/<run>/`.
- Kiểm tra Drive: `python scripts/verify_p2_storage.py --store drive` (đã PASS ngày 2026-11-11).

## 4. Kiểm tra định kỳ

```powershell
python -m unittest discover -s tests          # toàn bộ test
python main.py --no-env doctor                # có mục cli_version_pins
python scripts/verify_p2_providers.py --offline   # lệch phiên bản CLI
python scripts/verify_p2_api.py --offline --with-cli
python scripts/verify_p2_e2e.py --offline
```

Nâng cấp CLI (codex/claude/agy/opencode) chỉ sau khi `verify_p2_providers.py --offline` và một smoke live đạt,
rồi cập nhật `config/provider_versions.json`.

## 5. Việc còn cần bạn (live)

1. **Kaggle → Drive:** tạo Kaggle Secret tên `RCLONE_GDRIVE_CONF` (không dùng `RCLONE_CONFIG`, vì rclone hiểu
   biến đó là đường dẫn file). Giá trị: toàn bộ output của `rclone config show gdrive` (gồm dòng `[gdrive]`).
   Ô đầu tiên của notebook do adapter chèn sẽ đọc secret, ghi `~/.config/rclone/rclone.conf` (không in ra),
   đặt `root_folder_id` của thư mục Checkpoint và tải rclone v1.75.2 nếu chưa có.
2. **Colab:** dùng `drive.mount` (skill `colab-coccoc-playwright/scripts/drive_checkpoint.py`); đường dẫn là
   `/content/drive/MyDrive/Checkpoint/workflow-runs` = `gdrive:workflow-runs` với `root_folder_id`.
3. **Discord:** chạy listener khi bạn sẵn sàng và gửi một `!task` thử để có receipt gửi/nhận thật.
4. **Autopilot:** `scripts/p2_autopilot.py` chỉ phục vụ việc thực thi kế hoạch nâng cấp P2 (đã xong, có
   `P2_AUTOPILOT_COMPLETE`), nên không cần cài. Task thường đã có failover provider trong controller.
