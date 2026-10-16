# Dùng chung lượt tổng hợp biểu đồ — 16/10/2026

## Phạm vi

Gộp đọc dữ liệu cho bốn reader charge_power, grid_tie, temperature và
pv_efficiency trong request get_dashboard_data. Một lượt SQL cho bảng MPPT,
một lượt cho inverter trên mỗi đoạn thời gian/source. Raw ở đầu/cuối khoảng
summary cũng dùng cùng bộ cache theo source, bucket expression, start/end và
system_id. Cache chỉ tồn tại trong request, không giữ kết quả qua các lần F5.

GROUPING SETS tạo kết quả toàn phạm vi và từng thiết bị từ cùng nguồn. Nhiệt độ
vẫn theo thiết bị. AVG(V*I), AVG từng cột với null, MAX và sample_count weighted
summary giữ đúng biểu thức cũ; không suy ra AVG(V*I) từ AVG(V)*AVG(I).
Các dòng thiếu device vẫn tham gia thống kê toàn phạm vi như trước; nhiệt độ
chỉ hiển thị thiết bị tồn tại, giống inner join cũ. Không join chéo hai bảng raw.

Public getters độc lập và bộ lọc device_ids giữ reader cũ. Các phép ghép luồng
pin nearest-time, counter delta/validation, ledger năng lượng, heatmap và BMS
history giữ cơ chế riêng. Không ép cả dashboard thành một SQL. Chưa gộp phân
bổ điện với cột theo ngày vì khác phạm vi/cơ chế xác minh công-tơ: cộng số ngày
không mặc nhiên tương đương xác minh toàn khoảng.

## Kiểm chứng

- DB riêng solar_shared_test_20261016: 69 post-tests dashboard, 0 failed,
  0 errors; thống kê Odoo gồm 85 tests. Log máy chủ:
  /tmp/solar-shared-full-20261016.log.
- Regression mới: nhiều thiết bị/hệ thống, scope tổng/hệ thống, dữ liệu null,
  dòng không có device, zero, V/I thay đổi, sample_count khác nhau, raw ở
  ranh giới summary, timezone Việt Nam/Nepal/New York, dữ liệu rỗng, subset
  thiết bị, cache riêng từng request. Assert bundle thực hiện đúng 2 SQL.
- 18 trường hợp JavaScript test_dashboard_battery.cjs qua. Python AST và
  git diff --check qua. Không đổi frontend trong phần refactor này.
- Đối chiếu old-reader/new-reader trên dữ liệu production bằng cùng code
  formatter, cùng cutoff và transaction REPEATABLE READ READ ONLY. Không
  khác output nhóm bốn reader cho Live/2 phút, 1H, 6H, 24H, 1 tuần, 1 tháng,
  1 năm, 5 năm ở scope hệ thống và tổng; không khác output toàn bộ API biểu
  đồ cho 6H/24H/1 tuần/1 tháng.

## Số đo

Nhóm bốn reader, scope hệ thống 1, median 3 lần sau warmup, đảo thứ tự:

| Khoảng | Reader riêng | Dùng chung |
|---|---:|---:|
| 6H | 49,78 ms | 42,57 ms |
| 24H | 93,80 ms | 53,72 ms |
| 1 tuần | 32,11 ms | 33,92 ms |
| 1 tháng | 61,80 ms | 39,16 ms |

Scope tổng/raw giảm 4 SQL tổng hợp xuống 2; scope hệ thống có thêm truy vấn
metadata/timezone nên tổng reader 8 xuống 6. Summary có đoạn raw đầu/cuối
cũng chia sẻ. Một số summary nhỏ không nhanh hơn do overhead grouping sets.

Toàn API biểu đồ, scope hệ thống 1, median 3 lần sau warmup, đảo thứ tự,
cùng cutoff UTC 2026-10-16 07:25:01 và snapshot DB:

| Khoảng | Trước | Sau | Số SQL trước → sau |
|---|---:|---:|---:|
| 6H | 477,08 ms | 387,35 ms | 39 → 37 |
| 24H | 927,81 ms | 919,86 ms | 52 → 50 |
| 1 tuần | 3080,03 ms | 2984,05 ms | 60 → 54 |
| 1 tháng | 3266,03 ms | 3234,10 ms | 69 → 63 |

Số đo API là gọi model trực tiếp, không gồm HTTP, browser render hoặc latency
mạng. Là dữ liệu thực hiện tại, không phải DB có đủ 5 năm hoặc tải nhiều người.
Các phép tổng hợp/ghép pin/điện năng khác vẫn chiếm phần lớn ở khoảng dài;
không suy rộng mức giảm ~43% của nhóm 24H thành giảm 43% toàn dashboard.
Một phép đo đơn ban đầu 6H dao động ngược; dùng median có warmup ở bảng trên.

## Trạng thái

Code refactor và tests ở workspace local; bản ứng viên chỉ được đặt trong
/tmp/solar-shared-check để kiểm thử. Không chép source vào repo production,
không restart, không commit/push trong đợt này. Thay đổi local có sẵn về Live,
khoảng điện năng theo bộ lọc và index raw được giữ nguyên, không thuộc refactor.
Benchmark bật/tắt sharing trên cùng bản ứng viên để cô lập ảnh hưởng của refactor.

Production chỉ được SELECT read-only. DB thử nghiệm được tạo riêng, đã kết
thúc tất cả test và xoá sau kiểm thử. Không sửa raw, summary, ledger production.
