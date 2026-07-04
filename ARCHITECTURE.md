# addons_solar — Hệ thống giám sát điện mặt trời trên Odoo 19

> **Dành cho AI/dev mới:** Đọc file này trước tiên. Nó mô tả toàn cảnh repo — 3 module, kiến trúc, luồng dữ liệu, và mục đích từng phần — đủ để bạn định vị được nên sửa ở đâu mà không phải quét toàn bộ code.

---

## 1. Repo này là gì

Một bộ 3 module Odoo 19 giám sát **hệ điện mặt trời Hybrid**: thu dữ liệu thiết bị qua WebSocket, lưu và tổng hợp trong PostgreSQL, hiển thị dashboard realtime, và cho AI Agent đọc dữ liệu để viết báo cáo.

```
addons_solar/
├── smartsolar/             # LÕI: model dữ liệu, đồng bộ WebSocket, tổng hợp, cron
├── smartsolar_environment/ # Thời tiết: lat/long + Open-Meteo, cron 5 phút
├── smartsolar_dashboard/   # UI: dashboard OWL, biểu đồ, KPI, realtime qua bus.bus
├── smartsolar_ai/          # AI Tool Layer: Function/Tool Calling cho LLM (không chatbot)
└── smartsolar_ai_chat/     # Trợ lý AI trong Discuss: planner loop Ollama + Tool Layer
```

Quan hệ phụ thuộc:
```
smartsolar  ◄── smartsolar_environment (environment depends smartsolar, _inherit system)
smartsolar  ◄── smartsolar_dashboard   (dashboard depends smartsolar)
smartsolar  ◄── smartsolar_ai          (ai depends smartsolar)
smartsolar_ai ◄── smartsolar_ai_chat   (chat depends ai + mail, tái dùng Tool Layer)
```
`smartsolar` là nền tảng. Các module kia là các "mặt tiêu thụ / mở rộng" độc lập trên cùng dữ liệu.

---

## 2. Kiến trúc vật lý của hệ điện (RẤT QUAN TRỌNG)

Đây là **hệ Hybrid: 1 dàn PV đi qua 2 thiết bị nối tiếp.** Hiểu sai điểm này sẽ tính sai mọi KPI (đếm trùng năng lượng).

```
   PV  ──►  MPPT (charge_power)  ──►  Pin  ──►  GTI (grid_tie_inverter)  ──►  Lưới
            [Thiết bị 1]                        [Thiết bị 2]
            lấy điện PV nạp pin                  hòa lưới / bán tải
```

| Thiết bị | `device_type` | Model dữ liệu | Vai trò | Đại lượng chính |
|---|---|---|---|---|
| MPPT | `charge_power` | `charge.power` | PV → nạp pin | `charge_power` (W), `pv_voltage/current`, `bat_voltage/current`, `total_kwh` |
| GTI | `grid_tie_inverter` | `grid.tie.inverter` | Pin → hòa lưới | `output_power` (hòa lưới), `limiter_power` (lấy lưới), `energy_total` |

**Quy tắc tính toán:** công suất hòa lưới **chỉ** lấy từ GTI `output_power`; PV thu **chỉ** lấy từ MPPT. **KHÔNG cộng GTI + MPPT** như hai nguồn độc lập — sẽ đếm trùng.

---

## 3. Module `smartsolar` — Lõi dữ liệu

**Mục đích:** thu thập, lưu trữ, tổng hợp dữ liệu thiết bị. Không có UI phức tạp.

### Model
```
smartsolar.system          Hệ thống PV (cấu hình WebSocket, token, công suất kWp)
  └─ smartsolar.device      Thiết bị (GTI hoặc MPPT), gắn device_guid
       ├─ grid.tie.inverter        dữ liệu thô GTI, 1 bản ghi/phút
       ├─ charge.power             dữ liệu thô MPPT, 1 bản ghi/phút
       ├─ grid.tie.inverter.summary   tổng hợp theo giờ/ngày (bucket)
       └─ charge.power.summary        tổng hợp theo giờ/ngày (bucket)
```
> `lat/long` và `smartsolar.environment` nằm ở module `smartsolar_environment` (mục 3b), không thuộc lõi.

### Luồng thu dữ liệu (WebSocket → DB → realtime)
`smartsolar_system.py :: _sync_devices_from_mqsolar_websocket()`:
1. Cron (`_sync_all_systems_data`) chạy mỗi phút → mở kết nối WebSocket tới MQSolar Cloud.
2. Subscribe device theo `device_guid`, lắng nghe trong `mqsolar_ws_listen_seconds` (~20s).
3. Mỗi message tới: **push ngay lên `bus.bus`** (2 kênh: `smartsolar.realtime.{id}` và `smartsolar.realtime.all`) để dashboard vẽ realtime — dùng cursor riêng để commit tức thì.
4. Hết thời gian nghe: chỉ giữ **message cuối cùng của mỗi device** → tạo **1 bản ghi thô/phút** trong DB.

> Thiết kế "1 bản ghi/phút": thiết bị gửi ~1s/lần, nhưng chỉ lưu bản mới nhất mỗi chu kỳ để không phình DB. Realtime vẫn mượt vì đi qua bus.bus, không qua DB.

### Tổng hợp & dọn dẹp (cron trong `smartsolar_system.py`)
- `_cron_aggregate_hourly` / `_cron_aggregate_daily`: gom bảng thô → bảng summary (bucket giờ/ngày) bằng SQL `date_trunc`. Năng lượng bucket = `MAX(counter) - MIN(counter)`.
- `_cron_purge_old_data`: xóa dữ liệu cũ theo config (`raw_retention_days`=30, `hourly`=365...).

### Chuyển đổi định dạng
`utils.py`: MQSolar payload → "legacy API shape" (`dataStreams`). `detect_mqsolar_device_type()` nhận diện GTI/MPPT theo topic/field.

### Múi giờ
DB lưu **UTC naive**. Hiển thị cần đổi sang **UTC+7** (Asia/Ho_Chi_Minh).

---

## 3b. Module `smartsolar_environment` — Dữ liệu thời tiết

**Mục đích:** thu thập dữ liệu môi trường từ Open-Meteo, gắn theo từng hệ thống. Module bổ sung độc lập — cài thì hệ thống có thêm tính năng, gỡ thì `smartsolar` trở lại nguyên trạng.

### Model
```
smartsolar.system (_inherit)   + latitude / longitude / environment_ids + logic Open-Meteo
smartsolar.environment          dữ liệu thời tiết, 1 bản ghi / 5 phút
```

### Cách mở rộng
- `models/smartsolar_system.py` dùng `_inherit = 'smartsolar.system'` để **cắm thêm** field `latitude`/`longitude` + method gọi API, KHÔNG sửa module gốc.
- `views/smartsolar_system_views.xml` dùng **xpath inheritance** để thêm nhóm toạ độ, nút "Lấy dữ liệu môi trường", và tab Môi trường vào form hệ thống có sẵn.

### Luồng
`smartsolar_system.py :: _fetch_environment_data()` + `_parse_open_meteo()`:
- Mỗi hệ thống có `latitude`/`longitude` (cấu hình tay trên form, mặc định Hà Nội).
- Cron `_cron_fetch_environment` chạy **5 phút/lần** → gọi Open-Meteo (`requests` thuần, không cần API key) → tạo **1 bản ghi `smartsolar.environment`**.
- Lưu đầy đủ: nhóm `current` (nhiệt độ, độ ẩm, mây, áp suất, mưa...) + nhóm `daily` hôm nay (bình minh/hoàng hôn, bức xạ sóng ngắn, UV, giờ nắng, weather_code...).
- `weather_code` (chuẩn WMO) được diễn giải sang mô tả tiếng Việt qua computed field.
- Dùng để đối chiếu sản lượng PV với điều kiện thời tiết và cho tầng AI phân tích.

---

## 4. Module `smartsolar_dashboard` — Giao diện

**Mục đích:** dashboard OWL (Owl 2) trong Odoo backend, biểu đồ + KPI + realtime.

### Backend (`models/smartsolar_dashboard.py`, ~1100 dòng)
Model transient `smartsolar.dashboard` với các method trả JSON cho frontend:
- `get_overview_kpi()` — KPI hybrid-aware: công suất, sản lượng hôm nay/tổng, self-consumption %, grid-dependency %, yield kWh/kWp, CO₂, cây xanh, peak power time, cảnh báo hiệu suất.
- `get_*_series()` — chuỗi thời gian: charge power, grid tie (4 đường: hòa lưới/lấy lưới/PV/sạc pin), battery, PV efficiency, temperature.
- `get_heatmap_data()`, `get_monthly_comparison()`, `get_energy_flow_series()`, `get_energy_distribution()`, `get_device_status()`.
- Tự chọn nguồn raw vs summary theo độ dài khoảng; đổi UTC→UTC+7.

### Controller (`controllers/dashboard_controller.py`)
Endpoint: `/smartsolar/dashboard/data`, `/smartsolar/dashboard/kpi`, `/smartsolar/realtime/poll`.

### Frontend (`static/src/components/`)
- `dashboard/` — component OWL chính (JS/XML/SCSS). Chart.js cho biểu đồ, Canvas API cho heatmap. Có **realtime buffer** tích lũy dữ liệu ngầm (max 120 điểm) để không mất khi chuyển time range.
- `system_overview/` — sơ đồ topology (PV→MPPT→Pin→GTI→Lưới) với animation dòng chảy, **luôn chạy realtime** khi mở dashboard.

### Realtime: dùng `bus.bus` của Odoo (WebSocket thật)
Frontend subscribe kênh `smartsolar.realtime.all` qua `bus_service` (Odoo 19: `subscribe(type, cb)`). Backend push từ vòng lặp WebSocket ở mục 3. Đây là WebSocket thật qua SharedWorker của Odoo, không phải long-polling.

Time range: `Live / 1H / 6H / 12H / 24H / 1 tuần / 1 tháng`.

---

## 5. Module `smartsolar_ai` — Tầng Tool cho AI Agent

**Mục đích:** cho LLM (Ollama/LM Studio/OpenAI) đọc dữ liệu qua **Tool/Function Calling**. Không phải chatbot, không phải rule engine. **LLM không chạm DB, không sinh SQL — chỉ gọi Tool.**

```
User → AI Planner (LLM) → Tool Layer → Business Service → Repository → ORM → PostgreSQL → JSON → LLM → Báo cáo
```

### Nguyên tắc: Tool theo NĂNG LỰC, không theo câu hỏi
9 tool tổng quát, **metric là tham số** — ghép lại trả lời vô số câu hỏi mà không phải viết tool mới:
`list_metrics` · `get_timeseries` · `get_aggregate` · `compare_periods` · `get_device_status` · `get_alarms` · `find_anomalies` · `get_health_score` · `forecast`

### Điểm mở rộng DUY NHẤT: `domain/metric_registry.py`
Thêm đại lượng đo mới (bức xạ, độ ẩm, SOC...) = thêm **1 dòng `MetricSpec`**. Không sửa Service/Tool/Adapter (Open/Closed). Logic hybrid khai báo ở đây một lần.

### Các tầng
```
domain/        enums, value_objects (TimeRange UTC+7↔UTC), metric_registry ★, dto — không phụ thuộc Odoo
repositories/  tầng DUY NHẤT chạm ORM/SQL; 1 cỗ máy truy vấn cho mọi metric, tự chọn raw/summary
services/      business logic: analytics, anomaly, health, forecast, device
tools/         lớp bọc mỏng + phong bì {ok, data, meta, error}; registry điều phối
adapters/      openai_adapter, mcp_adapter — đọc chung ToolRegistry.specs()
controllers/   REST + MCP endpoint (/solar/ai/*) — Web/Mobile cũng dùng chung tool
```

Chi tiết đầy đủ: xem [`smartsolar_ai/README.md`](smartsolar_ai/README.md).

---

## 6. Bản đồ luồng dữ liệu tổng thể

```
                    MQSolar Cloud (WebSocket wss://...)
                              │  ~1 msg/giây
                              ▼
        ┌───────────────────────────────────────────────┐
        │  smartsolar: _sync_devices_from_mqsolar_...()   │  (cron mỗi phút)
        └───────────────────────────────────────────────┘
              │ push mỗi message                    │ lưu bản cuối/phút
              ▼                                      ▼
     bus.bus (realtime)                     grid.tie.inverter / charge.power  (bảng thô)
              │                                      │ cron aggregate hourly/daily
              ▼                                      ▼
     Dashboard OWL (Live)              *.summary (bucket giờ/ngày)
                                                     │
                        ┌────────────────────────────┴────────────────────────────┐
                        ▼                                                           ▼
        smartsolar_dashboard (biểu đồ/KPI)                        smartsolar_ai (Tool → LLM → báo cáo)
```

---

## 7. Bảng tra nhanh "muốn sửa X thì vào đâu"

| Muốn làm gì | File / vị trí |
|---|---|
| Đổi tần suất lưu / logic WebSocket | `smartsolar/models/smartsolar_system.py` |
| Thêm field dữ liệu thô từ thiết bị | `smartsolar/models/{grid_tie_inverter,charge_power}.py` + `utils.py` |
| Sửa cách tổng hợp giờ/ngày | `smartsolar/models/*_summary.py` |
| Đổi vị trí lat/long hoặc logic gọi thời tiết | `smartsolar_environment/models/smartsolar_system.py` (`_fetch_environment_data`, `_parse_open_meteo`) |
| Thêm/sửa field dữ liệu môi trường | `smartsolar_environment/models/smartsolar_environment.py` + `_OM_CURRENT_VARS/_OM_DAILY_VARS` |
| Thêm/sửa KPI hoặc biểu đồ dashboard | `smartsolar_dashboard/models/smartsolar_dashboard.py` + `static/src/components/dashboard/` |
| Sửa sơ đồ topology realtime | `smartsolar_dashboard/static/src/components/system_overview/` |
| Cho AI truy vấn đại lượng mới | `smartsolar_ai/domain/metric_registry.py` (thêm 1 `MetricSpec`) |
| Thêm năng lực AI mới (loại phân tích mới) | thêm Service + 1 Tool trong `smartsolar_ai/` |
| Đổi LLM / thêm giao thức gọi tool | `smartsolar_ai/adapters/` |

---

## 8. Quy ước & lưu ý chung

- **Múi giờ:** DB = UTC naive; hiển thị/AI = UTC+7. Luôn đổi ở tầng ngoài, đừng lưu lệch.
- **Hybrid:** không cộng GTI + MPPT. Hòa lưới = GTI `output_power`; PV thu = MPPT.
- **Realtime:** đi qua `bus.bus`, KHÔNG lưu DB. Dữ liệu lịch sử mới nằm ở bảng thô/summary.
- **Hiệu năng:** truy vấn dài dùng bảng `*_summary`; raw SQL `date_trunc` chỉ ở tầng Repository/model.
- **Placeholder trong AI:** `grid_import_energy` và `load_energy` đang trỏ tạm (chưa có công-tơ lấy lưới/tải riêng) — xem `smartsolar_ai/services/analytics_service.py::_ENERGY_SOURCES`.
- **Phụ thuộc Python:** `websocket-client` (module `smartsolar`).

---

## 9. Cài đặt & chạy

```bash
# Cài đặt / nâng cấp
odoo -i smartsolar,smartsolar_dashboard,smartsolar_ai
odoo -u smartsolar_dashboard        # nâng cấp riêng

# Test tầng AI
odoo -i smartsolar_ai --test-enable --test-tags smartsolar_ai

# Sau khi sửa JS/SCSS dashboard: xóa asset cache + hard refresh (Ctrl+Shift+R)
# DELETE FROM ir_attachment WHERE url LIKE '/web/content/%' AND name LIKE '%assets_backend%';
```
