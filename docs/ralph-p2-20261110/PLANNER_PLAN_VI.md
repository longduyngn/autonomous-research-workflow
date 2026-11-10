# Kế hoạch thực thi P2 (planner)

Ngày lập: 2026-11-10.  
Đầu vào chính: [PLAN_DRAFT_VI.md](PLAN_DRAFT_VI.md), giữ nguyên các quyết định D1–D8.  
Trạng thái: **Kế hoạch để người dùng duyệt; chưa thực thi.**

Lượt planner này chỉ đọc tài liệu, mã nguồn và trạng thái Git; không sửa file, chạy test, gọi model, chạy GPU hoặc thao tác Discord/Colab/Kaggle. Các lệnh bên dưới là yêu cầu thực thi và nghiệm thu dành cho Ralph sau khi kế hoạch được duyệt.

## 1. Kết luận và phạm vi

**Chọn A: OpenCode làm agent loop chung cho các provider API.** Controller tiếp tục sở hữu vòng Ralph, duyệt kế hoạch, quota routing, trạng thái task, tài nguyên và bằng chứng. Giữ `_run_http` làm đường tương thích tối thiểu, được cấu hình rõ ràng.

Các ràng buộc xuyên suốt:

- **D1:** `communicator`, `workspace_watcher`, `world_watcher` dùng `gemini-3.8-flash-low` qua AGY; fallback chỉ sang Claude/GPT **bên trong AGY**.
- **D2–D3:** tên chuẩn là `planner`; chuỗi mặc định đúng thứ tự `codex_cli/gpt-6-astra → claude_cli/opus → AGY/gemini-4-argon-high`.
- **D4:** plan được lưu thành file và chuyển thẳng cho người dùng. Chưa duyệt đúng nội dung plan thì operator chưa được chạy.
- **D5:** xác minh và tái sử dụng baseline đã có; chỉ commit thêm khi thực sự còn thay đổi thuộc phạm vi.
- **D6:** task mới không có ngân sách thời gian/GPU mặc định. Ngân sách thực thi chỉ xuất hiện khi người dùng nêu.
- **D7:** Google Drive là kho chính cho checkpoint/artifact nặng; local nhận kết quả nhẹ và manifest. HF Hub tùy chọn.
- **D8:** hoàn thành P2.1 ngay sau baseline, trước đổi tên và các nâng cấp compute.

P2 không triển khai provisioning VM thuê, mở rộng thành GPU fleet hoặc thay các CLI subscription hiện có bằng OpenCode.

## 2. Những phát hiện từ code ảnh hưởng trực tiếp đến kế hoạch

| Khu vực | Hiện trạng đã đối chiếu | Hệ quả triển khai |
|---|---|---|
| Baseline | HEAD là `cab220d`, thông điệp commit đã bao gồm baseline, quota optimization và bản nháp P2. `git status --short` chỉ hiện `.claude/worktrees/` chưa theo dõi. | P2.0 phải xác minh baseline, không `git add .`, không tạo commit rỗng hoặc thu gom worktree khác. |
| HTTP loop | `_run_http` có tool loop, nhắc soft budget, giới hạn vòng/token và handoff khi hết vòng. `_post` chuyển `HTTPError` thành `RuntimeError`, trong khi `_run_http` lại bắt `HTTPError` để xử lý 429. | Nhánh 429 hiện không bắt được lỗi HTTP thật đi qua `_post`; cần regression bằng fake HTTP server. |
| OpenCode | `_run_cli` đã gọi `run --format json`, lưu transcript/session ID và đặt quyền. Chưa truyền session ID vào lần chạy tiếp theo; parser chủ yếu đọc text/error. | Có thể mở rộng adapter hiện hữu, nhưng chưa thể coi session resume, usage hoặc terminal result là hoàn chỉnh. |
| Provider contract | `AgentRunResult` gồm `success`, `output`, `error`, `structured_data`; `ProviderRegistry` có seam cho adapter ngoài. | Mở rộng contract có lớp tương thích; không cần thay toàn bộ dispatcher cùng lúc. |
| Quota | Đã có account, cooldown, ledger, sticky route, `requires`, `WAITING_PROVIDER`. `account_for` chưa tự nhận diện API vendor. | Thêm account API và chính sách theo role; giữ ledger hiện có. |
| Thứ tự planner | Router hiện xếp theo sticky route, thời điểm reset, quota còn lại rồi mới tới thứ tự config. | Chỉ đảo danh sách `frontier` không bảo đảm D3; phải có chế độ chuỗi có thứ tự riêng. |
| Pin role | `source='pinned'` hiện nghĩa là task có `provider_override`, chưa phải role pin theo D1. | Phải phân biệt `task_override` và `role_pinned`; sửa cả các điểm gọi ngoài task pipeline. |
| Duyệt plan | `_execute_researcher` lưu brief/plan rồi chuyển thẳng sang `OPERATING`. | D4 cần thay state machine, không chỉ sửa prompt. |
| Alias | Tên cũ nằm trong enum, SQL chọn task, recovery, role memory, plan parser, scripts và tests. | Thay tên bằng compatibility layer; không tìm-thay mù trên DB và lịch sử. |
| Compute | `core.compute.ComputeAdapter` hiện là interface VM thuê: `provision/observe/stop/download`. Colab/Kaggle nằm trong `CloudTools`. | Tách lifecycle tài nguyên thuê khỏi lifecycle job; giữ extension thuê máy ở trạng thái disabled. |
| Polling | `ProgressStore`, `poll_job`, `Supervisor` và scheduler đã tồn tại. Poll và diagnosis là hai đường khác nhau. | Tái sử dụng nền này, bổ sung event/wake có chống trùng; không dựng một hệ thống giám sát song song. |
| Storage | `checkpoint_publish` hiện sao chép vào kho controller; `kaggle_export_data` tải toàn bộ output. `experiment_job_artifacts` yêu cầu file local. | Phải sửa cả publish, fetch, xác nhận artifact và resume để thực hiện D7. |
| Thời gian | Có mặc định task/wait 86.400 giây, job 43.200 giây, Kaggle 36.000 giây và agent 21.600 giây ở nhiều lớp. | D6 cần thay đồng bộ config, DB, controller, monitoring và worker instrumentation. |

Báo cáo nghiệm thu P1 trong repo ghi nhận 354 tests pass; đây là **bằng chứng đã lưu**, không phải kết quả test của lượt lập kế hoạch này.

## 3. Đánh giá A/B cho P2.1

| Tiêu chí | A — OpenCode | B — nâng `_run_http` |
|---|---|---|
| Tận dụng code hiện có | Mở rộng nhánh CLI, role memory, transcript và registry đã có. | Tận dụng `AgentTools`, nhưng phải xây thêm nhiều thành phần của agent runtime. |
| Đa vendor | Dùng cấu hình provider và runtime của OpenCode. | Code hiện chỉ có loop OpenAI-compatible; Anthropic/Gemini native cần thêm lớp chuyển đổi. |
| Context/session | Tích hợp resume và compaction của runtime, controller vẫn giữ handoff chuẩn. | Phải tự thiết kế lưu hội thoại, compaction, tool-call identity và recovery. |
| Quyền và quan sát | Cần kiểm thử nghiêm schema quyền, tiến trình con và event stream của phiên bản được pin. | Dễ kiểm soát dispatch Python hơn, nhưng shell vẫn không trở thành sandbox chỉ nhờ kiểm tra tên tool. |
| Bảo trì | Phụ thuộc phiên bản CLI; giảm lượng logic agent loop tự duy trì. | Ít phụ thuộc CLI nhưng tăng đáng kể code runtime và ma trận lỗi phải bảo trì. |
| Độ phù hợp P2 | Phù hợp D8 và mục tiêu lõi nhỏ. | Phù hợp làm đường dự phòng tối thiểu; quá lớn nếu làm loop đa vendor chính trong P2. |

OpenCode hiện có lệnh chạy tự động, JSON output, resume theo session cụ thể và export session. Đây là các khả năng phù hợp với seam hiện tại của repo. [Tài liệu CLI OpenCode](https://opencode.ai/docs/cli/commands/)

Có một thay đổi cần kiểm tra trước triển khai: tài liệu quyền hiện hành phân biệt V1/V2; V2 dùng `permissions`, `shell`, `subagent`, trong khi adapter đang tạo quyền kiểu `bash`/`task`. Không coi test mock hiện tại là bằng chứng cấu hình đó có hiệu lực trên phiên bản mới. [Tài liệu quyền OpenCode](https://opencode.ai/docs/permissions/)

**Quyết định triển khai:**

1. Pin một bản OpenCode cụ thể sau kiểm tra tương thích; không dùng `latest` trong vận hành.
2. Một implementation adapter phục vụ nhiều profile API; profile khác nhau chỉ ở vendor, model, endpoint và account.
3. Controller quyết định provider ở ranh giới Ralph. Không để OpenCode tự chuyển sang account/model ngoài route đã chọn.
4. `_run_http` chỉ được sửa lỗi chuẩn hóa, quota, handoff và chống replay; không xây thêm một hệ thống compaction/streaming đa vendor thứ hai.
5. Nếu phiên bản OpenCode được chọn không vượt qua kiểm thử quyền hoặc session, giữ P2.1 chưa nghiệm thu và xử lý compatibility; không âm thầm chuyển lựa chọn sang B.

## 4. Thiết kế các contract chính

### 4.1. Kết quả chuẩn của provider

Mở rộng `AgentRunResult` theo hướng tương thích, với payload chuẩn phiên bản 1:

```json
{
  "schema_version": 1,
  "run_id": "<invocation-id>",
  "role": "operator",
  "provider": "opencode_api_openrouter",
  "model": "<provider/model>",
  "quota_account": "api:openrouter",
  "status": "COMPLETED",
  "output_path": "<task-relative-path>",
  "handoff_path": "<task-relative-path-or-null>",
  "quota_hit": false,
  "reset_at": null,
  "usage": {
    "input_tokens": null,
    "output_tokens": null,
    "cache_read_tokens": null,
    "cache_write_tokens": null,
    "cost_usd": null,
    "source": "unavailable"
  },
  "error_class": null
}
```

Quy tắc:

- `status`: `COMPLETED`, `INCOMPLETE`, `WAITING_PROVIDER`, `WAITING_INPUT`, `FAILED`, `CANCELLED`, `UNKNOWN`.
- `error_class` tách `RATE_LIMIT`, `QUOTA_EXHAUSTED`, `AUTH_REQUIRED`, `MODEL_UNAVAILABLE`, `PERMISSION_DENIED`, `CONTEXT_LIMIT`, `TRANSPORT`, `PROTOCOL`, `TOOL_FAILED`.
- `reset_at` chỉ chứa thời điểm do nguồn cung cấp hoặc suy luận có provenance; không giả một cooldown thành quota reset đã đo.
- Usage/cost không được cung cấp phải là `null`, không mặc định thành số 0. Giữ raw usage đã lọc để kiểm tra cách cộng cache/token.
- Mỗi invocation có `run_id` riêng; resume cùng native session vẫn là invocation mới. Runner/router ghi cùng invocation theo cách idempotent.
- `success` cũ được suy ra từ kết quả chuẩn trong giai đoạn chuyển tiếp. CLI exit 0, text không rỗng hoặc event bắt đầu session chưa đủ chứng minh hoàn tất.
- OpenCode vẫn ghi `integration_mode=controller_observed` nếu chưa có integration hook thực sự; không gắn nhãn native hook giả.

Handoff do controller đảm bảo tồn tại cả khi model bị ngắt:

```text
task_id, role, iteration_id, plan_revision, plan_hash, feedback_revision
completed_actions, evidence_paths, acceptance_results
pending_actions, unresolved_effects, active_job_ids
checkpoint_manifest_uri, next_safe_action
```

Nội dung phải rõ nghĩa, có cấu trúc, không dùng caveman. Handoff ghi nhận việc đã quan sát; không tự nâng lời agent thành kết quả đã kiểm chứng.

### 4.2. API profile, account và session

- Đăng ký profile như `opencode_api_openrouter`, `opencode_api_anthropic`; tất cả dùng chung adapter `opencode_cli`.
- Account tính theo nơi sử dụng quota/thanh toán: model đi qua OpenRouter thuộc `api:openrouter`, không tự tính thành `api:anthropic`.
- Provider trực tiếp dùng `api:openai`, `api:anthropic`, `api:gemini`, `api:deepseek`; local dùng identity riêng và chỉ bỏ yêu cầu key khi profile khai báo rõ.
- Tên biến môi trường nằm trong config; giá trị credential không vào argv, prompt, notebook, transcript hoặc config sinh ra.
- OpenCode hỗ trợ provider tùy chỉnh và lấy credential từ tên biến môi trường; config sinh ra phải dùng cơ chế này. [Tài liệu provider OpenCode](https://opencode.ai/docs/providers/)
- Session được khóa theo task/role/profile/model/instructions. Không dùng “continue session gần nhất”.
- Resume chỉ dùng đúng session đã lưu và cùng identity. Đổi provider/model thì phiên mới nhận handoff.
- Compaction không thay nguồn sự thật của controller. Sau compaction phải đưa lại plan hash, acceptance và job identity. OpenCode mô tả compaction là có mất thông tin, nên kiểm thử bảo toàn các ràng buộc này là bắt buộc. [Tài liệu compaction](https://opencode.ai/docs/compaction/)
- Cấu hình OpenRouter `free_only` hiện hữu phải được bảo toàn hoặc báo profile chưa hỗ trợ; không bỏ ràng buộc này khi chuyển sang OpenCode.

### 4.3. `pinned` và fallback trong AGY

Cấu hình đề xuất áp dụng cho cả ba role của D1:

```json
{
  "provider": "agy_headless_safe",
  "model": "gemini-3.8-flash-low",
  "pinned": true,
  "fallback_scope": "same_provider_type",
  "fallbacks": [
    {
      "provider": "agy_headless_safe",
      "model": "claude-sonnet-5-5-medium"
    },
    {
      "provider": "agy_headless_safe",
      "model": "claude-opus-5-5-high"
    }
  ]
}
```

Hai model fallback trên là ứng viên từ tài liệu/config hiện có; phải xác nhận availability trước kích hoạt. Chỉ thêm GPT khi có exact model ID do AGY công bố, không tự đoán tên.

Chính sách:

1. `role_pinned` được giải quyết **trước** task/global `--provider`, kể cả khi quota router tắt.
2. Role pinned không đi vào `classes`, `reserve`, `workarounds` hay cách xếp hạng quota chung.
3. Fallback riêng chạy theo thứ tự, có cooldown và event `PINNED_FALLBACK`; không thử vô hạn.
4. Lỗi auth/CLI toàn AGY chuyển thẳng sang template. Lỗi quyền không được dùng làm lý do thử đường khác để thực hiện hành động bị chặn.
5. Giữ quyền communicator hiện có; watchers chỉ đọc/đề xuất, không được sửa code hoặc gọi compute.
6. Telemetry vẫn ghi account/model thực tế, nhưng không làm các role này chờ Codex/Claude CLI.
7. Khi toàn bộ AGY không khả dụng, trả trạng thái từ DB, thời điểm quan sát, blocker và đường dẫn kết quả. Watcher giữ event để xử lý sau, không đánh dấu đã phân tích thành công.

Các điểm phải dùng chung resolver: task communicator, `_conversation_reply`, hai watcher, supervision consultation và các lệnh thay provider hàng loạt như `use-cli`.

### 4.4. Alias `researcher → planner`

| Lớp | Cách chuyển |
|---|---|
| Config | Chuẩn hóa `roles`, role budget, `role_classes`, `requires` và CLI input qua một `canonical_role()`. Nhận tên cũ kèm cảnh báo deprecated. |
| Xung đột config | Nếu cả hai tên có cùng nội dung, hợp nhất. Nếu khác nội dung, báo lỗi chỉ rõ key; không âm thầm chọn cấu hình làm đổi quyền/provider. |
| Code/prompt | Dùng `RoleName.PLANNER`, `roles/planner.md`, `_execute_planner`; cập nhật scripts, tests, docs vận hành và bundle sinh ra. |
| Plan protocol | Output mới dùng `planner_plan`; parser tiếp tục nhận `research_plan` lịch sử qua compatibility layer. |
| DB | Migration chỉ bổ sung schema cần thiết; đọc role/status cũ qua mapping. Không `UPDATE` hàng loạt events, agent runs, quota ledger hoặc transcript lịch sử. |
| Task đang lưu | SQL claim/filter phải nhận cả trạng thái cũ và mới; các chuyển trạng thái mới ghi tên chuẩn. Các trường như `research_notes` được giữ như storage legacy và ánh xạ sang API `planner_notes`. |
| Cache | Đọc được `.workflow/roles/researcher/`; tạo namespace planner theo migration idempotent có lock và ghi nguồn legacy. Không sửa history cũ. |
| Native session | Chỉ giữ ID khi identity tương thích. Prompt mới làm thay instructions hash thì tạo generation mới và handoff; không ép resume bằng cách bỏ qua hash. |
| Dữ liệu có cả hai cache | Không trộn session. Chọn state canonical có nguồn gốc rõ; nếu xung đột thì dùng handoff và phiên mới. |

Không thay từ “research” có ý nghĩa chuyên môn một cách máy móc. Model tier `research` và các biến môi trường legacy được giữ alias nếu cần để config cũ tiếp tục hoạt động.

### 4.5. Plan approval và vòng Ralph

Luồng đích:

```text
Intake
  → planner
  → lưu plan Markdown + JSON + hash
  → WAITING_APPROVAL
  → người dùng duyệt đúng revision/hash
  → operator: một iteration
  → executable checks
  → validator
  → checkpoint iteration
  → iteration kế tiếp
  → communicator
```

- Controller ghi file từ output của planner; planner không cần quyền sửa implementation để đáp ứng D4.
- Plan bắt buộc có spec, acceptance đo được và các việc Ralph; task đơn giản có thể chỉ một việc.
- Duyệt gắn với `task_id`, owner, `plan_revision`, `feedback_revision`, `plan_hash`.
- Hash bao gồm nội dung ảnh hưởng thực thi: phạm vi, công việc, checks, compute, storage và ngân sách được người dùng nêu.
- Không gọi validator trước khi chuyển plan cho người dùng.
- Approval cũ không có hiệu lực với plan hoặc feedback đã đổi. Retry kỹ thuật cùng plan đã duyệt không hỏi duyệt lại.
- Review milestone không được tự thay plan đã duyệt. Chỉ thay chiến lược thực thi sau khi revision mới được duyệt.
- Tái sử dụng xác thực owner, delivery claim và outbox hiện có; thông báo plan phải có khóa riêng theo revision, tránh ghi đè thông báo kết quả task.

### 4.6. ComputeAdapter cho Colab/Kaggle

Tạo job contract chung:

```python
submit(job_spec, request_key) -> JobHandle
status(job_handle) -> JobObservation
logs_tail(job_handle, cursor, limit) -> LogPage
fetch_outputs(job_handle, manifest, policy) -> FetchReceipt
cancel(job_handle, reason) -> CancelReceipt
```

`JobSpec` gồm:

- Task/iteration, backend, script/notebook và hash.
- Config, environment lock, code/data fingerprint.
- Hardware yêu cầu và danh sách hardware/backend được plan cho phép.
- `checkpoint_uri`, resume manifest, output declarations.
- Ngân sách tùy chọn có nguồn `user`; mặc định không có deadline thực thi.

`JobHandle` phải có logical job ID, attempt ID, request key, backend identity, ownership generation. Colab bổ sung notebook/cell/boot identity; Kaggle bổ sung slug và version.

Giữ contract VM thuê hiện tại dưới tên phân biệt như `ProvisioningAdapter`; không áp yêu cầu `ComputeRequest.max_hours` của VM thuê lên job Colab/Kaggle. Registry VM thuê vẫn disabled.

Các nguyên tắc:

- Ghi request và trạng thái `SUBMISSION_UNKNOWN` trước hiệu ứng submit.
- Submit timeout không được tự gửi lại.
- `status`/`logs_tail` không cấp runtime, chuyển GPU hoặc reconnect làm thay đổi VM.
- Đóng transport không được coi là dừng compute.
- Cancel chỉ hoàn tất khi đúng job/version đã dừng; lỗi quan sát trả `UNKNOWN`.
- Colab tiếp tục dùng `ColabGate`, bridge và [skill Colab hiện có](../../.agents/skills/colab-coccoc-playwright/SKILL.md).
- Kaggle dùng CLI cho submit/status/output; [skill cancel hiện có](../../.agents/skills/kaggle-playwright-cancel/SKILL.md) phải được bổ sung kiểm tra version. Code hiện chọn nút của version đầu tiên chưa đủ cho cancel theo job identity.

### 4.7. Poller không dùng LLM

Tận dụng `ProgressStore`, `poll_job`, `Supervisor.poll`, scheduler và `ExperimentProgress`.

- Một đường polling chính có claim theo job; bỏ việc hai đường cùng quan sát một job mà không chia sẻ claim.
- Mỗi tick chỉ đọc trạng thái, progress hoặc manifest nhỏ, rồi cập nhật DB.
- Heartbeat khác useful progress; log nhiều không đồng nghĩa công việc tiến triển.
- Phát wake khi có thay đổi có ý nghĩa: hoàn tất, lỗi đã xác nhận, mất quan sát, bất thường metric, checkpoint/resume readiness.
- Wake key gồm job ID, loại sự kiện và observation sequence để chống trùng qua restart.
- Mất mạng là telemetry unavailable, không phải job đã thất bại.
- Poller không import/call runner; diagnosis nhận sự kiện riêng và bị giới hạn theo episode.
- Không dùng quota probe có inference trong vòng polling compute. Probe Claude chủ động và smoke model là công việc riêng, có lịch/receipt riêng.
- Máy local tắt thì polling và thông báo tạm dừng; job phải tự checkpoint ngoài VM. Khi mở lại, reconcile trước khi dispatch.

### 4.8. Google Drive, manifest và HF Hub

Kho chính:

```text
workflow-runs/<task-id>/<logical-run-id>/
  checkpoints/<checkpoint-id>/
  artifacts/
  manifests/
```

- Colab hỗ trợ `drive.mount`; kiểm tra mount thật và publish/hash/restore probe, không chỉ kiểm tra có thư mục.
- Kaggle dùng `rclone` trong worker để đưa checkpoint thẳng lên Drive. Local dùng rclone cho metadata/kết quả nhẹ.
- Credential runtime do người dùng thiết lập qua môi trường/Secrets; controller và agent không đọc/in credential.
- Cần kiểm tra scope tương thích: `drive.file` của rclone chỉ thấy file do chính ứng dụng đó tạo, có thể không đọc checkpoint tạo qua `drive.mount`. `root_folder_id` giới hạn cây thao tác của rclone, không được coi là OAuth chỉ có quyền trên một thư mục. [Tài liệu rclone Drive](https://rclone.org/drive/#scopes)
- Gom state thành ít file lớn. Upload vào checkpoint ID bất biến; chỉ công bố manifest hoàn tất sau khi upload và kiểm tra thành công. Không giả định rename qua Drive mount có độ bền giống filesystem local.
- Giữ đề xuất `N=3` checkpoint gần nhất, cộng checkpoint đang được resume/tham chiếu và bản cuối; chỉ dọn bản thuộc đúng task sau khi đã có bản thay thế được xác minh.
- Xử lý upload throttling như trạng thái chờ storage. Mốc khoảng 750 GB/ngày trong bản nháp là lưu ý vận hành, không hardcode thành hạn mức chắc chắn cho tài khoản. [Giới hạn được rclone ghi nhận](https://rclone.org/drive/#drive-stop-on-upload-limit)

Manifest phiên bản 1 chứa tối thiểu:

```text
task_id, logical_run_id, job_id, parent_job_id, checkpoint_id
backend, runtime_identity, hardware_requested, hardware_observed
code_hash, data_hash, config_hash, environment_hash
global_step, epoch, data_cursor, checkpoint_schema
files[{uri, remote_id, bytes, sha256, kind, local_download}]
publication_state, verification_source, verified_at
```

Checkpoint ứng dụng phải bao gồm model, optimizer, scheduler/scaler nếu có, RNG và data cursor. Manifest không tự chứng minh resume được; phải có kiểm thử restore và continuation.

Local mặc định chỉ nhận metrics, log tóm tắt, ảnh mẫu, báo cáo và manifest theo allowlist/giới hạn dung lượng. Không gọi tải toàn bộ Kaggle output rồi mới lọc.

HF Hub là exporter tùy chọn, mặc định tắt; publish trực tiếp từ worker/kho remote theo danh sách file, lưu repo/revision vào manifest. Hub có API upload file/folder; không cần đưa checkpoint qua local chỉ để xuất bản. [Tài liệu upload HF Hub](https://huggingface.co/docs/huggingface_hub/guides/upload)

### 4.9. Resume giữa Colab và Kaggle

Backend chuyển đổi phải nằm trong plan đã duyệt. Task chỉ yêu cầu Colab không tự biến thành Kaggle.

Trước tạo successor job:

1. Xác nhận job trước đã kết thúc, đã dừng, hoặc hoàn thành segment có checkpoint.
2. Xác minh manifest đã publish và state tương thích.
3. Đối chiếu code/data/config/environment; giữ logical run và progress offset.
4. Tạo attempt/job ID mới, liên kết parent và checkpoint.
5. Restore trực tiếp trên backend mới, kiểm tra hash trước khi load.
6. Xác minh có bước tiến mới sau resume rồi mới ghi nhận recovery thành công.

`UNKNOWN` hoặc `SUBMISSION_UNKNOWN` không được coi là điều kiện cho phép chuyển backend và chạy bản thứ hai.

## 5. Cách tổ chức iteration và bằng chứng

Thứ tự chính giữ nguyên mục 10:

```text
P2.0 → P2.1 → P2.2 → P2.3 → P2.4 → P2.5 → P2.7
```

P2.6 xen kẽ:

- **P2.6-01** sau P2.2: cổng duyệt plan.
- **P2.6-02** sau P2.4, trước P2.5: bỏ ngân sách thời gian mặc định.
- **P2.6-03** sau P2.5: tiết kiệm context và chính sách subagent.

Mỗi iteration làm một mục tiêu, kiểm thử, ghi bằng chứng rồi mới đi tiếp. Trạng thái dùng: `ready`, `in_progress`, `verified`, `reused_verified`, `deferred`.

Mỗi receipt ghi commit/diff liên quan, lệnh, exit code, assertions đã kiểm tra và giới hạn bằng chứng. Bằng chứng live và fixture được ghi riêng.

Quy ước lệnh trong các iteration:

```powershell
# U("test_x.py") bên dưới nghĩa là:
.conda/python.exe -m unittest discover -s tests -p "test_x.py" -v

# G: cổng nghiệm thu cuối giai đoạn
$env:PATH = 'C:\Program Files\Git\bin;' + $env:PATH
.conda/python.exe -m unittest discover -s tests -v
.conda/python.exe main.py --no-env doctor
git diff --check
```

Các file `test_p2_*`, script `verify_p2_*`, config fixture và script smoke nêu dưới đây là **file đề xuất tạo**, chưa phải khả năng đang có. Test offline phải dùng DB/workspace tạm, credential giả và transport giả hoặc localhost; không kế thừa probe model thật từ config cá nhân.

## 6. Danh sách iteration Ralph

### P2.0 — Baseline

**P2.0-01 — Xác minh baseline có thể tái sử dụng — S**

- **File/phạm vi:** `docs/ralph-p2-20261110/EXECUTION_LOG.md` mới; source chỉ sửa khi phát hiện lỗi baseline cụ thể.
- **Verify:** `git status --short`, `git log -4 --oneline`, bộ lệnh **G**; đối chiếu bằng chứng test với đúng commit.
- **Xong khi:** `cab220d` được ghi `reused_verified`, hoặc phần sửa baseline có test pass và commit riêng. Không đưa `.claude/worktrees/`, config cá nhân hoặc runtime state vào commit.
- **Rủi ro:** nhầm receipt P1 với kiểm thử của HEAD hiện tại. Trước thay listener/daemon phải kiểm tra không có task/job đang chạy hoặc chưa reconcile.

### P2.1 — API provider qua OpenCode

**P2.1-01 — Chốt phiên bản và compatibility contract OpenCode — S**

- **File:** `config/provider_versions.json`, `docs/PROVIDERS.md`, `tests/fixtures/opencode/`.
- **Verify:** sau cài bản đã chọn, chạy `opencode --version`, `opencode run --help`, `opencode session --help`; `U("test_p2_opencode_version.py")`.
- **Xong khi:** lưu exact version, schema quyền, command flags, terminal/error/usage events cần hỗ trợ; fixture có provenance.
- **Rủi ro:** trộn schema V1/V2 hoặc cho rằng flag cũ còn hiệu lực. Chưa đạt compatibility thì không chạy model thật.

**P2.1-02 — Sinh profile API và cấu hình runtime có kiểm soát — M**

- **File:** `core/configuration.py`, `core/integrations.py`, `core/diagnostics.py`, `config/workflow_config.example.json`, `core/opencode_config.py` mới.
- **Verify:** `U("test_p2_api_profiles.py")`; fixture OpenRouter, OpenAI, Anthropic, Gemini, DeepSeek, local; thử thiếu model/key, endpoint sai và config có override ngoài dự kiến.
- **Xong khi:** profile xác định rõ vendor/model/account; config chỉ chứa tham chiếu biến môi trường; model thiếu không kích hoạt paid default; cấu hình ràng buộc `free_only` được giữ.
- **Rủi ro:** config/global service của OpenCode làm lệch cấu hình. Kiểm tra precedence và cô lập runtime mà vẫn tôn trọng policy của host.

**P2.1-03 — Hoàn thiện adapter chạy OpenCode và kết quả chuẩn — M**

- **File:** `core/providers.py`, `core/provider_registry.py`, `core/models.py`, `core/opencode_provider.py` mới, tests provider.
- **Verify:** `U("test_p2_opencode_execution.py")`, `U("test_providers.py")`, `U("test_readiness.py")`.
- **Xong khi:** đọc JSON stream tăng dần; phân biệt progress/final/error; lưu output/handoff/usage theo contract mục 4.1; test stream thiếu final không PASS.
- **Rủi ro:** quyền chỉ đúng trong mock. Có test CLI thật với fake server cho planner không sửa source, operator chỉ làm việc trong workspace, quyền shell không vượt phạm vi đã cấp.

**P2.1-04 — Nối resume session và compaction với handoff — M**

- **File:** `core/opencode_provider.py`, `core/role_memory.py`, `core/integrations.py`, `tests/test_p2_opencode_session.py`.
- **Verify:** `U("test_p2_opencode_session.py")`, `U("test_resumable.py")`, `U("test_atomic_memory.py")`.
- **Xong khi:** restart giữ đúng session; đổi model/instructions tạo generation mới; thiếu session dùng handoff; compaction vẫn giữ plan/check/job identity.
- **Rủi ro:** dùng nhầm session của task khác hoặc replay tool sau stream ngắt. Test hai task và hai role đồng thời, không dùng global “last session”.

**P2.1-05 — Đăng ký quota/usage cho API ở mọi lượt — M**

- **File:** `core/quota.py`, `core/opencode_provider.py`, `core/database.py`, `core/configuration.py`, `tests/fakes/api_server.py`.
- **Verify:** `U("test_p2_api_quota.py")`, `U("test_quota*.py")`; fake server phát 429 ở lượt đầu và sau tool, 401/403, quota hết, 5xx, stream đứt và usage thiếu.
- **Xong khi:** account `api:<vendor>` được ghi đúng; rate limit khác hết quota; có `retry_after/reset_at` phù hợp; một invocation chỉ ghi một lần, kể cả routing tắt.
- **Rủi ro:** cộng trùng cache/cost, coi missing usage là 0, cooldown toàn bộ OpenCode thay vì đúng account.

**P2.1-06 — Sửa đường HTTP tối thiểu để không che lỗi và replay — S**

- **File:** `core/providers.py`, `tests/test_p2_http_errors.py`, `tests/test_turn_budget.py`.
- **Verify:** `U("test_p2_http_errors.py")`, `U("test_turn_budget.py")`; đi qua `_post` thật tới fake HTTP server.
- **Xong khi:** 429 không bị mất metadata; lỗi sau tool có handoff và trạng thái cần đối chiếu; giới hạn vòng tạo checkpoint tiếp tục được thay vì đánh dấu task hoàn tất.
- **Rủi ro:** mở rộng thành phương án B ngoài phạm vi; log response body chứa request/credential. Chỉ lưu các trường lỗi đã allowlist.

**P2.1-07 — Nghiệm thu API end-to-end — S**

- **File:** `scripts/verify_p2_api.py`, fixture synthetic và receipt trong `storage/p2/acceptance/api/`.
- **Verify:**
  - `python scripts/verify_p2_api.py --offline --with-cli`
  - `python scripts/verify_p2_api.py --live --profile <profile-da-chon> --case tiny-tool-task`
  - Bộ **G**.
- **Xong khi:** CLI thật gọi fake server, đọc/sửa fixture, chạy check và resume được; một smoke live nhỏ có output, model/account, usage/cost và receipt.
- **Rủi ro:** test live một vendor bị mô tả thành mọi vendor đã live. Ma trận phải tách cấu hình hỗ trợ, contract đã test và live đã test.

### P2.2 — Đổi tên và thiết lập planner

**P2.2-01 — Chuẩn hóa tên role ở config và điểm vào — M**

- **File:** `core/role_names.py` mới, `core/configuration.py`, `core/models.py`, `core/plans.py`, `main.py`, config example.
- **Verify:** `U("test_p2_planner_alias.py")`, `U("test_quota_requires.py")`; fixture chỉ có tên cũ, chỉ tên mới, cả hai giống nhau và xung đột.
- **Xong khi:** mọi input được chuẩn hóa một lần; CLI cũ vẫn dùng được kèm deprecation; config output mới dùng planner.
- **Rủi ro:** bỏ sót role budget/`requires` hoặc vô tình đổi model tier, biến môi trường đang dùng.

**P2.2-02 — Tương thích DB và state machine cũ — M**

- **File:** `core/database.py`, `core/orchestrator.py`, `core/recovery.py`, `core/conversation.py`, `core/supervision.py`, `core/observability.py`, `core/reports.py`.
- **Verify:** `U("test_p2_planner_database.py")`, `U("test_recovery*.py")`, `U("test_scheduler.py")`, `U("test_conversation*.py")`.
- **Xong khi:** DB fixture cũ được claim/resume/hiển thị đúng; record mới dùng planner; events và role-run lịch sử giữ nguyên giá trị gốc.
- **Rủi ro:** đổi enum nhưng SQL vẫn chỉ chọn `RESEARCHING`, khiến task bị bỏ quên.

**P2.2-03 — Chuyển role cache và portable memory — M**

- **File:** `core/role_memory.py`, `core/providers.py`, tests cache/migration.
- **Verify:** `U("test_p2_planner_memory.py")`, `U("test_resumable.py")`, `U("test_atomic_memory.py")`.
- **Xong khi:** migrate lặp lại không nhân history/session; crash giữa chừng có thể tiếp tục; cache cũ không bị sửa; identity không tương thích thì rotate có lý do.
- **Rủi ro:** hai namespace cùng resume một session. Test khóa xuyên tiến trình và trường hợp cả hai cache tồn tại.

**P2.2-04 — Áp mandate và chuỗi provider đúng D2–D3 — M**

- **File:** `roles/planner.md`, các role hiện hành, `core/plans.py`, `core/quota.py`, `core/providers.py`, `core/tools.py`, scripts cloud/artifact, config example và tests tương ứng.
- **Verify:** `U("test_p2_planner_contract.py")`, `U("test_quota*.py")`, `U("test_research_events.py")`.
- **Xong khi:** planner trả spec/acceptance/iteration; không sửa implementation/chạy compute; route lần lượt đúng Codex Astra → Claude Opus → AGY Argon high. Sticky/reset ranking không đảo D3.
- **Rủi ro:** prompt nói chỉ lập kế hoạch nhưng AGY mode/tool vẫn cho ghi. Kiểm tra quyền thực tế của từng adapter và generated bundle.

### P2.6 xen kẽ — Cổng duyệt kế hoạch

**P2.6-01 — Chặn thực thi trước approval đúng plan — M**

- **File:** `core/orchestrator.py`, `core/plans.py`, `core/control.py`, `core/approvals.py`, `core/conversation.py`, `core/database.py`, gateway delivery và CLI control.
- **Verify:** `U("test_p2_plan_approval.py")`, `U("test_approvals.py")`, `U("test_delivery*.py")`.
- **Xong khi:** plan file/hash tồn tại trước notice; chưa duyệt thì số lần gọi operator bằng 0; sai owner/hash/revision không mở khóa; duplicate approval không chạy hai lần.
- **Rủi ro:** dùng lại approval đã consumed hoặc thông báo plan ghi đè final outbox. Test restart và feedback đến đúng lúc approve.

### P2.3 — AGY pin cho communicator/watchers

**P2.3-01 — Áp resolver pinned và fallback nội bộ AGY — M**

- **File:** `core/role_routing.py` mới, `core/quota.py`, `core/providers.py`, `core/orchestrator.py`, `core/configuration.py`, hai watcher, `core/supervision.py`, `main.py`.
- **Verify:** `U("test_p2_pinned_roles.py")`, `U("test_watchers.py")`, `U("test_quota_orchestrator.py")`.
- **Xong khi:** cả ba role bắt đầu bằng Flash low; Codex/Claude CLI hết quota không chặn chúng; task/global override không đưa chúng ra ngoài AGY; fallback đổi model nhưng giữ quyền.
- **Rủi ro:** chỉ sửa task pipeline, bỏ sót chat hoặc watcher khi router tắt.

**P2.3-02 — Trả lời bằng template khi toàn AGY lỗi — S**

- **File:** `core/conversation.py`, `core/conversation_intents.py`, `core/orchestrator.py`, watchers và tests delivery.
- **Verify:** `U("test_p2_no_llm_status.py")`, `U("test_conversation_delivery.py")`, `U("test_discord_channels.py")`.
- **Xong khi:** fake toàn AGY unavailable vẫn nhận được status/blocker/path từ DB; không thêm inference; không tạo task hay approval từ câu trả lời fallback; không gửi trùng.
- **Rủi ro:** template phát biểu thành công dựa trên text agent hoặc watcher đánh dấu item đã xử lý dù model thất bại.

### P2.4 — Contract và failover toàn hệ thống

**P2.4-01 — Áp kết quả chuẩn cho mọi adapter — M**

- **File:** `core/models.py`, `core/providers.py`, `core/provider_registry.py`, `core/quota.py`, `core/remote_claude.py`, tests contract.
- **Verify:** `U("test_p2_provider_contract.py")`, `U("test_providers.py")`, `U("test_provider_registry.py")`, `U("test_remote_claude.py")`.
- **Xong khi:** CLI/API/mock/extension trả cùng contract; public `run_role` và receipt nhất quán; lỗi telemetry không đổi kết quả hay chạy lại tool.
- **Rủi ro:** phá caller dùng `success` hoặc vô tình làm remote Claude pilot thành operator “đã hỗ trợ”. Adapter chưa hỗ trợ vẫn phải báo rõ.

**P2.4-02 — Failover tại ranh giới Ralph với handoff — M**

- **File:** `core/orchestrator.py`, `core/quota.py`, `core/control.py`, `core/recovery.py`, `core/role_memory.py`.
- **Verify:** `U("test_p2_provider_failover.py")`, `U("test_quota_orchestrator.py")`, `U("test_recovery_evidence.py")`.
- **Xong khi:** quota hit lưu handoff, kết thúc invocation rồi chọn route mới ở bước kế tiếp; không mất retry; không đợi reset account cũ nếu đã có route hợp lệ.
- **Rủi ro:** lặp lại hiệu ứng trước quota hit. Test tool đã tạo file, job đã submit, phản hồi submit bị mất và controller restart; case chưa rõ hiệu ứng phải giữ `UNKNOWN`.

**P2.4-03 — Chọn validator độc lập khi có thể — S**

- **File:** `core/quota.py`, `core/orchestrator.py`, config routing, tests validator.
- **Verify:** `U("test_p2_validator_routing.py")`, `U("test_verdict_evidence.py")`.
- **Xong khi:** ưu tiên provider/account khác operator và đủ năng lực; chỉ còn cùng provider thì ghi rõ lý do/độ độc lập; executable check fail luôn chặn PASS.
- **Rủi ro:** coi hai alias OpenCode cùng vendor/account là hai provider độc lập, hoặc downgrade validator chỉ vì quota.

**P2.4-04 — Pin CLI và lập smoke probe có lịch riêng — S**

- **File:** `config/provider_versions.json`, `core/diagnostics.py`, `scripts/verify_p2_providers.py`, `docs/PROVIDERS.md`.
- **Verify:** `U("test_p2_provider_smoke_schedule.py")`; `python scripts/verify_p2_providers.py --offline`; bộ **G**.
- **Xong khi:** lệch phiên bản hiện cảnh báo rõ; smoke live dùng fixture synthetic, tối đa một lần/profile/ngày khi campaign hoạt động, không chạy bù hàng loạt sau khi máy ngủ.
- **Rủi ro:** biến doctor/poller thành inference định kỳ hoặc cố làm tài khoản thật hết quota để nghiệm thu.

### P2.6 xen kẽ — Ngân sách đúng D6

**P2.6-02 — Loại bỏ deadline task/GPU mặc định — M**

- **File:** `core/configuration.py`, `core/models.py`, `core/plans.py`, `core/orchestrator.py`, `core/control.py`, `core/monitoring.py`, `core/supervision.py`, `core/experiment_progress.py`, `core/cloud.py`, `core/processes.py`, config example.
- **Verify:** `U("test_p2_optional_budgets.py")`, `U("test_monitoring.py")`, `U("test_waits.py")`, `U("test_supervision.py")`.
- **Xong khi:** fake clock vượt các mốc 6/10/12/24 giờ không dừng task/job không có budget; budget người dùng có nguồn rõ và checkpoint trước dừng ở safe boundary.
- **Rủi ro:** bỏ sót default trong bootstrap/env hoặc SQL `NOT NULL`. Dùng trường deadline tùy chọn/migration tương thích, không đặt số rất lớn để giả “vô hạn”.

Timeout HTTP/RPC, lease TTL, heartbeat và phát hiện treo vẫn tồn tại. Chúng điều khiển một thao tác hoặc quyền sở hữu; không được âm thầm trở thành ngân sách tổng của thí nghiệm. Soft turn budget vẫn là hướng dẫn.

### P2.5 — Compute và storage

**P2.5-01 — Định nghĩa job adapter và identity thống nhất — M**

- **File:** `core/compute.py`, `core/compute_jobs.py` mới, `core/monitoring.py`, `core/cloud.py`, `docs/COMPUTE_EXTENSIONS.md`.
- **Verify:** `U("test_p2_compute_contract.py")`, `U("test_compute.py")`.
- **Xong khi:** fake Colab/Kaggle vượt contract năm phương thức; request key đổi nội dung bị từ chối; registry VM thuê vẫn không thể allocation.
- **Rủi ro:** nhầm interface job với provisioning và ép duration/cost cap mặc định vào Colab/Kaggle.

**P2.5-02 — Xây manifest checkpoint ngoài máy — M**

- **File:** `core/checkpoints.py`, `core/artifact_store.py` mới, schema manifest, tests storage.
- **Verify:** `U("test_p2_remote_manifest.py")`, `U("test_checkpoints.py")`; fake store lỗi giữa upload, manifest thiếu, hash sai, file trùng và path escape.
- **Xong khi:** checkpoint chưa publish xong không được chọn resume; tương thích code/data/config được kiểm tra; manifest cũ vẫn đọc được.
- **Rủi ro:** coi manifest tồn tại là upload thành công, hoặc dùng rename local để suy ra độ bền remote.

**P2.5-03 — Kết nối Drive mount và rclone — M**

- **File:** `core/artifact_store.py`, `scripts/storage.py` mới, helper Drive của skill Colab, config example, `docs/CHECKPOINTS.md`.
- **Verify:** `U("test_p2_drive_store.py")`; sau cấu hình: `python scripts/verify_p2_storage.py --store drive --case publish-restore`.
- **Xong khi:** checkpoint synthetic từ mount được đọc/restore qua rclone và chiều ngược lại; receipt chỉ có metadata/hash; thiếu consent/quyền chuyển sang chờ rõ ràng.
- **Rủi ro:** scope `drive.file` không thấy file mount; token bị đưa vào notebook/output; upload xong nhưng chưa đủ bằng chứng publish.

**P2.5-04 — Bọc Colab thành job adapter có fencing — M**

- **File:** `core/compute_colab.py` mới, `core/cloud.py`, `core/colab_bridge.py`, `core/colab_runtime.py`, `core/resource_gate.py`, helper Colab liên quan.
- **Verify:** `U("test_p2_colab_adapter.py")`, `U("test_resource_gate.py")`, `U("test_colab_runtime.py")`, `U("test_colab_drive_skill.py")`.
- **Xong khi:** submit một lần; boot/generation sai bị chặn; polling không đổi runtime; hardware không có thì chờ; Drive readiness kiểm tra trước long run; shutdown có xác nhận.
- **Rủi ro:** helper cũ reconnect/switch runtime hoặc run notebook trước khi job được đăng ký. Mọi hiệu ứng phải đi qua ownership/fence chung.

**P2.5-05 — Bọc Kaggle và cancel đúng version — M**

- **File:** `core/compute_kaggle.py` mới, `core/cloud.py`, `.agents/skills/kaggle-playwright-cancel/scripts/kaggle_cancel.py`, SKILL tương ứng.
- **Verify:** `U("test_p2_kaggle_adapter.py")`, `U("test_cloud.py")`; fake CLI và DOM fixture có nhiều version, cancel timeout, `CANCEL_ACKNOWLEDGED`.
- **Xong khi:** submit/status/log/cancel thống nhất identity; cancel là lệnh riêng, `status` không có hiệu ứng dừng; chỉ nhận stopped sau xác minh.
- **Rủi ro:** slug trỏ version mới hơn, chọn nút đầu tiên hoặc text parser không nhận trạng thái cancel.

**P2.5-06 — Dùng một poller thuần code và wake theo sự kiện — M**

- **File:** `core/monitoring.py`, `core/supervision.py`, `core/scheduler.py`, `core/orchestrator.py`, `core/experiment_progress.py`.
- **Verify:** `U("test_p2_compute_poller.py")`, `U("test_monitoring.py")`, `U("test_supervision.py")`, `U("test_scheduler.py")`.
- **Xong khi:** 1.000 tick bình thường tạo 0 model call và 0 submit; hai poller không phát wake trùng; restart không mất event; mất mạng không biến thành failed.
- **Rủi ro:** `_poll_remote` và supervisor cùng polling, hoặc diagnosis định kỳ bị tính nhầm là poll không LLM.

**P2.5-07 — Thu kết quả nhẹ và xác nhận artifact remote — M**

- **File:** `core/cloud.py`, hai compute adapter, `core/artifact_store.py`, `core/orchestrator.py`, `core/reports.py`, `roles/operator.md`, `roles/validator.md`.
- **Verify:** `U("test_p2_light_outputs.py")`; fixture có checkpoint lớn được khai báo remote và một số file nhỏ local.
- **Xong khi:** checkpoint không tải về local; metrics/report/manifest được tải và kiểm tra; trạng thái artifact ghi rõ remote verified/local fetched/unvalidated.
- **Rủi ro:** đường `kaggle_export_data` cũ vẫn tải tất cả, hoặc yêu cầu validator tải model về chỉ để thỏa check file local.

**P2.5-08 — Resume qua hai backend bằng checkpoint tương thích — M**

- **File:** `core/compute_jobs.py`, `core/checkpoints.py`, `core/recovery.py`, `core/plans.py`, `core/orchestrator.py`, hai adapter.
- **Verify:** `U("test_p2_cross_backend_resume.py")`, `U("test_recovery*.py")`; CPU fixture so continuation với chạy liền, kiểm RNG/optimizer/data cursor.
- **Xong khi:** Colab→Kaggle và Kaggle→Colab giữ logical run/progress; tạo successor đúng parent; đổi backend ngoài plan hoặc job cũ `UNKNOWN` đều bị chặn.
- **Rủi ro:** parser/replan hiện cấm đổi backend, checkpoint không portable, hoặc chạy hai writer trên cùng logical run.

**P2.5-09 — Export HF Hub tùy chọn — S**

- **File:** `core/artifact_store.py`, `scripts/storage.py`, config example, tests exporter.
- **Verify:** `U("test_p2_hf_export.py")`; fake Hub kiểm allowlist, revision receipt và lỗi publish.
- **Xong khi:** mặc định tắt; lỗi HF không làm mất checkpoint Drive; không cần download checkpoint về local.
- **Rủi ro:** publish công khai ngoài chủ ý hoặc upload cả thư mục chứa file ngoài output declaration. Live HF không chặn nghiệm thu lõi P2.

### P2.6 xen kẽ — Context và cách dùng agent

**P2.6-03 — Chuẩn hóa handoff ngắn và đo tiết kiệm context — S**

- **File:** `roles/*.md`, `core/integrations.py`, docs vận hành, `scripts/benchmark_context.py` mới.
- **Verify:** `U("test_p2_context_contract.py")`; benchmark cùng fixture trước/sau; nếu có RTK thì ghi `rtk gain`.
- **Xong khi:** dùng lại `status_digest`, `tail_log/search_log`, artifact helper; handoff vẫn giữ yêu cầu/check/job IDs; subagent chỉ đọc/khám phá/review/tóm tắt, workspace riêng cho việc độc lập.
- **Rủi ro:** giảm output làm mất lỗi hoặc bằng chứng. RTK chỉ bật sau đo và kiểm equivalence; chưa có dữ liệu thì giữ tắt. Ghi lịch rà plugin/MCP hằng tháng khi campaign được sử dụng.

### P2.7 — Task mẫu và bảo trì

**P2.7-01 — Tạo Mini-DDPM có checkpoint/resume tái lập — M**

- **File:** `examples/p2_mini_ddpm/` gồm train, config, environment lock, kiểm tra kết quả; `scripts/verify_p2_e2e.py`; tests CPU.
- **Verify:** `U("test_p2_smoke_cpu.py")`; `python scripts/verify_p2_e2e.py --offline`.
- **Xong khi:** fake end-to-end đi qua plan approval, provider failover, job polling, manifest và validator. CPU fixture kiểm tra state sau resume; live sample dùng CIFAR-10 với số epoch nhỏ, seed/config cố định.
- **Rủi ro:** chỉ so hash mà không kiểm continuation; nhầm smoke workflow với chất lượng mô hình hay paper reproduction. Không đặt ngưỡng FID tùy tiện.

**P2.7-02 — Nghiệm thu live Colab/Kaggle và thông báo — M**

- **File:** receipt/báo cáo trong `storage/p2/acceptance/e2e/`, tài liệu nghiệm thu.
- **Verify:** script mới:
  ```powershell
  python scripts/verify_p2_e2e.py --live --scenario colab-to-kaggle
  python scripts/verify_p2_e2e.py --live --scenario kaggle-to-colab
  python scripts/verify_p2_e2e.py --live --scenario discord-status
  ```
- **Xong khi:** cả hai chiều restore checkpoint từ Drive và có progress mới; local chỉ có file nhẹ; quota giả lập giữa vòng không submit lại GPU; validator có bằng chứng; Discord có receipt gửi/nhận thực tế.
- **Rủi ro:** cloud availability/consent, UI drift, ảnh hưởng runtime đang chạy. Chỉ dùng job mẫu đã được duyệt, không cố làm tài khoản thật cạn quota và không chiếm runtime của thí nghiệm khác.

**P2.7-03 — Cập nhật tài liệu và đóng gói nghiệm thu P2 — S**

- **File:** `CLAUDE.md`, `README.md`, `docs/ARCHITECTURE.md`, `docs/PROVIDERS.md`, `docs/QUOTA_ROUTING.md`, `docs/COMPUTE_EXTENSIONS.md`, checkpoint/operations docs, archive index.
- **Verify:** bộ **G**, `U("test_p2_documentation.py")`; quét tên cũ trong tập file vận hành và đối chiếu allowlist alias/lịch sử; kiểm liên kết.
- **Xong khi:** tài liệu mô tả đúng đường đã nghiệm thu; lịch sử có ngày không bị viết lại; di chuyển archive giữ nguyên nội dung và có map đường dẫn; còn blocker live phải ghi `deferred`.
- **Rủi ro:** thay tên trong biên bản, làm đứt link bằng chứng hoặc tuyên bố toàn bộ P2 xong khi còn thiếu live acceptance bắt buộc.

## 7. Ước lượng độ lớn

S/M/L là ước lượng khối lượng triển khai và xác minh, **không phải ngân sách thời gian/GPU của task**.

| Giai đoạn | Độ lớn | Lý do chính |
|---|---:|---|
| P2.0 | S | Baseline đã có; cần xác minh receipt và trạng thái checkout. |
| P2.1 | L | Runtime/version, quyền, session, lỗi đa vendor, usage và fake-server integration. |
| P2.2 | L | Đổi tên xuyên config, SQL, recovery, cache và compatibility lịch sử. |
| P2.3 | M | Resolver dùng chung và các điểm gọi ngoài pipeline. |
| P2.4 | L | Contract toàn adapter, chống replay và failover có trạng thái bền. |
| P2.5 | L — lớn nhất | Hai backend, remote storage, manifest, cancel đúng identity và resume hai chiều. |
| P2.6 | M, xen kẽ | Approval, optional budget và context policy chạm nhiều lớp nhưng tái dùng nền hiện có. |
| P2.7 | M | Harness nhỏ; thời gian chờ live phụ thuộc account, GPU và consent. |

Không gom P2.1 hoặc P2.5 thành một lần sửa lớn. Nếu iteration phát sinh nhiều hiệu ứng độc lập, tách thêm ID con trước khi thực thi.

## 8. Tiêu chí nghiệm thu toàn P2

P2 chỉ được ghi hoàn tất khi:

1. API qua OpenCode vượt fake-server contract và có ít nhất một smoke live nhỏ.
2. Planner dùng tên chuẩn, chuỗi D3 đúng thứ tự; dữ liệu cũ vẫn đọc được.
3. Operator không chạy trước approval đúng plan/hash.
4. Ba role pinned vẫn hoạt động khi Codex/Claude CLI hết quota; khi AGY lỗi có template xác định.
5. Failover không lặp tool/job đã có hiệu ứng và không tiêu hao retry do quota wait.
6. Task/job không có budget người dùng không bị cắt bởi các default thời gian cũ.
7. Colab/Kaggle dùng chung job contract, poller bình thường không gọi LLM.
8. Checkpoint nặng nằm ngoài máy; cả hai chiều resume có bằng chứng continuation.
9. Mini-DDPM, validation và đường thông báo live có receipt riêng.
10. Tài liệu phân biệt rõ source-supported, fixture-tested, live-verified và deferred.

## 9. Những điểm thực sự cần người dùng quyết định thêm

Không mở lại D1–D8. Hai thông tin dưới đây chỉ chặn bước live liên quan, không chặn implementation và test offline:

| Điểm còn thiếu | Quyết định/thông tin cần có | Thời điểm cần |
|---|---|---|
| Profile API cho smoke đầu tiên | Vendor và exact model được phép dùng nếu cấu hình vận hành chưa xác định sẵn. Ưu tiên profile người dùng đã bật; không yêu cầu gửi credential trong chat. | Trước P2.1-07 live. |
| Đích Drive và quyền truy cập tương thích | Folder ID/thư mục workflow cụ thể trên Drive; lựa chọn quyền OAuth cho phép rclone trao đổi file với `drive.mount`. Nếu chỉ cho phép `drive.file`, cần chấp nhận điều chỉnh đường publish Colab sang cùng ứng dụng rclone khi kiểm thử liên thông thất bại. | Trước P2.5-03 live. |

Không cần quyết định thêm về ngân sách mặc định, HF Hub hay RTK: ngân sách mặc định không có; HF Hub và RTK giữ tắt cho đến khi được chọn dùng.

