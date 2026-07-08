# -*- coding: utf-8 -*-
"""SmartSolar AI Agent — planner loop nối LLM (Ollama) với Tool Layer.

Đây là mảnh "phần AI" mà module smartsolar_ai cố ý không chứa: nó điều phối
vòng lặp tool-calling giữa LLM và các Tool đã định nghĩa.

Luồng (blocking / đồng bộ):
    1. Gửi câu hỏi user + danh sách tool (spec OpenAI) cho Ollama.
    2. Nếu LLM trả về tool_calls -> chạy từng tool qua ToolRegistry (tái dùng
       nguyên tầng Tool của smartsolar_ai), đưa kết quả JSON trở lại LLM.
    3. Lặp đến khi LLM trả lời cuối (không còn tool_calls) hoặc chạm giới hạn
       số vòng lặp.

LLM KHÔNG chạm DB, KHÔNG sinh SQL — chỉ gọi tool. Đúng kiến trúc đã thiết kế.
"""
from __future__ import annotations

import json
import logging
import re

from odoo import models, api, _

_logger = logging.getLogger(__name__)

# Marker phân tách khối THỐNG KÊ hiệu năng ở cuối câu trả lời của AI.
# Dùng chuỗi cố định, hiếm gặp trong văn bản tự nhiên để:
#   1. Nhận diện chắc chắn khi cần CẮT BỎ trước lúc nạp lại lịch sử cho LLM
#      (không để LLM học theo và tự bịa số liệu thống kê ở các lượt sau).
#   2. Không đụng nội dung thật do model sinh ra.
_STATS_MARKER = '⎯⎯⎯ 📊 Thống kê ⎯⎯⎯'
# Regex cắt từ marker tới hết chuỗi (kèm mọi khoảng trắng đứng trước marker).
_STATS_RE = re.compile(r'\s*' + re.escape(_STATS_MARKER) + r'.*\Z', re.DOTALL)

# System prompt định hướng vai trò cho LLM (kỹ sư giám sát, không phải chatbot).
_SYSTEM_PROMPT = (
    "Bạn là kỹ sư giám sát hệ thống điện mặt trời. Nhiệm vụ: đọc dữ liệu THẬT từ "
    "các công cụ (tools) và viết báo cáo.\n"
    "\n"
    "QUY TẮC BẮT BUỘC — chống bịa số:\n"
    "1. TUYỆT ĐỐI KHÔNG tự nghĩ ra con số. Mọi giá trị (W, V, A, kWh, %, °C...) "
    "phải lấy từ kết quả JSON mà tool trả về trong hội thoại này.\n"
    "2. Tool 'list_metrics' CHỈ liệt kê TÊN metric và đơn vị — nó KHÔNG chứa giá "
    "trị đo. Không được suy ra hay bịa giá trị từ list_metrics. Muốn có số phải "
    "gọi 'get_aggregate' hoặc 'get_timeseries' với metric và khoảng thời gian cụ thể.\n"
    "3. Nếu chưa gọi tool lấy số, hoặc tool trả về rỗng/không có dữ liệu, hãy NÓI "
    "RÕ 'chưa có dữ liệu' — KHÔNG được điền số thay thế.\n"
    "4. Chỉ báo cáo đúng những metric mà người dùng hỏi hoặc thật sự liên quan; "
    "không liệt kê tất cả metric kèm số bịa.\n"
    "\n"
    "ĐỐI CHIẾU THỜI TIẾT — SẢN LƯỢNG:\n"
    "- Có nhóm metric môi trường (irradiance/bức xạ, cloud_cover/mây, ambient_temp, "
    "humidity, uv_index, sunshine_duration, wind_speed) lấy từ trạm thời tiết của hệ "
    "thống. Chúng chỉ lọc theo hệ thống (system_id), KHÔNG có thiết bị — đừng truyền "
    "device_id cho các metric này.\n"
    "- Khi người dùng hỏi vì sao sản lượng cao/thấp, hoặc yêu cầu đánh giá hiệu suất, "
    "hãy lấy KÈM metric môi trường cùng khoảng thời gian (vd get_aggregate với cả "
    "['irradiance','cloud_cover','pv_input']) rồi đối chiếu: nắng tốt/bức xạ cao mà "
    "PV nạp thấp là dấu hiệu bất thường; nhiều mây/mưa mà sản lượng thấp là bình thường.\n"
    "- Chỉ nêu mối liên hệ khi CÓ dữ liệu cả hai phía; nếu thiếu một phía thì nói rõ.\n"
    "\n"
    "QUY TẮC ĐỊNH DẠNG (Odoo Discuss KHÔNG render được bảng):\n"
    "- TUYỆT ĐỐI KHÔNG dùng bảng Markdown (dạng có |---|) hay bảng HTML — chúng hiện "
    "ra dạng text thô, khó đọc trên Discuss.\n"
    "- Chỉ trình bày bằng: tiêu đề (heading), danh sách gạch đầu dòng (bullet), hoặc "
    "danh sách đánh số.\n"
    "- Mỗi giá trị số trình bày trên một dòng theo dạng: 'Tên chỉ số: Giá trị Đơn vị' "
    "(vd 'Bức xạ mặt trời: 18.08 MJ/m²').\n"
    "- Ưu tiên bố cục ngắn, dễ đọc trên cả máy tính lẫn điện thoại.\n"
    "\n"
    "Sau khi có dữ liệu JSON thật, viết báo cáo ngắn gọn bằng tiếng Việt, nêu con "
    "số cụ thể kèm đơn vị và một nhận định hữu ích. Thời gian theo giờ Việt Nam (UTC+7)."
)

# Prompt riêng cho chế độ PHÂN TÍCH ẢNH: gọn, không có catalog metric hay quy tắc
# tool (chế độ ảnh KHÔNG gọi tool). Chỉ giữ vai trò + quy tắc định dạng Discuss.
_VISION_SYSTEM_PROMPT = (
    "Bạn là kỹ sư giám sát hệ thống điện mặt trời. Người dùng gửi kèm ẢNH. Nhiệm "
    "vụ: quan sát ảnh và mô tả những gì thấy được, nêu nhận định kỹ thuật hữu ích "
    "(vd dấu hiệu hư hỏng tấm pin, bụi bẩn, đấu nối, chỉ số trên màn hình thiết "
    "bị...).\n"
    "- Chỉ nói về những gì THỰC SỰ nhìn thấy trong ảnh; không bịa chi tiết không "
    "có. Nếu ảnh mờ/không rõ, hãy nói rõ.\n"
    "- Trả lời bằng tiếng Việt, ngắn gọn.\n"
    "- KHÔNG dùng bảng Markdown/HTML (Discuss không render được); chỉ dùng tiêu đề, "
    "gạch đầu dòng hoặc danh sách đánh số."
)


class SmartSolarAIAgent(models.AbstractModel):
    _name = 'smartsolar.ai.agent'
    _description = 'SmartSolar AI Agent (planner loop)'

    # ------------------------------------------------------------------
    # Cấu hình
    # ------------------------------------------------------------------
    @api.model
    def _get_config(self):
        Param = self.env['ir.config_parameter'].sudo()
        return {
            'max_iterations': int(Param.get_param('smartsolar_ai.max_tool_iterations', 5) or 5),
            # System prompt cấu hình được; rỗng -> dùng mặc định _SYSTEM_PROMPT.
            'system_prompt': (Param.get_param('smartsolar_ai.system_prompt') or '').strip() or _SYSTEM_PROMPT,
            # Số cặp hỏi-đáp gần nhất được nạp làm ngữ cảnh hội thoại (0 = tắt trí nhớ).
            'history_limit': int(Param.get_param('smartsolar_ai.history_limit', 6) or 0),
        }

    @api.model
    def _runtime_context(self):
        """Ngữ cảnh runtime nối thêm vào system prompt mỗi lần chat.

        Gồm 2 phần, đều là dữ liệu ĐỘNG nên không thể để cứng trong _SYSTEM_PROMPT:
          1. Thời điểm hiện tại (UTC+7) — để LLM tự suy 'hôm nay/hôm qua/tuần này'
             thay vì đoán ngày (model nhỏ hay đoán sai -> truy vấn lệch khoảng).
          2. Danh mục metric hợp lệ (key + đơn vị + có lọc theo thiết bị không) sinh
             động từ MetricRegistry -> LLM biết ngay tham số 'metric' nào dùng được,
             khỏi tốn một vòng gọi list_metrics trước mỗi câu hỏi. Vì sinh động nên
             thêm metric mới vào registry là prompt tự cập nhật, không lệch.
        """
        from odoo.addons.smartsolar_ai.tools.base_tool import now_local_iso
        from odoo.addons.smartsolar_ai.domain.metric_registry import MetricRegistry

        lines = []
        for m in MetricRegistry.describe():
            scope = '' if m['has_device'] else ' [chỉ system_id, KHÔNG truyền device_id]'
            # Đánh dấu metric chỉ có độ phân giải NGÀY (thời tiết) để LLM không hỏi
            # theo giờ và không kỳ vọng dữ liệu chi tiết cũ hơn ~7 ngày.
            daily = ' [chỉ theo NGÀY, chi tiết ~7 ngày gần nhất]' if m.get('daily_only') else ''
            lines.append('- %s (%s)%s%s' % (m['key'], m['unit'] or '-', scope, daily))
        catalog = '\n'.join(lines)

        # Hệ thống mặc định: hệ thống có id NHỎ NHẤT mà user hiện tại phụ trách
        # (user_id). Nạp sẵn vào prompt để khi user hỏi chung chung ("kiểm tra
        # thông số hệ thống hôm nay") LLM khỏi phải hỏi lại system_id. Record rules
        # đã tự lọc theo công ty; nếu user không phụ trách hệ thống nào thì lấy hệ
        # thống đầu tiên user được phép xem (fallback), hoặc rỗng.
        System = self.env['smartsolar.system']
        default_system = System.search(
            [('user_id', '=', self.env.uid)], order='id asc', limit=1)
        if not default_system:
            default_system = System.search([], order='id asc', limit=1)
        if default_system:
            default_line = (
                "\n\nHỆ THỐNG MẶC ĐỊNH: system_id=%d (\"%s\"). Khi người dùng hỏi "
                "chung chung KHÔNG nêu rõ hệ thống nào (vd \"kiểm tra thông số hệ "
                "thống hôm nay\"), MẶC ĐỊNH dùng system_id=%d — TUYỆT ĐỐI KHÔNG hỏi "
                "lại người dùng system_id, cứ gọi tool luôn với id này.\n"
            ) % (default_system.id, default_system.name or '', default_system.id)
        else:
            default_line = ''

        return (
            "\n\nTHỜI ĐIỂM HIỆN TẠI (UTC+7): %s (chỉ để tham khảo).\n"
            "QUY TẮC THỜI GIAN (bắt buộc, tránh lệch 7 giờ):\n"
            "- Với câu hỏi TƯƠNG ĐỐI (gần nhất, hôm nay, hôm qua, N giờ/ngày qua): "
            "DÙNG TOKEN cho start/end, KHÔNG tự tính giờ. Ví dụ:\n"
            "    '2 giờ gần nhất' -> start='now-2h', end='now'\n"
            "    'từ sáng đến giờ' -> start='today', end='now'\n"
            "    'hôm nay'         -> start='today', end='tomorrow'\n"
            "    'hôm qua'         -> start='yesterday', end='today'\n"
            "    '7 ngày qua'      -> start='now-7d', end='now'\n"
            "- Chỉ khi người dùng nêu NGÀY/GIỜ CỤ THỂ mới dùng ISO giờ Việt Nam, "
            "KHÔNG kèm hậu tố múi giờ (viết '2026-07-06T00:00:00', không viết 'Z' "
            "hay '+07:00').\n"
            "- TUYỆT ĐỐI KHÔNG tự trừ 7 giờ và KHÔNG tự đổi sang UTC — server lo việc đó.\n"
            "\n"
            "METRIC THỜI TIẾT (các metric có nhãn '[chỉ theo NGÀY...]'):\n"
            "- Chỉ có độ phân giải theo NGÀY, KHÔNG có số liệu theo giờ. Đừng hứa "
            "'thời tiết lúc 14h'; chỉ nói được giá trị theo ngày.\n"
            "- Số liệu chi tiết chỉ còn ~7 ngày gần nhất; xa hơn chỉ còn tổng hợp theo ngày.\n"
            "- Muốn xu hướng/nhiều ngày: DÙNG get_timeseries (đọc bảng tổng hợp ngày), "
            "KHÔNG dùng get_aggregate cho khoảng dài — get_aggregate đọc dữ liệu chi "
            "tiết nên khoảng quá ~7 ngày có thể trả về rỗng.\n"
            "- Nếu kết quả có count=0 hoặc rỗng: nói 'không có dữ liệu cho khoảng này', "
            "TUYỆT ĐỐI KHÔNG báo giá trị 0 như thể đo được 0.\n"
            "%s"
            "\n"
            "CÁC METRIC CÓ SẴN (dùng đúng key này cho tham số 'metric'/'metrics'; "
            "KHÔNG cần gọi list_metrics nếu key đã có ở đây):\n%s"
        ) % (now_local_iso(), default_line, catalog)

    # ------------------------------------------------------------------
    # Khối THỐNG KÊ hiệu năng nối vào cuối câu trả lời
    # ------------------------------------------------------------------
    @api.model
    def _stats_enabled(self):
        """Bật/tắt khối thống kê qua config (mặc định BẬT)."""
        Param = self.env['ir.config_parameter'].sudo()
        val = (Param.get_param('smartsolar_ai.show_stats', 'True') or '').strip().lower()
        return val not in ('0', 'false', 'no', 'off', '')

    @api.model
    def _format_stats_block(self, usage):
        """Dựng khối thống kê (text) từ dict `usage` mà provider trả về.

        Ollama trả token count + timing (NANOSECOND); OpenAI-compatible chỉ trả
        token count. Trường nào thiếu thì bỏ dòng đó -> khối tự co theo provider.
        Trả về '' nếu không có gì để hiển thị (khỏi nối marker rỗng).
        """
        if not usage:
            return ''

        # Token: ưu tiên tên chuẩn nội bộ, fallback tên native Ollama.
        prompt_tok = usage.get('prompt_tokens')
        if prompt_tok is None:
            prompt_tok = usage.get('prompt_eval_count')
        completion_tok = usage.get('completion_tokens')
        if completion_tok is None:
            completion_tok = usage.get('eval_count')
        total_tok = usage.get('total_tokens')
        if total_tok is None and (prompt_tok is not None or completion_tok is not None):
            total_tok = (prompt_tok or 0) + (completion_tok or 0)

        def _sec(ns):
            """Nanosecond -> chuỗi giây gọn (Ollama dùng ns). None -> None."""
            if ns is None:
                return None
            return '%.2f s' % (ns / 1e9)

        lines = []
        if prompt_tok is not None:
            lines.append(_('Token đầu vào (prompt): %s') % prompt_tok)
        if completion_tok is not None:
            lines.append(_('Token đầu ra (completion): %s') % completion_tok)
        if total_tok is not None:
            lines.append(_('Tổng token: %s') % total_tok)

        # Timing native của Ollama (chỉ có ở provider này).
        total_dur = _sec(usage.get('total_duration'))
        load_dur = _sec(usage.get('load_duration'))
        prompt_dur_ns = usage.get('prompt_eval_duration')
        prompt_dur = _sec(prompt_dur_ns)
        eval_dur_ns = usage.get('eval_duration')
        eval_dur = _sec(eval_dur_ns)
        if total_dur is not None:
            lines.append(_('Tổng thời gian: %s') % total_dur)
        if load_dur is not None:
            lines.append(_('Thời gian nạp model: %s') % load_dur)
        if prompt_dur is not None:
            lines.append(_('Thời gian xử lý prompt: %s') % prompt_dur)
        # Tốc độ xử lý prompt (tokens/giây) — tính khi có đủ prompt_eval_count +
        # prompt_eval_duration.
        if prompt_tok and prompt_dur_ns:
            pps = prompt_tok / (prompt_dur_ns / 1e9)
            lines.append(_('Tốc độ xử lý prompt: %.1f token/giây') % pps)
        if eval_dur is not None:
            lines.append(_('Thời gian sinh trả lời: %s') % eval_dur)
        # Tốc độ sinh token (tokens/giây) — tính khi có đủ eval_count + eval_duration.
        if completion_tok and eval_dur_ns:
            tps = completion_tok / (eval_dur_ns / 1e9)
            lines.append(_('Tốc độ sinh: %.1f token/giây') % tps)

        if not lines:
            return ''
        return '\n\n%s\n%s' % (_STATS_MARKER, '\n'.join('- ' + l for l in lines))

    # Các khóa usage CỘNG DỒN được qua nhiều lượt LLM (token đếm + thời gian ns).
    # load_duration KHÔNG cộng: model chỉ nạp một lần (lượt đầu), các lượt sau ~0;
    # cộng lại sẽ vô nghĩa. Ta lấy GIÁ TRỊ LỚN NHẤT của load_duration thay vì tổng.
    _USAGE_SUM_KEYS = (
        'prompt_tokens', 'completion_tokens', 'total_tokens',
        'prompt_eval_count', 'eval_count',
        'total_duration', 'prompt_eval_duration', 'eval_duration',
    )

    @api.model
    def _merge_usage(self, acc, usage):
        """Cộng dồn `usage` của MỘT lượt LLM vào bộ tích lũy `acc` (sửa tại chỗ).

        Vì sao cần: luồng text chạy tool loop nhiều vòng, MỖI vòng là một lời gọi
        LLM riêng tốn token/thời gian. Nếu chỉ lấy usage lượt cuối, số liệu hiển
        thị THẤP hơn thực tế. Hàm này gộp mọi lượt để thống kê phản ánh đúng tổng
        chi phí của cả câu hỏi.

        - Các khóa đếm/thời-gian (_USAGE_SUM_KEYS): cộng dồn.
        - load_duration: lấy MAX (model nạp một lần, không cộng qua các vòng).
        Bỏ qua giá trị None (provider/lượt không trả trường đó).
        """
        if not usage:
            return acc
        for k in self._USAGE_SUM_KEYS:
            v = usage.get(k)
            if v is not None:
                acc[k] = acc.get(k, 0) + v
        load = usage.get('load_duration')
        if load is not None:
            acc['load_duration'] = max(acc.get('load_duration', 0), load)
        return acc

    @api.model
    def strip_stats(self, text):
        """Cắt bỏ khối thống kê (từ marker tới hết) khỏi một câu trả lời cũ.

        Dùng khi nạp LẠI lịch sử hội thoại cho LLM: khối thống kê là dữ liệu phụ
        do hệ thống chèn, KHÔNG phải nội dung model sinh -> phải gỡ để model không
        học theo và bịa lại số liệu ở các lượt sau. An toàn với chuỗi rỗng/None.
        """
        if not text:
            return text
        return _STATS_RE.sub('', text)

    # ------------------------------------------------------------------
    # Entry point: hỏi 1 câu, nhận câu trả lời cuối cùng (chuỗi)
    # ------------------------------------------------------------------
    @api.model
    def chat(self, question, history=None):
        """Chạy planner loop cho một câu hỏi. Trả về chuỗi trả lời.

        history: danh sách message trước đó (tùy chọn) để giữ ngữ cảnh hội thoại.

        Business layer chỉ làm việc với chuẩn ChatRequest/ChatResponse của Provider
        Layer — KHÔNG biết đang chạy Ollama, OpenAI, NVIDIA hay gì. Đổi provider =
        đổi cấu hình, không sửa hàm này.
        """
        from odoo.addons.smartsolar_ai.tools.registry import ToolRegistry
        from odoo.addons.smartsolar_ai.adapters.openai_adapter import OpenAIAdapter
        from ..providers.base import ChatRequest, ProviderError
        from ..providers.factory import get_provider

        registry = ToolRegistry(self.env)
        # tool_specs() sinh schema chuẩn OpenAI Function Calling — dùng cho MỌI provider.
        tools = OpenAIAdapter(registry).tool_specs()

        cfg = self._get_config()
        provider = get_provider(self.env)

        # Bổ sung ngữ cảnh runtime vào system prompt: (1) thời điểm hiện tại để LLM
        # suy ra "hôm nay/hôm qua/7 ngày qua" — nếu không có, model tự đoán ngày và
        # thường sai -> truy vấn lệch khoảng thời gian; (2) danh mục metric để LLM
        # biết ngay key/đơn vị hợp lệ, khỏi phải gọi list_metrics trước mỗi câu hỏi.
        system_prompt = cfg['system_prompt'] + self._runtime_context()

        messages = [{'role': 'system', 'content': system_prompt}]
        if history:
            messages.extend(history)
            _logger.info('SmartSolar AI: nạp %d tin ngữ cảnh hội thoại trước', len(history))
        messages.append({'role': 'user', 'content': question})

        # Bộ tích lũy usage: gộp MỌI lượt LLM trong loop để thống kê phản ánh
        # đúng tổng chi phí cả câu hỏi (không chỉ lượt cuối). Xem _merge_usage.
        usage_total = {}

        try:
            for _i in range(cfg['max_iterations']):
                response = provider.chat(ChatRequest(messages=messages, tools=tools))
                self._merge_usage(usage_total, response.usage)

                # LLM trả lời cuối (không gọi thêm tool) -> xong.
                if not response.has_tool_calls:
                    _logger.info('SmartSolar AI: AGENT vòng %d: LLM trả lời cuối (không '
                                 'gọi tool), độ dài content=%d', _i, len(response.content or ''))
                    answer = response.content or _("(LLM không trả về nội dung)")
                    if self._stats_enabled():
                        answer += self._format_stats_block(usage_total)
                    return answer

                _logger.info('SmartSolar AI: AGENT vòng %d: LLM yêu cầu %d tool -> %s', _i,
                             len(response.tool_calls),
                             [tc.name for tc in response.tool_calls])

                # Nối lượt assistant (giữ tool_calls) theo shape của provider.
                messages.append(provider.assistant_message(response))
                # Chạy từng tool qua registry (đã có log), gửi kết quả lại đúng chuẩn.
                for tc in response.tool_calls:
                    envelope = registry.execute(tc.name, tc.arguments)
                    content = json.dumps(envelope, ensure_ascii=False, default=str)
                    messages.append(provider.tool_result_message(tc, content))

            # Chạm giới hạn vòng lặp: gọi LLM lần cuối (không tool) để chốt câu trả lời.
            final = provider.chat(ChatRequest(messages=messages))
            self._merge_usage(usage_total, final.usage)
            answer = final.content or _("(Đã đạt giới hạn số bước)")
            if self._stats_enabled():
                answer += self._format_stats_block(usage_total)
            return answer
        except ProviderError as e:
            _logger.warning('SmartSolar AI: lỗi provider: %s', e)
            return _("Không kết nối được tới LLM.\nChi tiết: %s\n\nKiểm tra cấu hình "
                     "AI trong Settings > Smart Solar AI (provider, base URL, API key, model).") % e

    # ------------------------------------------------------------------
    # Chế độ PHÂN TÍCH ẢNH: user gửi ảnh -> LLM mô tả/nhận định, KHÔNG gọi tool.
    # ------------------------------------------------------------------
    @api.model
    def analyze_images(self, question, images, history=None):
        """Phân tích ảnh do user gửi. Trả về chuỗi trả lời.

        Khác hẳn chat(): CHỈ gọi LLM MỘT lượt và KHÔNG truyền tools -> không chạy
        planner loop. Lý do:
          - Đúng yêu cầu "gửi ảnh thì phân tích, không gọi tool".
          - Tránh vướng chuyện nhiều model vision yếu về tool-calling.

        images: danh sách dict {'mime', 'b64'} (b64 là base64 thuần). Provider tự
        nhúng theo chuẩn của nó (OpenAI content-array vs Ollama field 'images')
        qua build_image_message() -> business layer không cần biết provider nào.

        history vẫn là text-only (không nhồi lại ảnh cũ) để tiết kiệm token.
        """
        from ..providers.base import ChatRequest, ProviderError
        from ..providers.factory import get_provider

        if not images:
            # Không có ảnh -> quay về luồng chat thường (an toàn nếu bị gọi nhầm).
            return self.chat(question, history=history)

        cfg = self._get_config()
        provider = get_provider(self.env)

        # QUAN TRỌNG (bài học gỡ lỗi): Gemma KHÔNG có 'system' role native — template
        # Ollama phải gộp system vào lượt user đầu, và với model vision, message
        # 'system' đứng trước có thể khiến ảnh bị bỏ. Payload chạy tay thành công
        # của user chỉ có DUY NHẤT 1 message user + images. Vì vậy KHÔNG tạo message
        # system riêng: gộp chỉ dẫn vision thẳng vào phần text của message chứa ảnh
        # -> payload khớp đúng cấu trúc đã kiểm chứng chạy được.
        user_text = question or _("Mô tả và phân tích ảnh này.")
        prompt_text = '%s\n\n---\n%s' % (_VISION_SYSTEM_PROMPT, user_text)

        # KHÔNG ghép history vào lượt phân tích ảnh: payload chạy tay thành công
        # của user chỉ có DUY NHẤT 1 message user + images. Thêm bất kỳ message
        # nào đứng trước (system HOẶC history) đều là biến số có thể làm template
        # Gemma vision bỏ ảnh. Giữ payload tối giản đúng bằng cái đã kiểm chứng;
        # bổ sung history lại sau khi xác nhận ảnh chạy.
        img_msg = provider.build_image_message(prompt_text, images)
        messages = [img_msg]

        # Chẩn đoán: in CẤU TRÚC message ảnh (không đổ base64) để biết ảnh có được
        # nhúng đúng chuẩn provider không. 'images' field = Ollama; content-array
        # có 'image_url' = OpenAI-compatible. Nếu cả hai đều vắng -> ảnh bị rớt.
        if isinstance(img_msg.get('content'), list):
            shape = 'content-array (OpenAI): %s' % [
                b.get('type') for b in img_msg['content']]
        elif 'images' in img_msg:
            shape = 'images-field (Ollama): %d ảnh' % len(img_msg['images'])
        else:
            shape = 'KHÔNG có ảnh trong message (!)'
        _logger.info('SmartSolar AI: PHÂN TÍCH ẢNH (%d ảnh: %s) '
                     'provider=%s model=%s | message shape: %s',
                     len(images),
                     [(i.get('mime'), len(i.get('b64') or '')) for i in images],
                     type(provider).__name__, provider.model, shape)
        try:
            response = provider.chat(ChatRequest(messages=messages))
            answer = response.content or _("(LLM không trả về nội dung)")
            if self._stats_enabled():
                answer += self._format_stats_block(response.usage)
            return answer
        except ProviderError as e:
            _logger.warning('SmartSolar AI: lỗi provider khi phân tích ảnh: %s', e)
            return _("Không phân tích được ảnh.\nChi tiết: %s\n\nLưu ý: model phải hỗ "
                     "trợ ảnh (vision), vd gpt-4o, llava, qwen2-vl. Kiểm tra "
                     "Settings > Smart Solar AI.") % e
