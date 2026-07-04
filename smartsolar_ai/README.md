# Smart Solar AI Tools — Tầng Python Tool Layer cho AI Agent

Tầng Tool (Function/Tool Calling) để AI đọc dữ liệu điện mặt trời và tự viết báo cáo.
**AI KHÔNG chạm database, KHÔNG sinh SQL — chỉ gọi các Tool đã định nghĩa.**

```
User → AI Planner (Ollama/LM Studio/OpenAI) → Tool Layer
     → Business Service → Repository → Odoo ORM → PostgreSQL → JSON → AI → Báo cáo
```

---

## 1. Nguyên tắc cốt lõi: Tool theo NĂNG LỰC, không theo câu hỏi

Sai (không mở rộng được): `today_report()`, `compare_week()`, `get_battery()`...
Mỗi câu hỏi mới lại phải viết hàm mới.

Đúng (dùng cho vô số câu hỏi): 9 tool tổng quát, **metric là tham số**.

| Tool | Trả lời lớp câu hỏi |
|---|---|
| `list_metrics` | "Hệ thống đo được những gì?" — AI tự khám phá |
| `get_timeseries(metric, start, end)` | mọi câu về diễn biến theo thời gian của 1 đại lượng |
| `get_aggregate(metrics[], start, end)` | mọi câu tổng kết (kWh, đỉnh, trung bình) |
| `compare_periods(metrics[], A, B)` | "hôm nay vs hôm qua", "3 ngày gần nhất vs cùng kỳ năm ngoái" |
| `get_device_status()` | thiết bị nào online/offline, offline bao lâu |
| `get_alarms(start, end)` | lịch sử cảnh báo/sự cố |
| `find_anomalies(metric, start, end)` | "X có bất thường không?" |
| `get_health_score(start, end)` | điểm sức khỏe hệ thống |
| `forecast(metric, horizon_hours)` | "dự báo chiều nay/ngày mai" |

**Phép thử "500 câu hỏi tương lai":** nếu câu hỏi mới buộc phải viết tool mới → kiến trúc thất bại.
Ở đây, câu hỏi mới chỉ cần AI **ghép** các tool có sẵn.

---

## 2. Điểm mở rộng DUY NHẤT: `domain/metric_registry.py`

Thêm một đại lượng đo mới (bức xạ, độ ẩm, SOC pin, gió...) = **thêm 1 dòng `MetricSpec`**.
Không sửa Service, không sửa Tool, không sửa Adapter. Đây là nguyên tắc **Open/Closed**.

```python
# Ví dụ: mai này có cảm biến bức xạ, chỉ thêm vào _METRICS:
'irradiance': MetricSpec(
    key='irradiance', label='Bức xạ', unit='W/m²',
    kind=MetricKind.INSTANTANEOUS,
    raw_model='weather.data', raw_field='irradiance',
    summary_model='weather.data.summary', summary_field='irradiance_avg',
),
```

Ngay lập tức AI thấy `irradiance` qua `list_metrics` và truy vấn được qua `get_timeseries("irradiance", ...)`.

Logic **Hybrid** (PV → MPPT → Pin → GTI → Lưới) cũng nằm gọn ở đây: `output_power` đọc từ
GTI (hòa lưới), `pv_input` đọc từ MPPT (nạp pin). Các KPI dẫn xuất (`self_consumption_pct`,
`grid_dependency_pct`) khai báo bằng **công thức**, nên quy tắc "không đếm trùng" định nghĩa đúng một lần.

---

## 3. Các tầng (mỗi tầng một trách nhiệm)

```
domain/         Không phụ thuộc Odoo — test chạy độc lập
  enums.py            Từ vựng chung: Granularity, AggregationType, MetricKind...
  value_objects.py    TimeRange (tự đổi UTC+7 → UTC), DataPoint
  metric_registry.py  ★ Danh mục metric — nguồn sự thật duy nhất
  dto.py              Đối tượng kết quả có to_dict() → JSON sạch

repositories/   Tầng DUY NHẤT chạm ORM/SQL
  metric_repository.py  1 cỗ máy truy vấn cho MỌI metric; tự chọn raw vs summary
  device_repository.py  trạng thái thiết bị, offline_minutes
  alarm_repository.py   suy ra cảnh báo (chưa có model alarm riêng)

services/       Business logic thuần — nhận tham số có kiểu, trả DTO
  analytics_service.py  timeseries / aggregate / compare
  anomaly_service.py    zscore / iqr / threshold
  health_service.py     điểm tổng hợp có trọng số
  forecast_service.py   dự báo mùa vụ ngây thơ
  device_service.py     trạng thái + cảnh báo

tools/          Lớp bọc MỎNG, ổn định — không có business logic
  base_tool.py    Tool ABC + phong bì {ok, data, meta, error}
  solar_tools.py  9 tool
  registry.py     ToolRegistry — điểm vào cho mọi adapter

adapters/       Dịch giao thức (đọc chung từ ToolRegistry.specs())
  openai_adapter.py   sinh mảng tools + điều phối tool_call
  mcp_adapter.py      tools/list + tools/call

controllers/    REST/JSON-RPC — Web/Mobile cũng dùng chung tool
```

**Luồng phụ thuộc:** trên gọi xuống dưới, không có chiều ngược.
`Tool → Service → Repository → ORM`. Đổi cách lưu chỉ sửa Repository; đổi LLM chỉ sửa Adapter.

---

## 4. Chuẩn JSON trả về (phong bì)

Mọi tool trả về cấu trúc thống nhất — **chỉ số và chuỗi, không markdown/HTML/câu chữ**.
LLM đọc cái này rồi tự viết báo cáo.

```json
{
  "ok": true,
  "data": { "metric": "output_power", "unit": "W", "avg": 1240.5, "max": 2410 },
  "meta": { "tool": "get_aggregate", "generated_at": "2026-07-02T15:00:00+07:00" },
  "error": null
}
```

Lỗi được phân loại: `unknown_metric`, `unknown_tool`, `bad_request`, `internal_error`.
Tool không bao giờ ném ngoại lệ ra ngoài — luôn trả JSON hợp lệ.

---

## 5. Múi giờ

AI luôn nói giờ Việt Nam (UTC+7); Odoo lưu UTC naive. `TimeRange.from_iso()` nhận chuỗi ISO
UTC+7, đổi sang UTC để truy vấn; khi trả kết quả thì đổi ngược lại UTC+7. Toàn bộ quy tắc gói
trong `domain/value_objects.py`.

---

## 6. Cách gọi

**Python trực tiếp:**
```python
from odoo.addons.smartsolar_ai.tools.registry import ToolRegistry
reg = ToolRegistry(env)
reg.execute('get_aggregate', {
    'metrics': ['output_power', 'bat_voltage'],
    'start': '2026-07-02T00:00:00', 'end': '2026-07-02T23:59:59',
})
```

**Lấy spec cho LLM:**
```python
from odoo.addons.smartsolar_ai.adapters.openai_adapter import OpenAIAdapter
OpenAIAdapter(reg).tool_specs()   # → tools=[...] cho chat API
```

**REST:**
```
POST /solar/ai/tools                 → khám phá tool
POST /solar/ai/tool/get_timeseries   → chạy 1 tool
POST /solar/ai/mcp {method, params}  → MCP
```

---

## 7. Chỗ cần hoàn thiện (placeholder)

Ba mapping trong `services/analytics_service.py` (`_ENERGY_SOURCES`) đang trỏ **tạm** vì DB
chưa có cảm biến tương ứng:
- `grid_import_energy` — chưa có công-tơ lấy lưới dạng kWh (chỉ có `limiter_power` tức thời)
- `load_energy` — chưa có đo tải riêng

Khi bổ sung cảm biến: thêm `MetricSpec` + sửa mapping → các KPI dẫn xuất tự đúng.

---

## 8. Chạy test

```bash
odoo -i smartsolar_ai --test-enable --test-tags smartsolar_ai
```

Test domain chạy không cần DB; test tool/service dùng `TransactionCase`.
