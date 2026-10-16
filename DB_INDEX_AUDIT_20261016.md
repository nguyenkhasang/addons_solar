# Kiểm tra index và ràng buộc DB SmartSolar — 16/10/2026

Máy chủ `100.97.194.15`, DB `odoo19`, PostgreSQL 16.15, Odoo 19.
Source máy chủ tại `43b639f`. Đo khoảng 13:47–13:49 giờ Việt Nam.
Các lần kiểm tra dùng transaction read-only, statement timeout 15 giây,
lock timeout 2 giây. Không tạo/xoá index, sửa dữ liệu, chạy VACUUM/ANALYZE
hoặc sửa source production. Các thay đổi local có sẵn được giữ nguyên.

## Kết luận

Khóa chính và khóa ngoại đang có đầy đủ trên các quan hệ nghiệp vụ được rà.
Toàn schema public có 276 khóa chính, 854 khóa ngoại, không có FK chưa
validated; không thấy bảng thường thiếu PK hoặc index invalid/not-ready.
Không đồng nghĩa mọi cột FK đã có index ở phía bảng con.

Catalog/index được rà chi tiết cho 20 bảng lớn nhất theo dung lượng và toàn
bộ bảng SmartSolar/raw/summary. Không phát hiện index trùng chính xác trong
phạm vi đó; các index có cột đầu trùng nhau vẫn có thể chồng lấp một phần.
Số idx_scan là thống kê tích luỹ, không dùng một mình để quyết định xoá index.

## Bảng chính

Số dòng đếm chính xác tại một lần đọc; hệ thống tiếp tục ghi nên sẽ tăng.
Dung lượng gồm heap, index và TOAST, từ catalog ở lần lấy mẫu gần đó.

| Bảng | Dòng | Tổng dung lượng | Đánh giá |
|---|---:|---:|---|
| bus_bus | 710.251 | 387,14 MiB | Lớn nhất; chỉ có PK(id), thiếu index cho lọc create_date |
| grid_tie_inverter | 76.199 | 34,47 MiB | PK/FK hợp lệ; có index thời gian, device/time và write/time |
| charge_power | 76.295 | 30,55 MiB | Tương tự inverter |
| smartsolar_battery_telemetry | 1.959 | 2,38 MiB | PK/FK hợp lệ; có index battery_id và record_date riêng |

Hai bảng summary có khoảng 460 dòng/bảng theo thống kê; có PK, FK device/system,
UNIQUE(bucket_start, bucket_type, device_id), các index lọc bucket/device/system.
Sổ lifetime có UNIQUE(system_id), UNIQUE(lifetime_id, day/month) và index
lifetime_id/ngày/tháng. Không thấy thiếu index trọng yếu ở các bảng này.

## Raw: index đã có và phần còn thiếu

Cả hai bảng raw có:

- PK(id).
- Index(record_date), index(device_guid).
- Index(device_id, record_date, id): đã sử dụng hàng nghìn lần, hỗ trợ truy
  vấn seed theo thiết bị và FK device_id; không cần thêm device_id đơn.
- Index(write_date, system_id): phục vụ phát hiện dữ liệu mới/sửa muộn.
- FK device_id → smartsolar_device; system_id → smartsolar_system;
  company_id → res_company; create_uid/write_uid → res_users.

Chưa có index bắt đầu bằng system_id. `(write_date, system_id)` không thay thế
được index đó để lọc system_id đơn lẻ. Đề xuất `(system_id, record_date, id)`
cho hai bảng raw khi triển khai tối ưu tiếp theo: hỗ trợ lọc hệ thống/thời gian
và thao tác FK. Với chỉ một hệ thống hiện tại, độ chọn lọc thấp; không cam kết
cải thiện lớn trước khi đo ứng viên. Không cần index mọi cột chỉ vì là FK:
company_id/create_uid/write_uid hiện không phải điểm nóng đã chứng minh.

smartsolar_device.system_id và smartsolar_system.user_id cũng chưa có index
bắt đầu bằng cột FK; bảng nhỏ, ưu tiên thấp. Các bảng summary đã có index FK
nghiệp vụ device_id/system_id. FK audit/company phần lớn chưa có index riêng.

## Kế hoạch và thời gian SELECT thực tế

EXPLAIN ANALYZE SELECT, mặc định planner, không tắt seqscan; một lần đo mỗi
truy vấn, không phải benchmark tải đồng thời hay thời gian toàn bộ dashboard.

| Truy vấn đại diện | Kế hoạch | Thời gian |
|---|---|---:|
| Bus kết nối ban đầu: channel + create_date > 50 giây trước | Parallel Seq Scan ~710k dòng | 123,127 ms |
| Bus lần sau: channel + id > last_id thực | Index Scan PK(id) | 0,064 ms |
| Lịch sử BMS 12 giờ, battery_id + record_date DESC | Index Scan record_date | 0,270 ms |
| Raw inverter 24 giờ, lọc system_id, sắp record_date/id | Bitmap index record_date + sort 7.716 dòng | 5,811 ms |
| Seed raw theo device_id, record_date/id DESC LIMIT 1 | Index Only Scan device_time_idx | 0,053 ms |

Một phép đo bus ban đầu dùng giá trị id qua subquery cho kế hoạch kém phù hợp;
đã đo lại bằng tham số literal đúng kiểu ứng dụng. Kết luận incremental dùng
kết quả literal 0,064 ms, không dùng kết quả subquery chậm để quy lỗi cho PK.

## Bus và ưu tiên xử lý

1. Ưu tiên index `bus_bus(create_date)` cho truy vấn kết nối đầu tiên. Đoạn
   source Odoo lọc theo thời gian nhưng DB chỉ có PK(id); đã có bằng chứng
   quét toàn bảng. Cần đo trước/sau khi được triển khai, không tạo trong audit.
2. Xem lại tần suất dọn bus cho workload realtime cao. Mặc định giữ 24 giờ,
   cron Base Auto-vacuum chạy một lần/ngày, active, failure_count=0; vì vậy
   dữ liệu có thể gần 48 giờ trước lần dọn kế tiếp. Hiện oldest UTC 14/10
   15:00:55, lần cron trước UTC 15/10 15:00:53, lần kế UTC 16/10 14:59:58;
   chưa có bằng chứng cron hỏng. Dọn định kỳ thưa gây kích thước tăng theo chu kỳ.
   Không thay đổi retention/xoá thông báo trong lần kiểm tra này.
3. Raw `(system_id, record_date, id)` ở hai bảng: ưu tiên khả năng tăng nhiều
   hệ thống và FK; đo lợi ích vì hiện chỉ một hệ thống.
4. BMS `(battery_id, record_date, id)` là ứng viên khi nhiều pin/dữ liệu tăng;
   hiện chỉ ~2k dòng, truy vấn 0,27 ms, chưa cần thêm ngay.

Không có pg_stat_statements trên DB, nên đây là khảo sát catalog và một số
SELECT đại diện, chưa phải thống kê top SQL toàn bộ workload. EXPLAIN cho
điều kiện dọn bus là SELECT count, không chạy DELETE; điều kiện cắt 24 giờ
quét khoảng 127 ms, planner có thể vẫn nên seqscan khi loại một phần lớn bảng.
Index mới có chi phí ghi/dung lượng; không đề xuất thêm tất cả FK hoặc xoá
index ít dùng chỉ dựa trên snapshot. Chưa kiểm tra từng orphan ngoài các FK
hiện có hoặc tính toàn vẹn vật lý bằng amcheck.

## Bổ sung sau audit — người dùng yêu cầu triển khai index raw

Đã tạo bằng `CREATE INDEX CONCURRENTLY` trên production, ngày 16/10/2026:

- `grid_tie_inverter_system_time_idx (system_id, record_date, id)` — 3.112.960 bytes.
- `charge_power_system_time_idx (system_id, record_date, id)` — 3.121.152 bytes.

Cả hai `indisvalid=true`, `indisready=true`. Không sửa/xoá bản ghi hoặc index cũ;
không restart. Raw tiếp tục có mẫu mới sau khi tạo index.
Source local `smartsolar_lifetime.py:init` đã thêm lệnh IF NOT EXISTS tương ứng
để giữ cấu trúc khi nâng cấp module; chưa commit/push, không chép source lên server.

SELECT EXPLAIN ANALYZE trước/sau cùng system_id=1 và cutoff UTC
`2026-10-16 07:10:30.788273`, một lần đo mỗi truy vấn, không phải SLA:

| Truy vấn | Trước | Sau | Plan sau |
|---|---:|---:|---|
| Inverter 24h | 8,318 ms | 7,303 ms | Bitmap record_date + sort |
| Charge power 24h | 7,335 ms | 7,004 ms | Bitmap record_date + sort |
| Inverter latest | 0,054 ms | 0,041 ms | Index Scan system_time_idx |
| Charge power latest | 0,039 ms | 0,054 ms | Index Scan system_time_idx |

Planner đã chọn index mới cho latest; với 24h của hệ thống duy nhất hiện tại
vẫn chọn index record_date. Chênh lệch rất nhỏ, có cache/nhiễu; không khẳng định
index mới làm dashboard nhanh đáng kể ở quy mô hiện tại. Không tạo thêm index bus.
Python syntax và git diff --check qua. Rollback nếu cần: DROP INDEX CONCURRENTLY
public.grid_tie_inverter_system_time_idx và public.charge_power_system_time_idx;
chỉ thực hiện khi được yêu cầu. Không tự rollback index đang hợp lệ.
