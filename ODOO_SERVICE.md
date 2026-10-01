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
