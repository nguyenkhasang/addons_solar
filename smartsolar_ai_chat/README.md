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
tránh một lượt trả lời sớm rồi retry. Cả 13 tool
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

### Khắc phục câu hỏi đỉnh tải tốn 250k token

Log ngày 03/10/2026 cho thấy câu hỏi "ngày hôm qua thời điểm nào tổng tải lên
cao nhất" dùng 5 lượt model: trends theo giờ, trends raw với max_points=500
(bị từ chối vì tool chỉ hỗ trợ 120), trends 120 điểm, rồi hai timeseries raw
500 điểm. Payload lượt cuối lên 88.172 ký tự; riêng lượt đó 140.192 input tokens.
Chuỗi bị rút gọn còn làm mất mẫu đỉnh nên câu trả lời chỉ ra 2.317 W lúc 23:16.

Bổ sung `get_extrema` và `total_load_power`, hướng dẫn dùng truy vấn trực tiếp
cực trị thay vì drill-down chuỗi. Các chuỗi thực sự cần phân tích được mã hóa
thành hàng `[t,v]` kèm `point_columns`, giữ mọi timestamp/giá trị/null và cờ
truncated, giảm key JSON lặp; không cắt thêm bằng chứng.

Đo lại đúng câu hỏi, cùng gpt-6-luna và đúng lịch sử gốc (1 tin, 270 ký tự):

| Chỉ số | Log trước sửa | Đo sau sửa |
|---|---:|---:|
| Lượt gọi model | 5 | 2 |
| Token đầu vào cộng dồn | 252.519 | 22.596 |
| Token đầu vào cache (nằm trong input) | 164.096 | 3.840 |
| Token đầu ra | 1.735 | 210 |

Giảm khoảng 91% input tokens trong phép đo cùng câu hỏi/lịch sử này. Lần đo
không có lịch sử trước đó dùng 22.377 input tokens. Token CLI có thể dao động
giữa lần chạy, nên không cam kết tỷ lệ cho mọi câu hỏi.
Nguyên nhân được xác nhận từ log là drill-down/đầu vào sai giới hạn/tích lũy
chuỗi lớn. Tool mới tìm 2.319 W lúc 23:19:04.823841 ngày 02/10/2026 UTC+7 trên
789 bản ghi raw; hai thành phần tại cùng mẫu là 0 W hòa lưới + 2.319 W điện lưới.
Không xác nhận dữ liệu phủ liên tục cả ngày hoặc đỉnh giữa các mẫu.

93 kiểm thử của hai module AI đạt sau bổ sung extrema và biểu diễn chuỗi gọn.

### Tối ưu tiếp: lấy dữ liệu trước và JSON không mã hóa kép

Các câu hỏi độc lập rõ nghĩa như “Tổng tải hôm nay/hôm qua tiêu thụ bao nhiêu
kWh?” và “Công suất điện lưới/hòa lưới/PV hiện tại bao nhiêu W?” được lấy dữ
liệu qua `get_aggregate` trước lượt model đầu tiên. Đây là bằng chứng tool,
không phải câu trả lời viết sẵn. AI vẫn nhận mọi tool từ registry và có thể
lấy thêm dữ liệu. Provider mặc định hiện tại không bị thay đổi.

Chỉ dùng đường nhanh khi không có lịch sử, có hệ thống mặc định người dùng
được phép xem và câu hỏi khớp mẫu rõ nghĩa. Câu hỏi có hệ thống riêng, ngày
cụ thể, so sánh, báo cáo hoặc chỉ dẫn khác dùng planner như trước. Tool lỗi
thì quay về planner, không dùng lỗi làm số liệu. Cơ chế dùng chung cho các
provider có bộ chuyển đổi assistant/tool message.

Trong payload CLI, content của kết quả tool là object JSON trực tiếp thay
vì chuỗi JSON bị escape lần nữa. Giữ toàn bộ số liệu/null/cảnh báo/phạm vi,
đồng thời giữ dạng bảng chuỗi thời gian có `point_columns` hiện có. Định dạng
message gửi các API/adapter khác không thay đổi.

Đo riêng Codex `gpt-6-luna` ngày 03/10/2026, giữ nguyên cấu hình provider của
Odoo, cùng câu hỏi “Tổng tải hôm qua tiêu thụ bao nhiêu kWh?”:

| Chế độ | Input tokens | Lượt model |
|---|---:|---:|
| Mô phỏng bản trước: không prefetch, JSON kép | 22.718 | 2 |
| Prefetch + JSON trực tiếp | 11.166 | 1 |

Giảm khoảng 51% so với bản trước trong phép đo này, không phải cam kết cho
mọi câu hỏi. Cả hai trả 8,3873 kWh với độ phủ 67,12%, không ngoại suy thời gian
thiếu dữ liệu. Câu hỏi phức tạp vẫn cần nhiều lượt; lịch sử vẫn có giới hạn
để giữ đúng hệ thống/phạm vi được chọn trong hội thoại.

Vòng tối ưu này: 108 kiểm thử của hai module AI đạt trên database sao chép,
bao gồm giữ toàn bộ tool, không đoán scope từ lịch sử, fallback khi prefetch lỗi
và bảo toàn dữ liệu/cảnh báo khi bỏ JSON kép.

### Kiểm tra mẫu “báo cáo hệ thông hôm nay”

Báo cáo hôm nay độc lập, không có lịch sử hoặc phạm vi riêng, được lấy trước
`get_aggregate`, `get_device_status`, `get_alarms`, `get_health_score` và
`get_snapshot` (pin/nhiệt). Chấp nhận cả “hệ thông” và “hệ thống”. AI vẫn có
thể gọi thêm tool; trường hợp có lịch sử, hệ thống/ngày/giờ riêng hoặc câu hỏi
phức tạp dùng planner bình thường. Lỗi trong kế hoạch báo cáo được gửi rõ là
lỗi, không xem là bằng chứng; truy vấn thành công được đưa vào cache lượt đó.

Thử riêng Codex `gpt-6-luna` ngày 03/10/2026, giữ provider Odoo nguyên trạng:

| Bản báo cáo | Input tokens | Lượt model | Chất lượng |
|---|---:|---:|---|
| Ban đầu | 25.393 | 2 | Thiếu pin/nhiệt/sức khỏe |
| Ràng buộc đủ nhóm | 42.430 | 2 | Đủ tổng quan cơ bản |
| Lấy trước 5 nhóm | 15.934 | 1 | Đủ tổng quan cơ bản |

Bản cuối giảm khoảng 62% so với bản đủ nhóm hai lượt và 37% so với bản thiếu
thông tin ban đầu. Token dao động, dữ liệu hôm nay cập nhật theo thời gian;
không phải benchmark giá tiền cố định. 110 kiểm thử AI đạt. Báo cáo cơ bản
không thay thế chẩn đoán sâu với xu hướng, so sánh cùng giờ và thời tiết.
Provider Odoo đang chọn Ollama; lần thử này timeout, không đổi provider/model.


### Bộ thử 29 câu thường gặp (04/10/2026)

Danh sách câu hỏi độc lập, cách nói tự nhiên, lỗi chính tả và câu nối tiếp nằm ở
`tests/fixtures/common_questions_vi.json`. Đây là bộ đo thực trên model, không tự
gọi dịch vụ bên ngoài trong unit tests. Khi đo, dùng database snapshot, cố định
mốc UTC+7 và lưu evidence/usage; câu nối tiếp dùng câu trả lời thật của câu cha.

Preset lấy dữ liệu mở rộng cho tổng tải/PV/pin/thiết bị/đỉnh tải/chẩn đoán và kỳ
ngày/tuần/tháng rõ nghĩa. Scope/ngày riêng, câu ghép hoặc lịch sử vẫn do planner
xử lý; mọi tool của registry tiếp tục được cung cấp. Preset không tạo câu trả lời.
Tổng quan có total_load_power để không phụ thuộc phép cộng của model; chẩn đoán
PV/inverter lấy xu hướng gọn ngay cùng nhóm dữ liệu. Thiếu SOC/dung lượng không
được suy thời gian dùng; thiếu khung giờ đêm qua thì hỏi lại. Context có đơn giá
ước tính dashboard để không hỏi giá đã được cấu hình.

Kết quả đo Codex CLI gpt-6-luna trên system_id=1, snapshot
2026-10-03 23:41 UTC+7:

| Chỉ số toàn bộ 29 câu | Trước | Sau |
|---|---:|---:|
| Input tokens (gồm cache) | 955.601 | 469.544 |
| Input chưa cache | 547.025 | 289.832 |
| Trung vị input/câu | 30.540 | 12.286 |
| Vòng Odoo gọi Codex CLI | 59 | 33 |

Tổng input giảm 50,86%; 5 câu chưa giảm trong lượt đo. Token CLI có thể dao động
vì cache/hoạt động nội bộ và số truy vấn do model chọn; không cam kết mức giảm
cố định cho từng câu. Thống kê/warnings/null/coverage không bị cắt để đạt số này.
Lượt trước tuổi mẫu theo đồng hồ chạy; lượt sau cố định freshness, nên chỉ dùng
cùng snapshot số đo/phạm vi để đối chiếu. Báo cáo đầy đủ cùng evidence được lưu
cục bộ trong Odoo workspace `.odoo-runtime/ai-question-eval/report.md`.

117 kiểm thử Odoo đạt trên database clone (0 failed, 0 errors). Runtime đã được
nạp lại và smoke-test tool, nhưng benchmark không thay đổi lựa chọn provider;
Settings tại thời điểm đo vẫn chọn Ollama/gemma4:12b-it-qat.
