# JK BMS qua BLE trên SmartSolar / Odoo 19

## Kiến trúc và phạm vi

`JK BMS → BlueZ/Bleak → smartsolar-bms.py → Odoo ORM → latest/history → bus.bus → OWL/Chart.js`.
Daemon dùng virtualenv/config/registry cùng Odoo, chạy ở tiến trình riêng. Không tạo HTTP write endpoint, không cần ODOO_URL/ODOO_TOKEN; không giữ BLE connection trong Odoo worker. Không thay đổi MQSolar hoặc dữ liệu solar cũ.

Đã đọc thật ngày 10/10/2026: JK_PB1A16S10P, hardware 19A, firmware 19.10,
BLE `506177F45004950-15`, address `A4:C1:38:01:94:F7`. Service FFE0,
characteristic FFE1 handle 9 cho write-without-response và notification.
Firmware này cho đọc mà không pairing/password. Không triển khai authentication giả;
không dùng JK_BMS_PASSWORD. Firmware/model khác bị từ chối cho đến khi được xác minh.

## An toàn protocol

Chỉ builder `read_request(0x97)` và `read_request(0x96)` tồn tại; length và payload luôn 0.
Không có command ghi settings, điều khiển MOS, reset, calibration, password hoặc protection.
BLE write request là thao tác đọc ở application protocol; đăng ký notification chỉ ghi CCCD.
Frame ghép theo header 55 AA EB 90, 300 byte, sum8 checksum tại byte 299.
Các byte thừa ở gói 320 byte được bỏ qua khi tìm header tiếp theo.
Layout JK02_32S đã xác minh bằng 16 cell hoạt động, tổng cell khớp điện áp pack, SOC/capacity và frame device-info.

Tham khảo source và layout: https://github.com/syssi/esphome-jk-bms/blob/main/components/jk_bms_ble/jk_bms_ble.cpp
và https://github.com/syssi/esphome-jk-bms/blob/main/docs/protocol-design-ble.md .
Dòng signed int32 /1000, dương=sạc, âm=xả. Công suất signed V×A, không dùng trường unsigned power.
Nhiệt độ int16 /10; sentinel ±32767/−32768 là không có số đo. Byte 214 trên thiết bị thật là 255,
không dùng byte này để loại bỏ các nhiệt độ hợp lệ (source tham khảo cũng không dùng mask này để lọc nhiệt).
Không suy ra SOC từ điện áp. Alarm giữ bitmask thô, không tự diễn giải mức nguy hiểm.

## Requirements

Ubuntu, BlueZ, Python 3.12 virtualenv của Odoo, Odoo 19, PostgreSQL.

```bash
../odoo-19.0/venv/bin/python -m pip install 'bleak>=0.22,<3'
systemctl status bluetooth
rfkill list bluetooth
./smartsolar-bms.sh scan
```

Scanner liệt kê tên/address/service UUID. FFE0 chỉ là ứng viên; nhận dạng model/firmware sau device-info.
Nếu có nhiều ứng viên, phải chọn JK_BMS_DEVICE; không tự chọn thiết bị ngẫu nhiên.
Ngắt kết nối app điện thoại khi collector cần giữ kết nối BMS.

## Cài Odoo và cấu hình

Backup trước upgrade; dừng Odoo/MQSolar listener, chạy `-u smartsolar,smartsolar_dashboard --stop-after-init --no-http`, rồi khởi động lại.
Trong menu Smart Solar → Pin JK BMS, tạo pin gắn đúng hệ thống/company/address.
Có thể provision qua script đã chuẩn bị, sau khi phê duyệt triển khai:

```bash
../odoo-19.0/venv/bin/python smartsolar-bms-setup.py \
  --odoo-dir ../odoo-19.0 --config ../odoo-19.0/odoo.conf \
  --device <address đã xác minh> --system-id <ID hệ thống>
```

Tạo user collector không có quyền quản trị, gán nhóm `SmartSolar BMS collector` và quyền người dùng nội bộ.
Collector được đọc/cập nhật master pin, tạo history; không được tạo/xóa master hoặc sửa/xóa history.
Admin cấu hình pin; người dùng nội bộ chỉ đọc BMS. Record rules giới hạn company.

Tạo file local `../odoo-19.0/.odoo-runtime/jk-bms.env`, chmod 600:

```bash
JK_BMS_DEVICE=<address hoặc tên BLE chính xác>
JK_BMS_BATTERY_ID=<ID pin Odoo>
ODOO_BMS_USER_ID=<ID collector>
READ_INTERVAL=2
# ODOO_DATABASE=<database>    # tùy chọn, mặc định từ odoo.conf
# JK_BMS_DEBUG=1             # chỉ raw telemetry; không log raw device-info
```

Không cần secret HTTP; collector không cần mật khẩu đăng nhập để dùng local ORM.
File env dùng cú pháp shell, do user local quản lý. Chỉ cấu hình giá trị tin cậy.
`ODOO_PROJECT_DIR` đổi thư mục Odoo; `JK_BMS_ENV_FILE` đổi vị trí cấu hình.
History interval mặc định 60s, tối thiểu 30s; retention mặc định 30 ngày, dọn qua cron.
Latest được ghi tối đa mỗi 1–5s; history lưu cells dạng JSON cùng các cột chart.
Ngưỡng stale=10s, offline=60s, idle=.2A, delta warning=.03V; sửa ở form pin.
DB timestamp UTC naive, payload timestamp ISO UTC; giao diện dùng múi giờ trình duyệt.

## systemd và vận hành

Chạy bằng user Linux thường, không root. Khi đã phê duyệt triển khai:

```bash
mkdir -p "$HOME/.config/systemd/user"
ln -s "$PWD/smartsolar-bms.service" "$HOME/.config/systemd/user/smartsolar-bms.service"
systemctl --user daemon-reload
systemctl --user enable --now smartsolar-bms.service
./smartsolar-bms.sh status
./smartsolar-bms.sh logs
./smartsolar-bms.sh start
./smartsolar-bms.sh stop
./smartsolar-bms.sh restart
```

Boot trước đăng nhập cần user linger (máy này đã dùng linger cho Odoo); kiểm tra `loginctl show-user "$USER" -p Linger`.
Restart=on-failure, backoff BLE 1–60s. Không có telemetry trong 60s thì reconnect.
Odoo/DB lỗi: retry có backoff, giữ state mới nhất, không phát lại dữ liệu cũ như realtime.
Không spool toàn bộ lịch sử trong lúc DB offline; đó là khoảng thiếu dữ liệu thật.
Nếu daemon bị kill, BlueZ có thể giữ kết nối khiến BMS ngừng quảng bá. Reader nhận lại
Device1 đã Connected theo đúng address cấu hình và service FFE0, rồi subscribe lại.
Session advisory lock đảm bảo một daemon/pin; mất DB lock thì process thoát và systemd khởi động lại để lấy lock mới.

## Dashboard

Phần “Pin · JK BMS trực tiếp” nằm trong dashboard hiện có: SOC/progress, V/A/W,
trạng thái mẫu, nhiệt MOS/pin, Ah, chu kỳ, 16 cells/min/max/delta, MOS/balance/alarm bits,
ONLINE/STALE/OFFLINE và thời điểm mẫu. Dữ liệu cũ có cảnh báo rõ.
Các widget ước tính từ MPPT/AC vẫn có nhãn nguồn riêng; không dùng để hiệu chỉnh BMS.
Chart.js hiện có hiển thị SOC, V, A, W, ΔV và nhiệt pin 1 theo khoảng đang chọn.
Tối đa 240 điểm/chart và 10.000 mẫu gần nhất trong khoảng; có cảnh báo khi giới hạn.
Mẫu mới Live được thêm mỗi chu kỳ history. Lịch sử backend được nạp lại khi refresh/chọn khoảng.

## Debug / tests

```bash
bluetoothctl --timeout 25 scan le
journalctl -u bluetooth -n 30
journalctl --user -u smartsolar-bms.service -n 50
../odoo-19.0/venv/bin/python smartsolar/tests/test_jk_bms_protocol.py
../odoo-19.0/venv/bin/python smartsolar/tests/test_jk_bms_transport.py
node smartsolar_dashboard/tests/test_dashboard_battery.cjs
node smartsolar_dashboard/tests/test_system_overview.cjs
# Test render bằng runtime OWL thật và DOM test:
npm install --prefix /tmp/jk-bms-owl-check jsdom@26
NODE_PATH=/tmp/jk-bms-owl-check/node_modules node smartsolar_dashboard/tests/test_bms_panel.cjs
```

Test ORM trên database riêng: Odoo `--test-enable --test-tags smartsolar_bms --stop-after-init`;
dùng port HTTP/gevent riêng nếu Odoo live đang chạy.
Fixture `smartsolar/tests/fixtures/jk_pb1a16s10p_19_10.hex` là frame telemetry thật, không có device-info/password.
Không log byte device-info: frame này có thể chứa password ngay cả khi không cần password để đọc.
Không bật debug Bleak/dbus-fast vì có thể log raw device-info.

## Kiểm chứng và giới hạn

Đã nhận raw telemetry thật liên tục, kiểm checksum và decode 16 cell, V/A/SOC/Ah/chu kỳ/nhiệt/MOS/balance/alarm.
Fixture: 52.376V, −3.800A, −199.0288W, SOC34%, 100Ah danh định, 34.333Ah còn lại,
cell min 3.272V/max3.276V, Δ4mV, nhiệt pin1 34.7°C.
Dòng âm ứng với chiều xả theo implementation protocol; chưa so sánh trực tiếp app JK hoặc ampe kế.
Chưa thử tắt nguồn vật lý BMS. Collector đã được triển khai; xem kết quả verification cuối tài liệu.

Kiểm tra bổ sung: transport/parser mới kết nối lại thiết bị thật thành công và nhận 3 state
qua chính code mới: 52.289–52.293V, −4.914 đến −5.110A, SOC32%, đủ 16 cell,
nhiệt pin1 34.5–34.6°C. Unit transport mô phỏng disconnect/reconnect đạt;
3 test ORM/backend trên database clone đạt 0 lỗi. Runtime OWL mount thực tế trong
DOM test hiển thị 16 cell, SOC, số đo signed và ONLINE/STALE/OFFLINE đúng.
Database live đã upgrade module có backup; Odoo/MQSolar active, /web/login HTTP200.
Sau xác nhận của người dùng, đã tạo collector và bản ghi pin, bật smartsolar-bms.service.
Service enabled/active, Linger=yes. Latest state, 16 cell và historical sample đã được
xác minh trong database live bằng chính quyền collector; backend dashboard trả history thành công.
Vẫn chưa kiểm tra trực quan dashboard live trong phiên đăng nhập người dùng.

Verification service ngày 10/10/2026: enabled + Linger=yes; BLE reader ghi latest
mỗi ~2 giây và history đã tăng từ 1 lên 2 mẫu. Odoo restart không dừng collector.
SIGKILL test phát hiện kết nối BlueZ còn tồn tại; đã sửa nhận lại cache của đúng BMS
và bổ sung unit test exact-address/connected/service. Log không còn lặp đôi.
Service giữ StartLimitBurst=10/120s và RestartSec=30s: lỗi boot/DB được thử lại
mỗi 30 giây, nhịp tự retry thấp hơn giới hạn chống restart dồn dập.

Cập nhật giao diện theo yêu cầu: ẩn card “Pin · JK BMS trực tiếp” riêng và chart của
card đó. SYSTEM OVERVIEW nhận snapshot/realtime BMS từ dashboard, thay hai dòng
ghi chú MPPT/inverter bằng SOC, điện áp pack và 16 điện áp cell, có tuổi mẫu và
ONLINE/STALE/OFFLINE. Các chỉ số công suất ước tính trên sơ đồ vẫn giữ nhãn nguồn.
Sửa SCSS min(100%,420px) không tương thích Sass của Odoo. Toàn bộ web.assets_web
đã compile/rebuild thành công; CSS/JS mới được kiểm tra qua HTTP.

### Tùy chọn không lưu lịch sử

Trong SmartSolar → Pin JK BMS → mở pin, bỏ chọn **Lưu lịch sử** rồi lưu.
Collector vẫn cập nhật trạng thái mới nhất và gửi dữ liệu trực tiếp lên dashboard; chỉ ngừng tạo mẫu lịch sử mới.
Lịch sử cũ không bị xóa ngay và vẫn được dọn theo thời hạn lưu trữ (mặc định 30 ngày).
Bật lại tùy chọn để tiếp tục lấy mẫu theo nhịp đã cấu hình. Mặc định tùy chọn bật để giữ hành vi hiện có.
