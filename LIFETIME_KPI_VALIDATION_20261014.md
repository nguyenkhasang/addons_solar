# KPI tích luỹ Smart Solar — 14/10/2026

## Hành vi

- Dashboard đọc `smartsolar.lifetime`: tổng điện inverter cấp tải, điện lấy lưới,
  PV và tiền tiết kiệm. Không đọc raw, summary, bảng ngày/tháng để tính các tổng này.
- Job mỗi phút phát hiện raw/summary mới hoặc sửa bằng chỉ mục `write_date`;
  đưa ngày liên quan và ngày lân cận vào hàng đợi lưu trong DB. Xử lý tối đa 31
  ngày mỗi hệ thống/lần, ưu tiên gần hiện tại. Khởi tạo lịch sử chạy nền theo lô.
- `smartsolar.lifetime.day` dùng cùng kiểm tra nguồn với KPI điện năng phía trên.
  Không dùng bộ đếm `limiter_total` đứng ở 0 hoặc bộ đếm inverter cuối cùng làm
  tổng vòng đời. Khoảng thiếu không ngoại suy thành điện năng.
- `smartsolar.lifetime.month` tính tiền theo tổng điện trong từng tháng, từng
  hệ thống/công-tơ. Biểu giá và VAT được lưu theo tháng; kỳ đã đóng giữ biểu giá
  khi sửa dữ liệu điện năng cũ. Tháng hiện tại áp dụng cấu hình hiện tại.
- Tổng hệ thống thay đổi bằng `giá trị tháng mới - giá trị tháng cũ`, trong cùng
  giao dịch. Advisory lock ngăn hai worker cập nhật trùng. Lỗi giao dịch không
  để lại tổng đã tăng mà chưa có bản ghi tháng tương ứng.
- Khi nguồn cũ đã được dọn và không còn raw để tính lại, giữ điện năng đã lưu
  trong sổ ngày. Nếu raw còn và kết quả xác minh trở thành không hợp lệ, đánh
  dấu thiếu và trừ phần đã ghi nhận khỏi tổng.
- Sổ tích luỹ không thuộc cron dọn raw/summary. Liên kết hệ thống dùng restrict;
  xoá thiết bị không xoá sổ ngày/tháng. Người dùng nội bộ chỉ đọc; job chạy dưới
  root. Có rule công ty cho ba model mới.

## Giới hạn lịch sử và tài chính

- Không suy đoán điện năng trước mốc có dữ liệu. Hiển thị mốc bắt đầu, số ngày
  thiếu, số ngày có dữ liệu một phần, trạng thái khởi tạo và thời điểm cập nhật.
- Snapshot production 15:24:49 giờ Việt Nam: lịch sử từ 07/09/2026, 25 ngày có
  điện năng, 13 ngày thiếu, 16 ngày có dữ liệu một phần; các giá trị là ước tính.
- Tổng tại snapshot: inverter 47,979 kWh; lấy lưới 76,889 kWh; tiết kiệm 104.663 ₫.
  Tháng 10: inverter 47,929 kWh; lấy lưới 28,147 kWh; tiết kiệm 104.556 ₫.
  Tổng vòng đời và tháng hiện tại khác phạm vi nên không phải luôn bằng nhau.
- Các kỳ quá khứ khởi tạo chưa có lịch sử biểu giá: dùng cấu hình lúc khởi tạo
  và công khai đây là ước tính. Không khẳng định đây là hoá đơn đã đối soát.
  Tiền tiết kiệm chưa trừ bảo trì, thay thế hay đầu tư; chưa phải tỷ lệ hoàn vốn.
- Sửa bằng ORM/collector được phát hiện qua write_date. SQL trực tiếp không đổi
  write_date hoặc xoá nguồn thủ công cần chủ động đưa ngày vào pending_days /
  chạy `_refresh_dates` trong công việc bảo trì; không tự suy đoán mọi lần xoá
  là điều chỉnh điện năng, vì raw cũng được xoá theo chính sách lưu trữ.

## Bằng chứng

- DB test riêng `smartsolar_kpi_test_20261014`: bộ trước 79 post-tests không lỗi;
  bản cuối dashboard 58 post-tests không lỗi (thêm ca nguồn sửa thành invalid).
  Logs: `/tmp/lifetime_test_v2_20261014.log`, `/tmp/lifetime_test_final_20261014.log`.
- Test tổng: chạy lại không cộng trùng; sửa tăng/giảm; bậc thang theo tháng;
  đóng kỳ giữ giá; unknown khác zero; rollback nguyên tử; counter reset;
  limiter_total đứng 0; dọn nguồn; late raw; tách hệ thống; backfill theo lô.
- Owl thật trong jsdom: template mới render được unknown, zero, tổng, mốc lịch
  sử, thiếu/một phần và trạng thái khởi tạo. Hồi quy thiết bị realtime và click
  qua. SCSS biên dịch thành công. Chưa kiểm tra hình thức trên trình duyệt thật.
- Production: đối chiếu toàn bộ ngày với phép tính độc lập ở cùng cutoff không
  sai lệch; tổng tiền từng tháng độc lập = 104.663 ₫, bằng tổng lưu sẵn.
- Đọc tổng với ORM cache đã xoá: 5 truy vấn, 5,437 ms; không truy vấn nguồn raw/
  summary. Toàn API dashboard ở lần đo: 2,7093 giây. Thay đổi này giảm chi phí
  và sửa nghĩa của tổng vòng đời; không khẳng định toàn dashboard chỉ mất 5 ms.
- Worker đo trên production: 0,6076 giây. Module đã nâng cấp; Odoo và listener
  active. Backup mã trước triển khai: `/tmp/lifetime_kpi_backup_20261014T082328Z/`.

## Khôi phục

Giữ bản backup và `preflight.json` trên server. Khi cần khôi phục giao diện cũ,
vô hiệu job `smartsolar_dashboard.ir_cron_lifetime_refresh`, dừng Odoo, phục hồi
6 file trước triển khai và bỏ 6 file mới theo preflight, rồi khởi động Odoo.
Giữ các bảng sổ tích luỹ để không mất kết quả đã ghi nhận; không xoá raw hay
summary.
