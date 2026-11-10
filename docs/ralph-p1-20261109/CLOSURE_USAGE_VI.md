# Usage của các lượt review cuối

Phạm vi chỉ gồm ba lượt AGY review checkpoint bốn file và ba Claude headless continuation được lưu dưới `storage/ralph-p1-20261109/validation/`. Đây không phải tổng task, tổng campaign hay tổng tài khoản; không gồm coordinator và Codex worker. Dữ liệu máy đọc: [CLOSURE_NATIVE_USAGE.json](../../storage/ralph-p1-20261109/implementation/CLOSURE_NATIVE_USAGE.json).

## Claude — ba invocation, ba session mới

Các số dưới đây lấy một lần từ result của mỗi CLI invocation. Không cộng lại `modelUsage`, `iterations` hay thinking vào các tổng đã có. Một invocation có thể gồm nhiều lượt model/tool; không đồng nghĩa một response.

| Callback | Việc thực tế | New input | Cache creation | Cache read | Output |
|---|---|---:|---:|---:|---:|
| R7 | Bàn giao thiếu biên nhận, chưa chốt | 10 | 15,805 | 49,252 | 3,350 |
| R8 | Bàn giao lỗi hook, chưa chốt | 18 | 26,071 | 140,603 | 6,916 |
| R9 | Chạy finalizer và chốt hồ sơ | 16 | 29,001 | 118,543 | 9,490 |

New input nhỏ không có nghĩa toàn bộ lượt gọi nhỏ: vẫn có cache creation và context được đọc qua các vòng tool. Reasoning là phần con của output trong schema này. Không đổi các số này thành phần trăm quota hay hóa đơn subscription.

## AGY — giữ nguyên counter gốc, chưa quy thành usage riêng mỗi lượt

Ba lượt dùng chung conversation. Scope/reset semantics của counter từ native AGY chưa được xác lập độc lập; cache-read ở một snapshot có thể lớn hơn input. Vì vậy **không cộng các hàng hoặc lấy delta để gọi là usage mỗi invocation**. Đưa số gốc vào đây để có provenance, không áp schema Codex/Claude lên AGY.

| Review | Verdict | Raw input | Raw output | Raw thinking | Raw cache read | Raw total |
|---|---|---:|---:|---:|---:|---:|
| 1 | NEEDS_EVIDENCE | 35,725 | 26,885 | 25,880 | 23,980 | 62,610 |
| 2 | NEEDS_EVIDENCE | 105,351 | 30,582 | 28,905 | 145,262 | 135,933 |
| 3 | PASS | 180,022 | 33,892 | 31,317 | 212,825 | 213,914 |

Nguồn mỗi hàng là `validation/R7-four-checkpoint-review{1,2,3}/receipt.json`; nguồn Claude là `validation/{R7,R8,R9}-completion/receipt.json`. Raw total không được cộng thêm raw thinking/cache-read. Không suy từ các counter chưa rõ scope ra số tiền hoặc hạn mức tiêu thụ.

## Phần cần giảm ở workflow

Hai lượt đầu chưa chốt được nghiệm thu: lượt 1 thiếu biên nhận đã có, lượt 2 dùng hook gắn với thư mục cũ. Đây là overhead điều phối quan sát được trong chính đợt này. Việc sửa là đính kèm đủ evidence trước dispatch, kiểm hook/đường dẫn bằng code trước khi gọi model, và giữ một owner cho bước chốt.

Theo yêu cầu hiện tại, mỗi review hoàn tất đã gọi một Claude continuation, không có model polling. Một tối ưu tiếp theo có thể là để code chốt các receipt/template thông thường và chỉ gọi Claude khi cần đánh giá hoặc viết tổng hợp; đó là đề xuất, chưa triển khai và chưa có A/B. Không dùng việc có ba callback để bịa hệ số tiết kiệm khi bỏ callback.

