# Smart Solar AI Chat

Module Odoo 19 đưa trợ lý SmartSolar AI vào Discuss. Khi người dùng nhắn trong
kênh có bot, planner gọi LLM, thực thi các tool của `smartsolar_ai`, rồi tổng hợp
JSON thành câu trả lời tiếng Việt. Tin nhắn placeholder được cập nhật theo thời
gian thực trong lúc planner chạy nền.

## Luồng xử lý

```text
Discuss → SmartSolar AI Agent → Provider (Ollama/OpenAI-compatible)
        → ToolRegistry → Service → Repository → PostgreSQL
        ← JSON có trạng thái availability ←─────────────────────┘
```

Module hỗ trợ:

- Hội thoại nhiều lượt với số tin nhớ có thể cấu hình.
- Tool calling qua planner loop.
- Cập nhật tiến trình vào cùng một bong bóng chat.
- Thống kê token/tốc độ cho provider có cung cấp usage.
- Phân tích tối đa 4 ảnh, mỗi ảnh khoảng 5 MB, nếu model hỗ trợ vision.
- Chạy nền bằng cursor/transaction riêng sau khi tin nhắn người dùng đã commit.

## Provider

| Provider | Base URL mặc định | API key |
|---|---|---|
| Ollama | `http://localhost:11434` | Không |
| LM Studio | `http://localhost:1234/v1` | Thường không |
| OpenAI | `https://api.openai.com/v1` | Có |
| NVIDIA Build API | `https://integrate.api.nvidia.com/v1` | Có |
| OpenRouter | `https://openrouter.ai/api/v1` | Có |

LM Studio, OpenAI, NVIDIA và OpenRouter dùng chung adapter OpenAI-compatible.
Model được chọn phải hỗ trợ tool/function calling; model chỉ sinh text sẽ bị
agent dừng an toàn đối với câu hỏi cần dữ liệu vận hành.

## Cài đặt

`smartsolar_ai_chat` phụ thuộc `smartsolar_ai` và module Odoo `mail`:

```bash
pip install requests
odoo-bin -d <database> --stop-after-init -i smartsolar_ai,smartsolar_ai_chat
```

Sau khi cập nhật code:

```bash
odoo-bin -d <database> --stop-after-init -u smartsolar_ai,smartsolar_ai_chat
```

## Cấu hình khuyến nghị cho Ollama 20B

Vào **Settings → Smart Solar AI**:

| Trường | Khuyến nghị | Ý nghĩa |
|---|---:|---|
| Provider | `Ollama` | Chạy local |
| Base URL | để trống hoặc `http://localhost:11434` | Endpoint Ollama |
| Model | `gpt-oss:20b` | Mặc định cho cài mới |
| Temperature | `0.1` | Ổn định chọn tool/tham số |
| Max output tokens | `1000` | Giới hạn mỗi lượt sinh |
| Context window | `32768` | Được truyền thành Ollama `num_ctx` |
| Max tool iterations | `5` | Giới hạn planner loop; runtime giữ trong khoảng 2–10 |
| History limit | `6` | Số tin gần nhất; 0 để tắt nhớ |

```bash
ollama pull gpt-oss:20b
ollama serve
```

Nếu database từng cài phiên bản cũ, giá trị `smartsolar_ai.model` hiện hữu sẽ
không tự đổi vì dữ liệu cấu hình dùng `noupdate`. Hãy chọn lại model trong Settings.

System Prompt trong Settings là phần **bổ sung**. Quy tắc mặc định về gọi tool,
không bịa số và xử lý dữ liệu thiếu luôn được giữ lại.

## Cách sử dụng trong Discuss

1. Mở Discuss và tạo hội thoại/kênh có thành viên **SmartSolar AI**.
2. Hỏi một câu cần dữ liệu, ví dụ: `Công suất hiện tại bao nhiêu?`.
3. Bot tạo trạng thái “Đang phân tích”, gọi tool và cập nhật câu trả lời cuối.

Một số câu hỏi phù hợp:

- `Báo cáo sản lượng hôm nay và so với hôm qua.`
- `Thiết bị nào đang offline?`
- `Nhiệt độ inverter tuần này có bất thường không?`
- `Dự báo công suất 6 giờ tới.`

“Sản lượng” là điện năng kWh, còn “công suất” là W. Forecast hiện chỉ hỗ trợ
metric tức thời; không đánh tráo dự báo công suất thành dự báo sản lượng.

## Cơ chế chống kết luận sai

- Prompt runtime chứa thời gian UTC+7 và catalog metric mới nhất.
- Câu hỏi vận hành mà model chưa gọi tool sẽ được nhắc gọi lại một lần.
- Tool trả lỗi không được coi là dữ liệu hợp lệ.
- Nếu vẫn không có tool thành công, agent trả thông báo an toàn thay vì dùng nội
  dung số do model tự sinh.
- Lời gọi tool cùng tên và cùng tham số được cache trong một lượt chat.
- Kết quả `supported=false`, `available=false`, `value=null` hoặc `score=null`
  không được diễn giải thành số 0.
- Payload chứa ảnh/kết quả tool chỉ log ở DEBUG và ảnh luôn được che base64.

## Test

```bash
odoo-bin -d <test_database> --test-enable --stop-after-init \
  -i smartsolar_ai,smartsolar_ai_chat \
  --test-tags smartsolar_ai_chat
```

Regression tests bao phủ parser tool-call fallback, option Ollama, prompt an toàn,
retry khi model bỏ tool, cache lời gọi trùng và fail-closed khi tool trả lỗi.

## Xử lý sự cố

- **Bot không xuất hiện:** kiểm tra module đã upgrade và user `SmartSolar AI` đang active.
- **Bot không trả lời:** kiểm tra log Odoo, provider/base URL và khả năng tool calling của model.
- **Ollama timeout:** thử giảm history/context hoặc kiểm tra tài nguyên RAM/VRAM.
- **Model trả “chưa gọi được tool”:** model không sinh tool-call hợp lệ; đổi model
  có native tool calling hoặc kiểm tra schema/endpoint provider.
- **Không có số liệu:** đọc `available`, `reason`, `count` và `supported`; không
  sửa prompt để ép model tạo số thay thế.

Xem thêm [AI Tool Layer](../smartsolar_ai/README.md) và
[kiến trúc toàn repo](../ARCHITECTURE.md).
