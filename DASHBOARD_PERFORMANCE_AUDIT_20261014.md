# Kiểm tra hiệu suất dashboard — 14/10/2026

Đọc production `100.97.194.15`, DB `odoo19`, và tab Chrome dashboard đang mở.
Không sửa source, cấu hình, chỉ mục hoặc dữ liệu production trong lần kiểm tra.
Các thay đổi XML/SCSS bỏ CO₂ của yêu cầu trước được giữ nguyên, chưa commit.

## Số đo

- UI Chrome, bấm Làm mới ở 24H, chờ thanh tải xuất hiện rồi biến mất:
  9.085 ms. Đây là thời gian qua điều khiển trình duyệt, có chi phí điều khiển;
  không phải một bản ghi Network/Performance trace của Chrome DevTools.
- Log HTTP cùng lần refresh, 16:46:37 giờ Việt Nam: 109 queries;
  SQL 2,424 s; phần xử lý ngoài SQL 0,704 s; tổng phía Odoo khoảng 3,128 s.
- Chưa phân tách được khoảng chênh còn lại thành mạng, chờ request, parse/render
  và chi phí điều khiển trình duyệt. Không kết luận toàn bộ 9 giây là SQL.
- Gọi đúng model/API aggregator trực tiếp trong Odoo, transaction read-only,
  giữ cache PostgreSQL như đang vận hành, xoá ORM cache mỗi lần:

| Phạm vi | Tổng | SQL | Số queries | JSON |
|---|---:|---:|---:|---:|
| 24H, tất cả hệ thống, lần 1 | 2,977 s | 2,311 s | 122 | 49.658 B |
| 24H, tất cả hệ thống, lần 2 | 2,976 s | 2,288 s | 108 | 49.657 B |
| 24H, hệ thống 1 | 2,686 s | 2,105 s | 114 | 49.632 B |
| Live/2 phút, tất cả hệ thống | 2,074 s | 1,677 s | 108 | 19.591 B |

Thời gian phương thức dưới đây có thể bao gồm phương thức con; không cộng trực
tiếp các hàng để ra tổng request. Số đo 24H tất cả hệ thống, hai lần:

| Phương thức | Thời gian | Nhận xét |
|---|---:|---|
| get_overview_kpi | 1,499–1,563 s | Phần lớn nhất; chứa điện năng tháng và nhiều KPI cũ |
| get_energy_widgets | 0,710–0,736 s | Nằm trong overview; tính scope tổng và từng công-tơ |
| get_energy_comparison, cộng 3 lần | 0,884–0,914 s | Gồm các lần của widgets và biểu đồ |
| get_energy_distribution, cộng 2 lần | 0,413–0,453 s | Today trong KPI cũ và khoảng biểu đồ |
| get_battery_flow_series | 0,372–0,409 s | Vẫn cần cho đường sạc/xả và MPPT/PV |
| get_heatmap_data | 0,391–0,397 s | Ba truy vấn raw 30 ngày |
| get_performance_alert | 0,142–0,152 s | Giao diện không còn dùng |
| _get_peak_power_time | 0,066–0,071 s | Đỉnh cũ theo giờ, giao diện không dùng |
| Các today counter cũ | khoảng 0,25 s | Bốn truy vấn 60–75 ms, độc lập widgets mới |
| get_battery_series | 0,026 s | Biểu đồ batteryChart đã bỏ canvas |
| get_bms_history trong API tổng | 0,059 s | Component BMS lấy lại qua API riêng |
| get_monthly_comparison | 0,001 s | Không dùng; không phải nút thắt lớn |
| Tổng vòng đời lưu sẵn, đo lại riêng | 0,007 s | Không tính lại raw cả vòng đời |

## Truy vấn thừa và request BMS

- `get_dashboard_data()` vẫn trả `battery`, `monthly_comparison`, `bms_history`.
  Template không có batteryChart hoặc biểu đồ so sánh tháng. `BmsHistory` có
  state riêng và gọi `/smartsolar/dashboard/bms_history`, không dùng history
  của API tổng. Callback realtime ở parent còn cập nhật history riêng không
  truyền vào component; cần gỡ cả phần này khi bỏ payload thừa.
- Các trường CO₂ vẫn được tính trong overview dù hai ô đã bỏ. Phép nhân CO₂
  rất nhẹ, nhưng nguồn `today_kwh` và các nhánh KPI cũ liên quan vẫn query raw.
- Self-consumption, grid dependency, yield, CO₂ trees và performance alert
  còn getter/model logic nhưng không có chỗ dùng trong template hiện tại.
- Các định nghĩa `_get_total_energy`, `_get_total_limiter_energy`,
  `_get_total_pv_energy` cũ vẫn tồn tại nhưng không được F5 gọi; xoá riêng định
  nghĩa không tạo cải thiện hiệu suất. Không xoá API/helper mà chưa xét caller.
- Parent truyền `state.lastRefresh` vào BMS refreshToken. Callback từng
  `smartsolar_data` message đổi timestamp này; effect BMS gọi lại history.
  Log các phút hoàn tất 16:45–16:51 ghi nhận 42–52 history requests/phút.
  Tải lại history cần đi theo mẫu lịch sử/nhịp lưu BMS, không theo mỗi bus frame.
- `get_battery_flow_series` không phải query thừa: gridTieChart dùng net_power,
  MPPT/PV dùng mppt_output_power. Không bỏ hàm này khi bỏ batteryChart cũ.

## EXPLAIN ANALYZE và tăng trưởng

- Battery flow: 13.512 anchors trong 24H, hai lateral branches đều chạy
  13.512 vòng (khoảng 27.024 lần tìm/ghép). Execution 412 ms, khoảng 85.000
  shared-buffer hits. Có index record_date nhưng thuật toán vẫn lặp theo mẫu.
- Heatmap: sequential scan khoảng 61.497 dòng GTI mỗi nhánh; một nhánh 130 ms.
- Performance alert: sequential scan toàn raw vì lọc bằng timezone(...date/time).
  Date index không hỗ trợ trực tiếp điều kiện này. Truy vấn today cũ cũng có
  kiểu lọc đó. Đổi sang UTC start/end giữ ngày local, dùng được date index.
- Freshness của điện năng tháng vẫn đọc toàn cửa sổ raw để COUNT/MAX(write_date).
  PV ngày cũng gom raw cả tháng dù có summary. Cache hiện chỉ sống trong một
  request; scope tổng và scope từng hệ thống tạo cache keys khác nhau.
- Raw chưa có compound indexes `(device_id, record_date, id)` hoặc
  `(system_id, record_date)`. Chỉ mục đơn record_date hiện được dùng ở nhiều
  truy vấn; không nên tạo thêm index trước khi chốt query và đo lại plan.
- Snapshot: GTI 61.493 dòng/29,7 MB; MPPT 61.588 dòng/26,0 MB;
  GTI summary 418 dòng; MPPT summary 411 dòng; BMS telemetry 612 dòng.
- Cấu hình production: raw 30 ngày, hourly 365 ngày, daily không xoá; cron
  purge/aggregate active. Hai thiết bị lấy mẫu 10 giây liên tục tạo 17.280
  dòng/ngày, khoảng 518.400 raw rows/cửa sổ 30 ngày. Không suy ra thời gian
  query tuyến tính chắc chắn từ số dòng; cần benchmark dữ liệu quy mô mục tiêu.
- Máy tại lần đọc: load average 0,28/0,21/0,26; RAM available khoảng 4,4 GB.
  Swap có dữ liệu nhưng chưa có bằng chứng swap đang hoạt động gây chậm.

## Thứ tự tối ưu đề xuất

1. Tách mốc snapshot/API khỏi mốc bus realtime; BMS history chỉ tải khi đổi
   hệ thống/khoảng thời gian, làm mới chủ động hoặc có nhịp mẫu lịch sử mới.
   Bỏ payload và query không dùng của API tổng, giữ compatibility của các API
   riêng nếu có caller bên ngoài. Gỡ getters/renderers/buffer chết sau khi kiểm
   tra dependencies. Giữ battery flow đang dùng, các đỉnh PV/tải hiện đang hiển thị.
2. KPI hôm nay/tháng và biểu đồ điện ngày đọc sổ ngày/tháng đã xác minh, dùng
   cùng cutoff và biểu giá. Không kiểm tra lại raw của mọi ngày trên F5. Job
   nền phát hiện dữ liệu sửa muộn và đánh dấu/cập nhật phần bị ảnh hưởng;
   UI nêu thời điểm cập nhật, unknown và coverage. Tránh tính riêng scope tổng
   rồi tính lại từng công-tơ nếu có thể tổng hợp từ cùng các kết quả per-system.
3. Heatmap lấy summary giờ có metadata hợp lệ, trung bình có trọng số mẫu;
   raw chỉ cho phần giờ hiện tại hoặc phần chưa được tổng hợp. Không lấy
   AVG(voltage) × AVG(current) thay cho trung bình tích cùng mẫu.
4. Battery flow chuyển sang ghép timeline một lượt, giữ nguyên quy tắc nearest
   ±10 giây và thiếu thiết bị; hoặc lưu summary phút của kết quả ghép đã xác minh.
   Đo composite indexes trên DB test quy mô 30 ngày/nhiều thiết bị trước khi tạo
   trên production. Các query còn dùng phải có UTC range, không bọc timestamp
   trong WHERE khi có thể tính start/end ở ngoài.
5. Frontend hiển thị khung và KPI trước, tải biểu đồ phụ/BMS/heatmap sau. Tải
   thư viện Chart.js và dữ liệu song song thay vì await tuần tự. Nếu cache API,
   cache ngắn theo scope/quyền công ty/timezone/range và version dữ liệu/cấu hình;
   realtime bus không bị chờ cache. Bổ sung đo click→request→response→render để
   phân tách khoảng trễ phía client/mạng trước khi tuyên bố đã xử lý đủ 9 giây.

Mục tiêu nghiệm thu, chưa phải số đo đạt được: API tổng <1 giây ở quy mô raw
30 ngày; phần KPI đầu <0,5 giây; tăng tuổi hệ thống không gây quét raw toàn vòng
đời; BMS không gọi history theo từng message. So sánh cùng snapshot điện năng,
coverage, unknown, biểu giá/VAT, reset và sửa muộn trước/sau tối ưu. Đo thêm
trình duyệt thật ở lần mở đầu và refresh; không chỉ báo thời gian Python/SQL.
