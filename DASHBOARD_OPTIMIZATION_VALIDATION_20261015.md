# Tối ưu dashboard — kiểm chứng ngày 15/10/2026

## Thay đổi đã triển khai

- Tách `/smartsolar/dashboard/overview` khỏi phần biểu đồ. KPI, trạng thái thiết
  bị, BMS snapshot và thời tiết được hiển thị trước; `/data` lấy biểu đồ với
  `include_overview=false`, không tính lại phần overview.
- Overview trả UTC cutoff; request biểu đồ dùng cùng cutoff. Các bộ lọc khoảng
  thời gian, ngày/tháng địa phương dùng cutoff đó, tránh lệch ranh giới ngày.
  Hai HTTP request vẫn là hai transaction; chỉnh sửa/sửa muộn commit giữa hai
  request có thể chỉ xuất hiện ở snapshot kế tiếp, không phải lịch sử MVCC chung.
- Nạp Chart.js song song với overview. Giữ biểu đồ cũ khi refresh và báo đang
  tải biểu đồ; loại phản hồi đến muộn sau khi đổi hệ thống/range hoặc refresh mới.
- API dashboard không gọi battery chart cũ, monthly comparison, BMS history
  trùng lặp, KPI CO₂/self-consumption/yield/performance alert cũ. Endpoint KPI
  và các helper cũ giữ tương thích; định nghĩa không được gọi không gây query.
- Tách `snapshotRefresh` khỏi timestamp bus realtime. BMS history chỉ tải khi
  snapshot chủ động thay đổi, đổi hệ thống/range hoặc đến chu kỳ 30–300 giây
  phù hợp lịch sử; tôn trọng tạm dừng auto-refresh, huỷ timer khi unmount.
- Ngày hoàn tất đọc sổ ngày đã xác minh. Ngày pending, ngày chưa có sổ, phần
  đầu/cuối chưa đủ ngày dùng reader raw đã có. Chia sẻ kết quả cùng ngày giữa
  KPI và biểu đồ trong request. Giữ unknown, estimated, coverage và biểu giá/VAT.
- Scope tổng nhiều hệ thống hoặc timezone không tương thích với sổ một hệ
  thống giữ reader cũ để tránh cộng các kết quả counter validation khác nghĩa.
- Heatmap gom raw theo giờ UTC một lượt/table, tính nhiều metric chung; chuyển
  timezone sau khi gom. Dùng statistics summary giờ đã xác minh, kiểm tra count
  và write_date để tránh summary stale; không thay AVG(V×I) bằng AVG(V)×AVG(I).
  Heatmap vẫn đọc cửa sổ raw tối đa 30 ngày để kiểm tra/ghép, không phải zero raw.
  Timezone lệch giờ lẻ giữ reader cũ để bảo toàn ranh giới giờ địa phương.
- Ghép pin đọc hai timeline thay cho lateral query từng anchor, giữ nearest
  ±10 giây, tie theo id lớn nhất, voltage eligibility trước deduplication,
  thiếu thiết bị → unknown, trung bình per-system rồi cộng hệ thống.
- Thêm index `(device_id, record_date, id)` cho hai bảng raw. Counter không có
  mẫu hợp lệ trong cửa sổ không cần tìm seed trong toàn bộ lịch sử trước đó;
  zero được khai báo hợp lệ vẫn được xử lý như trước.

## Kiểm chứng độ chính xác

- So sánh code trước với ứng viên trong CÙNG transaction/snapshot DB và cùng
  cutoff: 24H, 1 tuần, 1 tháng, Live/2 phút. Không khác charge_power, grid_tie,
  temperature, battery_flow, pv_efficiency, distribution hoặc energy widgets
  (điện ngày/tháng, peak date, tiết kiệm, còn phải trả, VAT, billing days).
- Một so sánh ban đầu ở hai thời điểm DB khác nhau thấy thêm bucket giờ 07:00
  do cron tổng hợp chạy giữa hai lần đo; đã thay bằng đối chiếu cùng snapshot,
  không bỏ qua hoặc sửa số liệu để làm phép so sánh qua.
- Gate đầy đủ trước tối ưu seed: 87 post-tests hai module không lỗi.
  Gate cuối dashboard: 66 post-tests, 0 failed/0 errors; log trên server
  `/tmp/dashboard_perf_pass_20261015.log`. Kiểm tra cả zero hợp lệ/không hợp lệ,
  counter reset, dữ liệu thiếu, pending day, late raw và paired timestamps.
- 9 file test JavaScript/Owl qua: KPI hiện trước khi chart RPC hoàn tất;
  10 bus messages không tạo BMS history RPC mới; request charts giữ cutoff;
  response cũ không đè hệ thống mới. Thêm kiểm tra lỗi mạng không treo spinner.
- XML/Python và `git diff --check` qua. Không sửa raw hoặc lịch sử production.

## Benchmark

Trước/sau cùng snapshot production, ứng viên trước tối ưu seed cuối:

| Khoảng | Trước | Sau | Sai lệch dữ liệu |
|---|---:|---:|---|
| 24H | 2,273 s | 1,030 s | Không |
| 1 tuần | 4,351 s | 2,917 s | Không |
| 1 tháng | 4,473 s | 3,050 s | Không |
| Live/2 phút | 1,546 s | 0,486 s | Không |

DB benchmark riêng: 30 ngày, 2 thiết bị, mẫu mỗi 10 giây, **518.400 raw rows**.
Có counter đứng 0 để phản ánh trường hợp production; số học day ledger dựa
trên công suất hằng đã biết và PV register. So với đúng counter SQL cũ:

| Phần | Trước | Bản cuối |
|---|---:|---:|
| API tổng 24H | 7,204 s | 1,412 s |
| Overview riêng | — | 0,229 s |
| Charts riêng | — | 1,351 s |

Không lệch KPI/tiền hoặc dữ liệu biểu đồ. Dữ liệu giả rollback và DB benchmark
`smartsolar_perf_bench_20261015` đã xoá; không chèn vào production.

Production sau triển khai, đọc lạnh ORM: overview 0,196–0,290 s; charts
1,005–1,052 s. HTTP thực của Chrome: overview 0,215–0,251 s; charts
0,928–1,356 s. Không cộng các lần đo khác nhau thành SLA.

Chrome thật: một lần refresh cho KPI hiện khoảng **1,724 s**, hết trạng thái
đang tải biểu đồ khoảng **2,739 s**; không có console error/warn. Đây là đo
qua tự động hoá UI, có chi phí điều khiển, không phải Chrome Performance trace.
Một lần mở tab mới timeout ở công cụ điều khiển rồi trang vẫn tải xong; chưa
khẳng định cold-load toàn Odoo/asset không cache dưới 3 giây. Các số baseline
UI 9,1 s là ngày 14/10, không phải benchmark A/B cùng snapshot ngày 15/10.

## Giới hạn và vận hành

- Mục tiêu API tổng <1 giây chưa đạt trên fixture 30 ngày (1,412 s); KPI riêng
  đã dưới 0,5 giây. Không đổi/giảm độ chính xác để đạt một ngưỡng thời gian.
- Chưa benchmark nhiều người dùng đồng thời/nhiều hệ thống. Scope tổng nhiều
  hệ thống vẫn fallback bảo toàn semantics; không cam kết tốc độ như 1 hệ thống.
- Ngày chốt phản ánh job gần nhất; có timestamp lịch sử. Late data được job
  cập nhật theo chênh lệch. Ngày thiếu/partial không biến thành zero hoặc ngoại suy.
- Raw retention 30 ngày, hourly 365 ngày, daily không xoá được giữ nguyên.
- Backup nguồn: `/tmp/dashboard_perf_backup_20261015T014523Z/` và preflight.json.
  Khi rollback: dừng Odoo, khôi phục 9 file gốc/bỏ 3 file mới theo manifest,
  khởi động và rebuild assets. Hai index có thể giữ vì không đổi dữ liệu.
- Odoo/listener active, assets HTTP 200, cả hai index có trong production.
  Không commit/push trong yêu cầu triển khai này.
