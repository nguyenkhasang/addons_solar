# addons_solar — Giám sát điện mặt trời trên Odoo 19

Bộ addon Odoo 19 để thu thập, tổng hợp và hiển thị dữ liệu điện mặt trời; đồng
thời cung cấp Tool Layer và trợ lý AI trong Discuss. LLM không truy cập trực tiếp
database và không tự sinh SQL: mọi số liệu phải đi qua các tool có schema rõ ràng.

```text
Thiết bị/MQSolar → WebSocket → Odoo/PostgreSQL → Dashboard
                                      └────────→ AI Tool Layer → LLM → Discuss
Open-Meteo ────────────────────────────┘
```

## Các module

| Module | Vai trò | Phụ thuộc chính |
|---|---|---|
| `smartsolar` | Model hệ thống/thiết bị, dữ liệu MPPT và GTI, WebSocket, summary và cron dọn dữ liệu | `websocket-client` |
| `smartsolar_environment` | Thu thập và tổng hợp dữ liệu thời tiết từ Open-Meteo | `requests` |
| `smartsolar_dashboard` | Dashboard Owl, KPI, biểu đồ và trạng thái realtime qua `bus.bus` | `smartsolar` |
| `smartsolar_ai` | 9 tool analytics dùng chung qua Python, REST, OpenAI function calling và MCP | `smartsolar` |
| `smartsolar_ai_chat` | Planner loop và bot SmartSolar AI trong Discuss | `requests`, `smartsolar_ai`, `mail` |

`web_responsive` là addon giao diện độc lập được đặt cùng repo.

## Yêu cầu

- Odoo 19 và PostgreSQL theo yêu cầu của Odoo.
- Python package `websocket-client` và `requests` trong đúng môi trường chạy Odoo.
- Nếu dùng AI local: Ollama hoặc LM Studio và một model có native tool calling.

```bash
pip install websocket-client requests
```

## Cài đặt và nâng cấp

Thêm thư mục repo vào `addons_path`, sau đó chạy:

```bash
odoo-bin -d <database> --stop-after-init \
  -i smartsolar,smartsolar_environment,smartsolar_dashboard,smartsolar_ai,smartsolar_ai_chat
```

Nâng cấp sau khi pull code mới:

```bash
odoo-bin -d <database> --stop-after-init \
  -u smartsolar,smartsolar_environment,smartsolar_dashboard,smartsolar_ai,smartsolar_ai_chat
```

Tên executable có thể là `odoo` thay cho `odoo-bin`, tùy cách cài đặt.

## Cấu hình AI local 20B

Vào **Settings → Smart Solar AI** và bắt đầu với:

| Trường | Giá trị khuyến nghị |
|---|---|
| Provider | `Ollama` |
| Base URL | `http://localhost:11434` |
| Model | `gpt-oss:20b` hoặc model 20B có native tool calling |
| Temperature | `0.1` |
| Max output tokens | `1000` |
| Context window | `32768` |
| Max tool iterations | `5` |
| History limit | `6` |

Ví dụ chuẩn bị model mặc định với Ollama:

```bash
ollama pull gpt-oss:20b
ollama serve
```

Với database đã tồn tại, `ir.config_parameter` giữ giá trị model cũ khi nâng
module. Hãy kiểm tra lại trường **Model** sau khi upgrade.

## Quy tắc độ tin cậy của số liệu AI

- `list_metrics` công bố `supported`, `note`, loại metric và cách gộp mặc định.
- `available=false` hoặc `value=null` nghĩa là thiếu cảm biến/dữ liệu, không phải 0.
- `grid_dependency_pct` chưa khả dụng cho tới khi có công-tơ lấy lưới và tải riêng.
- Tổng kWh dùng counter qua `get_aggregate`; không cộng trực tiếp mẫu công suất W.
- Timeseries mặc định tối đa 240 điểm và trả `truncated/original_count` khi được lấy mẫu.
- Forecast, anomaly và health score không tạo số 0 thay thế khi lịch sử không đủ.
- Planner chỉ chấp nhận tool trả `ok=true`, retry một lần khi model bỏ qua hoặc gọi
  tool lỗi, đồng thời cache lời gọi trùng lặp trong cùng câu hỏi.

## Chạy test

```bash
odoo-bin -d <test_database> --test-enable --stop-after-init \
  -i smartsolar_ai,smartsolar_ai_chat \
  --test-tags smartsolar_ai,smartsolar_ai_chat
```

## Tài liệu

- [Kiến trúc toàn hệ thống](ARCHITECTURE.md)
- [AI Tool Layer](smartsolar_ai/README.md)
- [AI Chat trong Discuss](smartsolar_ai_chat/README.md)

## License

Các module Smart Solar trong repo dùng giấy phép LGPL-3 theo manifest tương ứng.
