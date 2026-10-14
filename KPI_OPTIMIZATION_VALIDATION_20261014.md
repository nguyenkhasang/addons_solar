# Kiểm chứng tối ưu KPI SmartSolar — 14/10/2026

Đã triển khai lên SSH `100.97.194.15`, database `odoo19`. Source local và source máy đích được đối chiếu hash.

## Thay đổi

- Tăng chu kỳ lưu raw từ 5 lên 10 giây; vẫn gửi từng message realtime lên bus.
- Ngày hoàn tất dùng summary phiên bản chất lượng 4 khi metadata hợp lệ và dữ liệu không bị thay đổi sau khi tổng hợp. Ngày hiện tại, khoảng chưa đủ ngày, summary thiếu thiết bị/metadata hoặc có mẫu đến muộn đều quay về raw.
- Kiểm tra cả mẫu lân cận nửa đêm trong cửa sổ 300 giây để tránh dùng tích phân cũ.
- Tái sử dụng kết quả điện năng trong một request, theo hệ thống/múi giờ/khoảng thời gian; cache không tồn tại giữa các request.
- Tìm đỉnh bằng một lượt qua mẫu theo thứ tự thời gian; giữ cửa sổ đồng bộ 10 giây, đủ thiết bị, mẫu mới nhất khi trùng timestamp, thời điểm sớm nhất khi bằng đỉnh và giá trị 0/thiếu dữ liệu.
- Khung nhiều tháng/năm giữ ngày trống là thiếu dữ liệu, bỏ các truy vấn raw riêng cho từng ngày trống.

## Đối chiếu dữ liệu thật

Thời gian được cố định tại `2026-10-14 04:38:21` UTC. Bản cũ và bản mới chạy trong cùng snapshot database, transaction chỉ đọc. Đây là thời gian backend; không gồm mạng và thời gian vẽ trình duyệt.

| Phạm vi | Trước | Sau | Giảm |
|---|---:|---:|---:|
| Toàn hệ thống | 7.804 s | 2.980 s | 61.8% |
| Hệ thống 1 | 6.254 s | 2.740 s | 56.2% |

- Mỗi phạm vi kiểm tra 65 giá trị điện năng tuần/tháng còn raw: khớp chính xác ở độ chính xác API hiển thị. Toàn bộ KPI ngoài nhóm tổng hợp điện năng tháng, và các phần dashboard khác, cũng khớp trước/sau.
- Tiền điện, tiền tiết kiệm, số ngày tính tiền và tổng từng chỉ số tháng được đối chiếu lại với chuỗi điện năng ngày và hàm tính biểu giá đã có test riêng.
- Một thay đổi dự kiến: các ngày 01–04/10 còn summary hợp lệ nhưng đã xoá raw được đưa lại vào KPI tháng. Ngày 05–06/10 không có dữ liệu hợp lệ vẫn để thiếu; không tự tạo điện năng.

| Ngày được đọc lại từ summary | Inverter kWh | Lấy lưới kWh | Tiêu thụ kWh |
|---|---:|---:|---:|
| 2026-10-01 | 0.000 | 4.991 | 4.991 |
| 2026-10-02 | 0.000 | 8.387 | 8.387 |
| 2026-10-03 | 0.000 | 8.322 | 8.322 |
| 2026-10-04 | 0.000 | 0.619 | 0.619 |

## Kiểm thử

- 64 test Odoo trên database riêng `smartsolar_kpi_test_20261014`: 0 failed, 0 errors. Log máy SSH: `/tmp/smartsolar_kpi_test_release_20261014.log`.
- 26 unit test: tổng hợp KPI/tiền điện, biểu giá bậc thang/VAT, listener và nhịp raw 10 giây — đều qua.
- 29 ca JavaScript hiện có về battery/dashboard/system overview — đều qua.
- Test mới bao gồm: summary so với raw và đoạn qua nửa đêm; giữ điện năng/tiền điện sau xoá raw; ngày chưa hoàn tất; mẫu đến muộn trong ngày và sát nửa đêm; thiếu thiết bị; độ phủ; metadata cũ; nhánh âm/thiếu; reset công-tơ; lệch tỷ lệ công-tơ/công suất; cache khác hệ thống/cutoff; đổi tháng; ngày trống dài hạn; trùng timestamp/NULL/0/ranh giới 10 giây/thời điểm đỉnh; tái sử dụng battery balance.
- Syntax và `git diff --check` qua.

## Xác nhận sau triển khai

- Dịch vụ user `smartsolar-odoo.service` và `smartsolar-listener.service` active; `/web/login?db=odoo19` trả HTTP 200.
- Đo lại source thực tế đã triển khai: backend 24h khoảng 3.107 s toàn hệ thống và 2.673 s hệ thống 1.
- Sau restart: mỗi thiết bị có 9 mẫu raw mới, toàn bộ `sample_interval_seconds=10`. Khoảng cách MPPT 10.018–11.000 s; inverter 10.002–11.012 s. Chu kỳ lưu là tối thiểu 10 s và chờ message kế tiếp.
- Cùng giai đoạn đo có 197 message ở kênh bus theo hệ thống và 197 message ở kênh toàn hệ thống. Đây là một nhóm frame được gửi tới cả hai kênh; không cộng thành 394 frame độc lập.
- Backup source trước triển khai trên máy SSH: `/tmp/smartsolar_kpi_backup_20261014T043925Z/`.

## Giới hạn kiểm chứng

- Chưa kiểm tra trực tiếp thao tác/render trong trình duyệt và chưa đo endpoint dashboard bằng phiên người dùng đã đăng nhập. Các số đo KPI là ORM/backend trực tiếp trên database thật với source đã triển khai.
- Ba bộ JavaScript dùng `jsdom` không khởi chạy trong môi trường local vì thiếu dependency này; không có thay đổi JavaScript trong bản sửa.
- Kết quả trước/sau xác nhận phép tính trên cùng dữ liệu. Nhịp raw 10 s có độ phân giải lịch sử thấp hơn raw 5 s; đỉnh nằm giữa hai mẫu có thể không được ghi lại.
- Các số điện năng/tiền điện theo thời điểm sau triển khai tiếp tục thay đổi khi có mẫu mới.
