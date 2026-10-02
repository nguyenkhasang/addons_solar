# Smart Solar AI Chat

Module Odoo 19 đưa trợ lý SmartSolar AI vào Discuss. Khi người dùng nhắn trong
kênh có bot, planner gọi LLM, thực thi các tool của `smartsolar_ai`, rồi tổng hợp
JSON thành câu trả lời tiếng Việt. Tin nhắn placeholder được cập nhật theo thời
gian thực trong lúc planner chạy nền.

## Luồng xử lý

```text
Discuss → SmartSolar AI Agent → Provider (Codex CLI/Ollama/OpenAI-compatible)
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
| Codex CLI | Không dùng HTTP endpoint | Đăng nhập CLI bằng ChatGPT |
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

## Dùng Codex CLI thay Ollama

1. Cài Codex CLI trên **máy chạy Odoo** và chạy `codex login` bằng cùng tài khoản
   Linux chạy service Odoo. Kiểm tra bằng `codex login status`.
2. Upgrade module `smartsolar_ai_chat`, rồi vào **Settings → Smart Solar AI**.
3. Chọn **Codex CLI (ChatGPT)**. Nhập đường dẫn tuyệt đối tới CLI nếu service
   không có `codex` trong PATH (`command -v codex` để tìm đường dẫn).
4. Nhập **Model Codex** phù hợp với tài khoản; để trống dùng mặc định của CLI.
   Tên model Ollama cũ không được dùng cho Codex. Base URL/API Key không cần.
5. Thời gian chờ mặc định là 120 giây **mỗi lượt**; một báo cáo có thể cần nhiều
   lượt lấy dữ liệu. Lưu và thử `Báo cáo sản lượng hôm nay và so với hôm qua`.

CLI phải hỗ trợ `exec --ignore-user-config --ignore-rules --ephemeral
--output-schema` (đã kiểm tra với 0.159.2/0.159.3). Cài CLI độc lập cho service;
đường dẫn trong `.vscode/extensions/...` thay đổi khi extension cập nhật. Odoo
không cần VS Code đang mở.

Ví dụ cài riêng trong thư mục dự án Odoo (chạy tại `odoo-19.0`):

```bash
npm install --prefix .odoo-runtime/codex-cli --no-audit --no-fund @openai/codex@0.159.3
./.odoo-runtime/codex-cli/node_modules/.bin/codex login status
```

Nếu chưa đăng nhập, dùng cùng lệnh với `login` thay cho `login status`. Đặt trường
**Đường dẫn Codex CLI** thành đường dẫn tuyệt đối đến
`.odoo-runtime/codex-cli/node_modules/.bin/codex` trong dự án Odoo. Cài qua npm
cần Node.js có trong PATH của service; xác thực CLI nằm ở tài khoản Linux chạy
Odoo, không phụ thuộc vòng đời VS Code. Tham khảo [OpenAI Docs](https://learn.chatgpt.com/docs/codex/cli).

Provider gửi lịch sử hội thoại và schema tool qua stdin, dùng output schema JSON
để đổi yêu cầu của Codex thành `ToolCall`. Odoo thực thi qua registry hiện có rồi
gửi kết quả lại. Codex chạy trong thư mục tạm riêng, read-only, tắt shell, web
search, apps, hooks và multi-agent; bỏ cấu hình cá nhân và không lưu phiên chat
CLI. Ảnh được truyền bằng `--image`; file tạm được xóa sau mỗi lượt, kể cả khi lỗi.
Không gọi SQL trực tiếp từ Codex. Cấu hình và kiểm tra dữ liệu của planner vẫn áp dụng.

**Dữ liệu hội thoại và kết quả truy vấn Solar được gửi tới dịch vụ OpenAI** để
Codex suy luận; đây không phải suy luận local như Ollama. CLI dùng xác thực đã
lưu của tài khoản Linux chạy Odoo (hoặc `CODEX_HOME` của service), không sao chép
token vào cấu hình Odoo. Cần Internet và tài khoản có quyền sử dụng model/hạn mức
đủ. Không tự chuyển về Ollama khi Codex lỗi; lỗi được trả về chat.

Tham khảo [tài liệu OpenAI về codex exec](https://learn.chatgpt.com/docs/non-interactive-mode).

## Cấu hình kết nối Ollama

Vào **Settings → Smart Solar AI**:

| Trường | Khuyến nghị | Ý nghĩa |
|---|---:|---|
| Provider | `Ollama` | Chạy local |
| Base URL | để trống hoặc `http://localhost:11434` | Endpoint Ollama |
| Model | `gpt-oss:20b` | Mặc định cho cài mới |
| Max tool iterations | `8` | Ngân sách planner loop; runtime giữ trong khoảng 2–12 |
| History limit | `6` | Số tin gần nhất; 0 để tắt nhớ |

Module không gửi `temperature`, `num_predict`/`max_tokens` hay `num_ctx` trong
yêu cầu chat. Các tham số suy luận này do model server/provider tự quyết định.

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

## Phân tích tự chủ và độ sâu báo cáo

Prompt cho phép AI chọn thêm thông số liên quan, kiểm tra giả thuyết và viết báo
cáo có nhận định, so sánh, giới hạn dữ liệu và đề xuất kiểm tra theo ưu tiên.
Không còn giới hạn cứng 1–3 nhận định. Câu hỏi hẹp vẫn trả lời trực tiếp; độ dài
phụ thuộc nhu cầu, không buộc mọi câu hỏi vào một mẫu.

Các tool ngữ cảnh, snapshot và xu hướng nhiều metric giúp AI lấy đủ bằng chứng
với ít lượt hơn. `list_metrics` và `get_system_context` không tính là dữ liệu đo
thành công. Mỗi vòng tối đa 12 tool; khi hết ngân sách, AI tổng hợp bằng chứng đã
có và nêu phần chưa xác minh. Model đang cấu hình vẫn được giữ nguyên.

Đối với database cũ, `noupdate` giữ ngân sách đã lưu; đổi **Max Tool Iterations**
sang 8 nếu muốn tăng không gian phân tích. Tăng số vòng có thể tăng thời gian
và token. AI vẫn không được sinh SQL, thay đổi hệ thống hoặc bịa thông số.

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

## Giảm token đầu vào

Bridge Codex dùng `model_instructions_file` trong thư mục tạm của từng lượt
để thay hướng dẫn lập trình mặc định bằng giao thức planner Solar. Không sửa
cấu hình Codex cá nhân, không lưu phiên chat chung giữa người dùng. Giữ sandbox
read-only, tắt shell/web/apps/hooks và chỉ thực thi tool qua registry Odoo.

System prompt giữ chiều dòng điện, kWh từ tích phân công suất, độ phủ, dữ liệu
không khả dụng, mẫu cũ và giới hạn pin. Catalog mặc định chỉ gửi key/nhãn/đơn
vị/loại/cờ hỗ trợ; `list_metrics` vẫn cung cấp toàn bộ mô tả khi cần. Câu hỏi cần số liệu dùng schema bắt buộc chọn tool đến khi có bằng chứng hợp lệ,
tránh một lượt trả lời sớm rồi retry. Cả 12 tool
vẫn khả dụng; không chọn một tập tool cố định theo từ khóa câu hỏi.

Payload Codex dùng JSON gọn, bỏ mô tả lặp của tham số chung (thời gian, phạm
vi, metric) nhưng giữ nguyên required/type/enum/limits. Kết quả tool giữ nguyên
số liệu, null, lỗi, đơn vị, khoảng thời gian, độ phủ và cảnh báo; chỉ bỏ metadata
lặp đã có trong system prompt. Các provider khác vẫn nhận schema nguyên bản.

Lịch sử văn bản tối đa 8.000 ký tự, tối đa 3.000 ký tự/tin, ưu tiên tin mới;
phần cắt có dấu và yêu cầu kiểm chứng lại bằng tool. Không cắt câu hỏi hiện tại
hoặc bằng chứng tool trong lượt hiện tại. Bỏ khối tiến trình/thống kê trước khi
gửi lại lịch sử, kể cả caller trực tiếp không đi qua Discuss.

Đo kiểm cùng `gpt-6-luna`, lịch sử rỗng, câu hỏi tổng tải 02/10/2026
00:00–23:02, hai lượt model:

| Phiên bản | Token đầu vào cộng dồn |
|---|---:|
| Trước tối ưu | 56.380 |
| Hướng dẫn CLI/catalog/schema gọn | 23.548 |
| Thêm system prompt gọn | 21.763 |
| Bản cuối, bắt buộc tool trước số liệu (đo lại) | 32.836 |

Usage CLI dao động giữa lần chạy dù payload tương tự: lần cuối giảm khoảng
42%, lần trước đạt 61%. Không cam kết tỷ lệ cho mọi câu hỏi. Các lần trả 6,8039 kWh với độ phủ 65,74%, không suy thành tổng toàn ngày.
Số liệu là usage CLI, không phải ước lượng từ số ký tự hay bảng giá.

Thống kê cộng dồn mọi lượt, hiện thêm số lượt gọi model và cached input tokens
(nằm trong input tokens, không cộng lần nữa hoặc tự suy ra chi phí). Log chỉ
lưu số ký tự, số message/tool và usage từng lượt, không log toàn prompt.

Cấu hình chính thức: https://learn.chatgpt.com/docs/config-file/config-reference

Kiểm tra lịch sử dài: giảm 82.623 xuống 47.559 input tokens và 3 xuống 2 lượt
model; công suất trả từ field last, có thời điểm/tuổi mẫu. Báo cáo tổng quan
vẫn phân tích đủ tải/PV/pin/nhiệt/cảnh báo/độ phủ trong 2 lượt. 86 kiểm thử của
hai module AI đạt trên database sao chép tạm, không đăng tin thử vào Discuss.
