# Quản lý Odoo với systemd

Service dùng tài khoản Linux hiện tại, tự khởi động khi máy bật nhờ user linger và tự restart sau 5 giây nếu tiến trình lỗi. Dừng chủ động bằng `stop` sẽ không tự restart; service vẫn chạy lại ở lần boot tiếp theo.

```bash
cd /home/sangnk/addons_solar
git pull && ./odoo-service.sh restart
./odoo-service.sh status
./odoo-service.sh logs
```

Script giữ log tại `../odoo-19.0/.odoo-runtime/odoo.log`. Xem log quản lý tiến trình bằng `journalctl --user -u smartsolar-odoo.service`.

## Cài đặt trên máy có cùng cấu trúc thư mục

```bash
chmod +x odoo-service.sh
mkdir -p "$HOME/.config/systemd/user"
ln -s "$PWD/smartsolar-odoo.service" "$HOME/.config/systemd/user/smartsolar-odoo.service"
loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable --now smartsolar-odoo.service
```

Nếu bật linger yêu cầu quyền quản trị, dùng `sudo loginctl enable-linger "$USER"`.

Unit mặc định tìm `~/addons_solar` và `~/odoo-19.0`. Cần chỉnh đường dẫn trong unit nếu cài ở nơi khác. Khi sửa unit, chạy `systemctl --user daemon-reload` trước khi restart.

Để tắt tự chạy khi boot: `systemctl --user disable smartsolar-odoo.service`.
Giới hạn restart: 10 lần khởi động trong 120 giây. Nếu service lỗi liên tục và chạm giới hạn, sửa lỗi rồi chạy `systemctl --user reset-failed smartsolar-odoo.service` và khởi động lại.

## Bộ nhận MQSolar liên tục

`smartsolar-listener.service` dùng cùng Python, cấu hình và database với Odoo,
nhưng chạy riêng: không mở HTTP và không chạy cron. Mỗi hệ thống có một kết nối
WebSocket, ping mỗi 20 giây, tự kết nối lại với thời gian chờ 1–30 giây. Nếu không
nhận được dữ liệu thiết bị trong 60 giây, bộ nhận sẽ mở lại kết nối. Danh sách
thiết bị/token được kiểm tra lại mỗi 30 giây.

Bus được commit ngay từng message. Mẫu raw lưu khoảng mỗi 5 giây cho
mỗi thiết bị; mỗi mẫu mới ghi kèm nhịp 5 giây để tổng hợp đúng độ phủ. Mẫu cũ
không có nhịp vẫn dùng cấu hình legacy `smartsolar.sync_interval_seconds`
(60 giây). PostgreSQL advisory lock ngăn cron/manual hoặc một bộ nhận thứ hai
subscribe trùng hệ thống. Cron nhận WebSocket được tắt; cron tổng hợp giờ/ngày
và dọn raw vẫn hoạt động.

Khi áp dụng lần đầu, dừng listener và Odoo, cập nhật code rồi upgrade module
`smartsolar` để thêm metadata nhịp mẫu. Sau đó khởi động lại Odoo và bật listener:

```bash
ln -s "$PWD/smartsolar-listener.service" "$HOME/.config/systemd/user/smartsolar-listener.service"
systemctl --user daemon-reload
systemctl --user enable --now smartsolar-listener.service
systemctl --user status smartsolar-listener.service
```

Log nhận dữ liệu: `../odoo-19.0/.odoo-runtime/listener.log`. Khi sửa code Python,
restart cả hai dịch vụ. Nếu cần quay lại nhịp cũ, dừng listener rồi kích hoạt lại
cron nhận WebSocket; giữ nguyên các cột metadata để không mất dữ liệu đã ghi.
Không xóa hoặc sửa mẫu raw lịch sử.
