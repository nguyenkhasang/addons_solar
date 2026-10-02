# Smart Solar AI Tools — Tầng Python Tool Layer cho AI Agent

Tầng Tool (Function/Tool Calling) để AI đọc dữ liệu điện mặt trời và tự viết báo cáo.
**AI KHÔNG chạm database, KHÔNG sinh SQL — chỉ gọi các Tool đã định nghĩa.**

Tài liệu tổng quan repo nằm tại [README root](../README.md); cách cấu hình và dùng
bot Discuss nằm tại [smartsolar_ai_chat/README.md](../smartsolar_ai_chat/README.md).

```
User → AI Planner (Ollama/LM Studio/OpenAI) → Tool Layer
     → Business Service → Repository → Odoo ORM → PostgreSQL → JSON → AI → Báo cáo
```

---

## 1. Nguyên tắc cốt lõi: Tool theo NĂNG LỰC, không theo câu hỏi

Sai (không mở rộng được): `today_report()`, `compare_week()`, `get_battery()`...
Mỗi câu hỏi mới lại phải viết hàm mới.

Đúng (dùng cho vô số câu hỏi): 12 tool tổng quát, **metric là tham số**.

| Tool | Trả lời lớp câu hỏi |
|---|---|
| `get_system_context(system_id)` | cấu hình công khai, công suất định mức, nhóm thông số và giới hạn cảm biến |
| `get_snapshot(metrics[], window_minutes)` | nhiều số đo gần nhất, thời điểm mẫu, tuổi dữ liệu và trạng thái thiết bị |
| `get_metric_trends(metrics[], start, end)` | thống kê và chuỗi thời gian của nhiều metric để đối chiếu diễn biến |
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

Logic **Hybrid** (PV → MPPT → Pin → GTI → tải) cũng nằm gọn ở đây: `pv_input` là điện PV
thu tại MPPT để nạp pin; `output_power` là điện pin/DC qua GTI cấp tải, không phải PV thu
cùng thời điểm. Các KPI dẫn xuất (`self_consumption_pct`,
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
  solar_tools.py  12 tool
  registry.py     ToolRegistry — điểm vào cho mọi adapter

adapters/       Dịch giao thức (đọc chung từ ToolRegistry.specs())
  openai_adapter.py   sinh mảng tools + điều phối tool_call
  mcp_adapter.py      tools/list + tools/call

controllers/    REST/JSON-RPC — Web/Mobile cũng dùng chung tool
```

**Luồng phụ thuộc:** trên gọi xuống dưới, không có chiều ngược.
`Tool → Service → Repository → ORM`. Đổi cách lưu chỉ sửa Repository; đổi LLM chỉ sửa Adapter.

---

## 4. Chuẩn JSON trả về (phong bì và availability)

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

Kết quả có dữ liệu đo sử dụng thêm hợp đồng trạng thái:

| Trường | Ý nghĩa |
|---|---|
| `available` | Có đủ dữ liệu để kết luận hay không |
| `reason` | Lý do không khả dụng; model phải trình bày thay vì tạo số thay thế |
| `count` / `sample_count` | Số mẫu thực sự dùng |
| `original_count` | Số điểm trước khi giới hạn context |
| `truncated` | Danh sách/chuỗi đã bị rút gọn |

`ok=true` chỉ có nghĩa tool chạy đúng. Dữ liệu vẫn có thể trả `available=false`
nếu khoảng thời gian rỗng hoặc thiếu cảm biến. `value=null`/`score=null` không phải 0.

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

## 7. Metric chưa đủ cảm biến

`grid_dependency_pct` hiện có `supported=false`: hai counter đã xác định được chiều nhưng
`limiter_total` chưa có summary dài hạn. Tool trả `value=null`, `available=false` thay vì
so sánh hai nguồn có cửa sổ dữ liệu không đồng nhất.

Các metric và giới hạn liên quan:

- `grid_import_energy_total` đọc `energy_total` và dùng được summary `energy_total_end`.
- `energy_exported_total` giữ key cũ để tương thích nhưng đọc `limiter_total`; metric này
  chỉ truy vấn được trong thời gian còn dữ liệu raw vì chưa có cột summary tương ứng.
- `grid_export_energy` không có công-tơ thật; `self_consumption_pct` phụ thuộc biến này nên
  cũng trả `available=false`, không ánh xạ sang counter khác làm placeholder.
- `MetricSpec.flow` phân biệt rõ điện inverter phát ra với điện lấy từ lưới;
  `MetricSpec.unreliable` công bố các giả định chưa được xác minh cho LLM.

Chỉ chuyển `grid_dependency_pct` sang `supported=true` sau khi bổ sung summary cho
`limiter_total`, để hai nhánh luôn được so sánh trên cùng khoảng dữ liệu.

---

## 8. Chạy test

```bash
odoo-bin -d <test_database> --test-enable --stop-after-init \
  -i smartsolar_ai --test-tags smartsolar_ai
```

Test domain chạy không cần DB; test tool/service dùng `TransactionCase`.

### Lưu ý cho LLM local

- `get_timeseries` mặc định tối đa 240 điểm; AUTO dùng bucket giờ khi khoảng dài
  hơn 6 giờ. Kết quả có `original_count` và `truncated`.
- `forecast`, `find_anomalies`, `get_health_score` trả `available=false` và
  `reason` khi không đủ dữ liệu; không diễn giải `value=null`/`score=null` thành 0.
- Với Ollama nên dùng context 16K–32K, temperature 0.0–0.2 và model có native
  tool calling.

## 9. Quy tắc aggregation

- Metric tức thời (W, V, A, °C): dùng `avg` cho mức điển hình, `max` cho đỉnh.
- Counter kWh: dùng `get_aggregate`; service tính năng lượng trong khoảng từ
  summary hoặc chênh lệch counter. Không dùng `sum` trên timeseries counter.
- Không cộng mẫu công suất W để tuyên bố đó là kWh nếu chưa tích phân theo thời gian.
- Counter toàn hệ thống được tính first/last riêng từng thiết bị rồi mới cộng,
  tránh trộn counter của hai thiết bị.

## 10. Giới hạn đầu ra dành cho LLM

- `get_timeseries`: mặc định 240, tối đa 500 điểm.
- `get_aggregate` và `compare_periods`: tối đa 10 metric mỗi lượt.
- `get_device_status`: mặc định 100, tối đa 200 thiết bị.
- `get_alarms`: mặc định 50, tối đa 200 cảnh báo.
- Mọi schema tool có `additionalProperties=false`; tham số lạ trả `bad_request`.

## 11. Ngữ cảnh và chất lượng dữ liệu cho phân tích tự chủ

AI tự chọn metric và ghép các tool theo bằng chứng cần cho câu hỏi. Không có
template báo cáo cố định trong Python và không tự lấy mọi metric cho mọi câu hỏi.

`get_system_context` chỉ đọc whitelist: tên/mã, vị trí, múi giờ, công suất cấu hình,
ngày lắp đặt và trạng thái. Token MQSolar, mật khẩu và cấu hình kết nối không được trả.
Cấu hình này không phải số đo; agent vẫn cần tool dữ liệu để báo cáo vận hành.

`get_aggregate` trả thêm `label`, `kind`, `energy_available` cho counter và `quality`:

- Thời điểm mẫu đầu/cuối; tuổi mẫu và khoảng trống ở hai mép thời gian.
- Nguồn raw/summary, độ phân giải, ý nghĩa timestamp (summary là đầu bucket).
- Cảnh báo công-tơ đứng yên và điện năng bằng 0 dù có công suất cùng khoảng.

Đây là dấu hiệu cần kiểm tra, không phải phép tích phân công suất thành điện năng.
Tuổi mẫu và khoảng trống không chứng minh số mẫu phủ đủ khoảng; chưa tính tỷ lệ
coverage lịch sử theo lịch lấy mẫu. `last` là mẫu cuối, không luôn là giá trị hiện tại.

`get_snapshot` mặc định lấy 7 metric điện/pin/nhiệt trong 10 phút, có thể chọn tối
đa 10 metric và mở cửa sổ 1–1440 phút; phải công bố mẫu cũ khi mở rộng cửa sổ.
`get_metric_trends` lấy 1–6 metric, mặc định 60, tối đa 120 điểm mỗi chuỗi. Chuỗi
truncated/bucket và thời tiết theo ngày không dùng để khẳng định mọi mẫu đều bình thường.

Health trả `assessment=partial` và `missing_components` khi chỉ có một phần dữ liệu.
Điểm 100 với coverage 60% không chứng minh toàn hệ thống khỏe. Thành phần availability
phản ánh online hiện tại, không phải uptime của khoảng lịch sử.

Cảnh báo trạng thái chuẩn hóa cả `0` và `0.0`. Mã chưa có tài liệu giải nghĩa được
trả là sự kiện `info`, `interpretation=unknown`, `confirmed_fault=false`. Không tự
ánh xạ mã sạc thành lỗi hay gán ý nghĩa firmware chưa xác minh. Offline là trạng
thái hiện tại; tool không có lịch sử sự cố phần cứng đầy đủ.

### Điện năng từ công suất khi công-tơ không cập nhật

`get_aggregate` với `total_load_energy` tích phân công suất tổng tải
`output_power + limiter_power` theo từng thiết bị, trả kWh trong `value`.
Các metric `output_power`, `grid_import_power`, `pv_input` trả thêm
`energy_estimate` theo cùng phương pháp. Đây là ước tính từ mẫu công suất,
không phải chỉ số công-tơ hay tổng chính xác cả ngày.

Dùng hình thang giữa hai mẫu cách nhau tối đa 300 giây, cắt đoạn tại biên
khoảng yêu cầu; không ngoại suy hoặc nối qua khoảng mất dữ liệu. Không có
đoạn hợp lệ thì `value=null`, `available=false`, không trả 0 giả.
`coverage_pct` và `per_device` mô tả thời gian thực sự được tích phân trên
thiết bị có dữ liệu; không xác nhận đủ mọi thiết bị cấu hình. Chỉ hỗ trợ raw
với khoảng truy vấn tối đa 31 ngày; dữ liệu cũ đã dọn không được khôi phục từ
công suất trung bình. Không lấy kWh chia cho độ phủ để tự đoán tổng toàn kỳ.
