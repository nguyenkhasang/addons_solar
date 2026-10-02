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

# Marker khối TIẾN TRÌNH (log các bước "Đang phân tích / Vòng N gọi tool") — cùng
# cơ chế với khối thống kê: là dữ liệu phụ do hệ thống chèn (KHÔNG phải nội dung
# model sinh), nên khi nạp lại lịch sử phải CẮT BỎ để LLM không học theo/bịa lại.
# Mặc định tiến trình bị ghi đè mất khi có câu trả lời cuối; bật config để GIỮ.
_PROGRESS_MARKER = '⎯⎯⎯ 🔧 Tiến trình ⎯⎯⎯'
_PROGRESS_RE = re.compile(r'\s*' + re.escape(_PROGRESS_MARKER) + r'.*\Z', re.DOTALL)

# Chỉ dùng để retry MỘT lần nếu model local trả lời thẳng mà chưa gọi bất kỳ tool
# nào cho một câu hỏi rõ ràng cần dữ liệu hệ thống. Không bắt các câu hỏi kiến thức
# chung để tránh ép tool không cần thiết.
_DATA_INTENT_RE = re.compile(
    r'(bao nhiêu|hiện tại|bây giờ|kiểm tra|xem số liệu|báo cáo|tình trạng|'
    r'online|offline|cảnh báo|bất thường|sức khỏe|dự báo|so sánh|'
    r'hôm nay|hôm qua|tuần này|tháng này|công suất|sản lượng|năng lượng|'
    r'điện áp|dòng điện|nhiệt độ|pin|ắc quy|pv|inverter|lấy lưới|điện lưới|'
    r'hòa lưới|tổng tải|tiêu thụ|thiết bị)',
    re.IGNORECASE,
)
_CONCEPTUAL_INTENT_RE = re.compile(
    r'(là gì|khái niệm|cách hoạt động|nguyên lý)', re.IGNORECASE)

# System prompt định hướng vai trò cho LLM (kỹ sư giám sát, không phải chatbot).
_SYSTEM_PROMPT = """Bạn là kỹ sư giám sát điện mặt trời, trả lời tiếng Việt. Tự chủ chọn metric/tool, kiểm tra giả thuyết. Câu hỏi hẹp trả lời trực tiếp; báo cáo/chẩn đoán có ngữ cảnh, diễn biến, nhận định và việc cần kiểm tra, không chỉ đọc số.
QUY TẮC BẮT BUỘC:
- Câu hỏi về số liệu phải gọi tool; Không tự đặt số, không sinh SQL. Dùng system_id mặc định; chỉ hỏi lại khi không có mặc định. Gộp nhiều metric cùng khoảng trong một lần gọi; không gọi lại cùng tham số.
- ok=false là lỗi; available=false/value=null/count=0 ở số đo là thiếu dữ liệu, không phải 0. Alarm count=0 là không có cảnh báo; device total=0 là không có thiết bị. supported=false/unreliable=true: nêu reason/note, không kết luận số. list_metrics/get_system_context là ngữ cảnh, không phải số đo.
TOOL:
- 'hiện tại/bây giờ': get_aggregate từ now-10m đến now; dùng field last, không dùng avg làm giá trị hiện tại. Hôm nay: today..now. Counter: energy là kWh trong khoảng; last là chỉ số tích lũy. Metric tức thời: avg/min/max cả khoảng.
- get_snapshot: số mới nhất/tuổi mẫu theo thiết bị; get_metric_trends/get_timeseries: diễn biến; compare_periods: a_minus_b=A-B. list_metrics: mô tả chi tiết/giới hạn metric.
- Tổng quan: get_device_status, get_health_score, get_aggregate ['output_power','grid_import_power','pv_input','grid_import_energy_total','energy_exported_total','pv_energy_total']; bổ sung pin/nhiệt, cảnh báo, chất lượng và so sánh khi hữu ích, không gọi mọi tool máy móc. Bất thường: find_anomalies; cảnh báo: get_alarms; dự báo: forecast (mặc định 6 giờ), không dự báo counter/derived.
THUẬT NGỮ:
- 'Điện hòa lưới': điện từ PV và/hoặc pin qua inverter cấp tải; output_power (W), energy_exported_total (kWh), nguồn DB limiter_total. Không phải bán lên lưới; ghi nhãn 'Công suất điện hòa lưới'.
- 'Điện lưới': điện lấy từ lưới điện quốc gia; grid_import_power (W), grid_import_energy_total (kWh), nguồn DB energy_total; ghi nhãn 'Công suất điện lưới'.
- 'Điện thu PV': điện DC thu từ tấm pin qua MPPT nạp pin; pv_input (W), pv_energy_total (kWh). Không cộng nhánh nạp pin vào tổng tải; không dùng từ 'hòa lưới' như tên chung.
- Công suất điện tổng tải (W) = output_power + grid_import_power; ghi 'Công suất điện tổng tải (suy ra)'. Điện năng tải: get_aggregate(total_load_energy), đọc value và coverage_pct; không cộng counter đứng yên rồi báo 0 kWh. Khi constant_counter/zero_energy_with_nonzero_power, lấy energy_estimate từ công suất tương ứng. Ước tính chỉ cho phần có dữ liệu; nêu độ phủ, không ngoại suy hoặc chia kWh cho độ phủ để đoán cả ngày. Không tính được: chưa xác định, không phải 0. Hỏi tiêu thụ cả ngày bằng kW: hiểu kWh và giải thích ngắn.
NHẬN ĐỊNH:
- Đọc quality, tuổi mẫu, nguồn, warnings, phạm vi và độ phủ. last nhiều thiết bị không phải tổng hệ thống. end_gap nhỏ không chứng minh phủ đủ; không gọi mẫu cũ là hiện tại. health partial/coverage<100 không chứng minh khỏe toàn hệ thống; online không chứng minh công-tơ đúng. interpretation=unknown/confirmed_fault=false không chứng minh lỗi; không suy SOC/thời gian pin từ điện áp.
- So sánh cùng giờ/phạm vi; hôm nay chưa hết ngày. Timeseries truncated chỉ dùng xu hướng. Phân biệt dữ kiện, ước tính, giả thuyết; không biến tương quan thành nguyên nhân. Giải thích sản lượng cần irradiance/cloud_cover/pv_input cùng khoảng. Thiếu dữ liệu vẫn phân tích phần có căn cứ.
ĐỊNH DẠNG: kết luận trước, tên chuẩn/giá trị/đơn vị, thời gian UTC+7; đoạn văn/gạch đầu dòng, không bảng Markdown/HTML. Độ sâu theo câu hỏi; không giới hạn số nhận định. Khi đủ công suất hai nhánh, tính tổng tải suy ra. Thứ tự: PV, hòa lưới, điện lưới, tổng tải; không lặp một số dưới nhiều tên.
"""

_ELECTRICAL_TERMINOLOGY_REMINDER = (
    "Dùng đúng nhãn khi tổng hợp: output_power = Công suất điện hòa lưới; "
    "grid_import_power = Công suất điện lưới; pv_input = Công suất điện thu PV; "
    "Công suất điện tổng tải (suy ra) = output_power + grid_import_power. "
    "Không gọi điện lưới hoặc điện thu PV là điện hòa lưới; không lặp một giá trị "
    "dưới nhiều nhãn."
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
        custom_prompt = (Param.get_param('smartsolar_ai.system_prompt') or '').strip()
        # Đặt tùy chỉnh văn phong TRƯỚC các invariant bắt buộc. Cả hai cùng thuộc
        # system message, nên quy tắc dữ liệu đứng sau sẽ có độ gần cao hơn với model
        # nhỏ và không bị một custom prompt vô tình làm loãng/chống lại.
        system_prompt = ''
        if custom_prompt:
            system_prompt = (
                'TÙY CHỈNH VĂN PHONG CỦA QUẢN TRỊ VIÊN '
                '(không được ghi đè quy tắc dữ liệu bên dưới):\n%s\n\n'
                % custom_prompt)
        system_prompt += _SYSTEM_PROMPT
        return {
            'max_iterations': max(
                2, min(12, int(Param.get_param('smartsolar_ai.max_tool_iterations', 8) or 8))),
            # Custom prompt chỉ được NỐI THÊM; không thể xóa quy tắc an toàn mặc định.
            'system_prompt': system_prompt,
            # Số cặp hỏi-đáp gần nhất được nạp làm ngữ cảnh hội thoại (0 = tắt trí nhớ).
            'history_limit': int(Param.get_param('smartsolar_ai.history_limit', 6) or 0),
        }

    @staticmethod
    def _question_requires_tool(question):
        text = question or ''
        return (not _CONCEPTUAL_INTENT_RE.search(text)
                and bool(_DATA_INTENT_RE.search(text)))

    @api.model
    def _runtime_context(self):
        """Ngữ cảnh runtime nối thêm vào system prompt mỗi lần chat.

        Gồm 2 phần, đều là dữ liệu ĐỘNG nên không thể để cứng trong _SYSTEM_PROMPT:
          1. Thời điểm hiện tại (UTC+7) — để LLM tự suy 'hôm nay/hôm qua/tuần này'
             thay vì đoán ngày (model nhỏ hay đoán sai -> truy vấn lệch khoảng).
          2. Danh mục ngắn (key + nhãn + đơn vị + loại + cờ hỗ trợ) sinh
             động từ MetricRegistry -> LLM biết ngay tham số 'metric' nào dùng được,
             list_metrics cung cấp chi tiết khi cần. Vì sinh động nên
             thêm metric mới vào registry là prompt tự cập nhật, không lệch.
        """
        from odoo.addons.smartsolar_ai.tools.base_tool import now_local_iso
        from odoo.addons.smartsolar_ai.domain.metric_registry import MetricRegistry

        lines = []
        for metric in MetricRegistry.describe():
            flags = []
            if not metric.get('supported', True):
                flags.append('unsupported')
            if metric.get('unreliable'):
                flags.append('unreliable')
            if metric.get('daily_only'):
                flags.append('daily_only')
            if not metric['has_device']:
                flags.append('system_only')
            lines.append('%s: %s [%s,%s%s]' % (
                metric['key'], metric.get('label', ''), metric['unit'] or '-',
                metric['kind'], ',' + ','.join(flags) if flags else ''))
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
                "HỆ THỐNG MẶC ĐỊNH: system_id=%d (\"%s\"). Hỏi chung chung không nêu "
                "hệ thống thì dùng id này, gọi tool luôn, KHÔNG hỏi lại.\n"
            ) % (default_system.id, default_system.name or '')
        else:
            default_line = ''

        return (
            "\n\nTHỜI ĐIỂM HIỆN TẠI (UTC+7): %s (chỉ tham khảo).\n"
            "%s"
            "\n"
            "TOKEN THỜI GIAN (server tự đổi UTC+7; không tự trừ 7 giờ):\n"
            "- hiện tại: now-10m..now; hôm nay đến lúc này: today..now; hôm qua: "
            "yesterday..today; N ngày qua: now-Nd..now.\n"
            "- Cùng kỳ năm trước có thể ghép token: now-1y-3d..now-1y. Ngày/giờ "
            "cụ thể dùng ISO giờ Việt Nam không kèm Z/offset.\n"
            "- Metric có '[chỉ theo NGÀY]' không có chi tiết theo giờ.\n"
            "\n"
            "CÁC METRIC CÓ SẴN (dùng đúng key cho 'metric'/'metrics'; khỏi gọi "
            "list_metrics nếu cần mô tả chi tiết/giới hạn. unsupported/unreliable: không kết luận số; "
            "daily_only: chỉ theo ngày; system_only: không truyền device_id):\n%s"
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
    def _progress_enabled(self):
        """Bật/tắt việc GIỮ khối tiến trình dưới câu trả lời cuối (mặc định TẮT).

        Khác _stats_enabled ở giá trị mặc định: tiến trình vốn chỉ là hiệu ứng
        "log dần" trong lúc chờ, xong thì bị câu trả lời ghi đè. Chỉ khi user bật
        config này mới nối lại khối tiến trình vào cuối câu trả lời chính thức.
        """
        Param = self.env['ir.config_parameter'].sudo()
        val = (Param.get_param('smartsolar_ai.show_progress', 'False') or '').strip().lower()
        return val in ('1', 'true', 'yes', 'on')

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
        if usage.get('cached_prompt_tokens'):
            lines.append(_('Token đầu vào được cache (đã nằm trong prompt): %s') % usage['cached_prompt_tokens'])
        if usage.get('llm_calls'):
            lines.append(_('Số lượt gọi model: %s') % usage['llm_calls'])
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
        'cached_prompt_tokens', 'llm_calls', 'prompt_chars',
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
    def _format_progress_block(self, progress_lines):
        """Dựng khối tiến trình (text) từ các dòng bước đã ghi trong planner loop.

        Trả về '' nếu không có dòng nào (khỏi nối marker rỗng). Các dòng vốn đã có
        icon 🔍/🔧 nên giữ nguyên, chỉ bọc dưới marker để về sau strip được.
        """
        lines = [l for l in (progress_lines or []) if l]
        if not lines:
            return ''
        return '\n\n%s\n%s' % (_PROGRESS_MARKER, '\n'.join(lines))

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

    @api.model
    def strip_progress(self, text):
        """Cắt bỏ khối tiến trình (từ marker tới hết) khỏi một câu trả lời cũ.

        Song song với strip_stats: khối tiến trình cũng là dữ liệu phụ hệ thống
        chèn, không phải nội dung model -> gỡ khi nạp lại lịch sử. An toàn rỗng/None.
        """
        if not text:
            return text
        return _PROGRESS_RE.sub('', text)

    @staticmethod
    def _contains_unavailable(value):
        """Có nhánh dữ liệu ``available=false`` trong kết quả tool hay không."""
        if isinstance(value, dict):
            if value.get('available') is False:
                return True
            return any(SmartSolarAIAgent._contains_unavailable(v)
                       for v in value.values())
        if isinstance(value, list):
            return any(SmartSolarAIAgent._contains_unavailable(v) for v in value)
        return False

    # ------------------------------------------------------------------
    # Entry point: hỏi 1 câu, nhận câu trả lời cuối cùng (chuỗi)
    # ------------------------------------------------------------------
    @api.model
    def _bounded_history(self, history, max_chars=8000, max_message_chars=3000):
        """Keep recent text context; explicitly mark truncation and reverify data."""
        result, remaining = [], max_chars
        for message in reversed(history or []):
            if message.get('role') not in ('user', 'assistant'):
                continue
            text = message.get('content')
            if not isinstance(text, str):
                continue
            text = self.strip_stats(self.strip_progress(text)).strip()
            if not text:
                continue
            if remaining <= 0:
                break
            allowance = min(remaining, max_message_chars)
            if len(text) > allowance:
                suffix = '\n[Lịch sử rút gọn; gọi tool lại để xác minh số liệu.]'
                if allowance <= len(suffix):
                    break
                text = text[:allowance - len(suffix)] + suffix
            result.append({'role': message['role'], 'content': text})
            remaining -= len(text)
        return list(reversed(result))

    @api.model
    def chat(self, question, history=None, on_progress=None):
        """Chạy planner loop cho một câu hỏi. Trả về chuỗi trả lời.

        history: danh sách message trước đó (tùy chọn) để giữ ngữ cảnh hội thoại.
        on_progress: callback(text) tùy chọn — được gọi mỗi bước để báo tiến trình
            (vd hiển thị "Đang phân tích...", "Vòng 1: gọi tool X") lên UI theo thời
            gian thực. None = chạy im lặng (tương thích ngược). Là hàm thuần nên
            provider layer KHÔNG cần biết tới nó.

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
            messages.extend(self._bounded_history(history))
            _logger.info('SmartSolar AI: nạp %d tin lịch sử, chars=%d',
                         len(messages) - 1, sum(len(m['content']) for m in messages[1:]))
        messages.append({'role': 'user', 'content': question})

        # Bộ tích lũy usage: gộp MỌI lượt LLM trong loop để thống kê phản ánh
        # đúng tổng chi phí cả câu hỏi (không chỉ lượt cuối). Xem _merge_usage.
        usage_total = {}
        tool_cache = {}
        has_successful_tool_result = False
        forced_tool_retry = False

        # Log tiến trình CỘNG DỒN: giữ các dòng bước trước để tạo cảm giác "log dần"
        # (vòng 1 -> vòng 2 -> ...). Chỉ dùng khi có on_progress.
        progress_lines = []

        def _emit(line):
            """Thêm một dòng vào log tiến trình rồi đẩy toàn bộ qua on_progress."""
            if not on_progress:
                return
            progress_lines.append(line)
            try:
                on_progress('\n'.join(progress_lines))
            except Exception as e:  # noqa: BLE001 - báo tiến trình không được làm chết luồng
                _logger.warning('SmartSolar AI: on_progress lỗi (bỏ qua): %s', e)

        _emit(_('🔍 Đang phân tích câu hỏi...'))

        try:
            for _i in range(cfg['max_iterations']):
                require_tool = (
                    getattr(provider, 'supports_required_tool_choice', False)
                    and self._question_requires_tool(question)
                    and not has_successful_tool_result)
                response = provider.chat(ChatRequest(
                    messages=messages, tools=tools,
                    tool_choice='required' if require_tool else None))
                self._merge_usage(usage_total, response.usage)

                # LLM trả lời cuối (không gọi thêm tool) -> xong.
                if not response.has_tool_calls:
                    if (not has_successful_tool_result and not forced_tool_retry
                            and self._question_requires_tool(question)):
                        forced_tool_retry = True
                        messages.append(provider.assistant_message(response))
                        messages.append({
                            'role': 'user',
                            'content': ('Bạn chưa gọi tool. Câu hỏi này cần dữ liệu hệ thống '
                                        'thật; hãy gọi tool phù hợp trước khi trả lời.'),
                        })
                        _logger.info('SmartSolar AI: buộc retry vì model chưa gọi tool')
                        continue
                    if (not has_successful_tool_result and forced_tool_retry
                            and self._question_requires_tool(question)):
                        answer = _(
                            'Model chưa gọi được tool dữ liệu nên không thể đưa ra số liệu đáng tin cậy. '
                            'Vui lòng thử lại hoặc kiểm tra model có hỗ trợ tool calling.')
                        if self._stats_enabled():
                            answer += self._format_stats_block(usage_total)
                        return answer
                    _logger.info('SmartSolar AI: AGENT vòng %d: LLM trả lời cuối (không '
                                 'gọi tool), độ dài content=%d', _i, len(response.content or ''))
                    answer = response.content or _("(LLM không trả về nội dung)")
                    if self._progress_enabled():
                        answer += self._format_progress_block(progress_lines)
                    if self._stats_enabled():
                        answer += self._format_stats_block(usage_total)
                    return answer

                _logger.info('SmartSolar AI: AGENT vòng %d: LLM yêu cầu %d tool -> %s', _i,
                             len(response.tool_calls),
                             [tc.name for tc in response.tool_calls])
                _emit(_('🔧 Vòng %(round)s: đang gọi %(tools)s') % {
                    'round': _i + 1,
                    'tools': ', '.join(tc.name for tc in response.tool_calls),
                })

                # Nối lượt assistant (giữ tool_calls) theo shape của provider.
                messages.append(provider.assistant_message(response))
                # Chạy từng tool qua registry (đã có log), gửi kết quả lại đúng chuẩn.
                if len(response.tool_calls) > 12:
                    raise ProviderError('AI yêu cầu quá nhiều tool trong một lượt (tối đa 12).')
                for tc in response.tool_calls:
                    cache_key = (tc.name, json.dumps(
                        tc.arguments or {}, ensure_ascii=False, sort_keys=True, default=str))
                    if cache_key in tool_cache:
                        # Sao chép qua JSON để không sửa envelope cache gốc.
                        envelope = json.loads(tool_cache[cache_key])
                        envelope.setdefault('meta', {})['cached'] = True
                        envelope['meta']['instruction'] = (
                            'Kết quả này đã được trả trước đó; không gọi lại cùng tham số, '
                            'hãy tổng hợp câu trả lời hoặc đổi tham số.')
                    else:
                        envelope = registry.execute(tc.name, tc.arguments)
                        tool_cache[cache_key] = json.dumps(
                            envelope, ensure_ascii=False, default=str)
                    # Một tool trả lỗi không phải là bằng chứng dữ liệu. Model local
                    # phải sửa tham số/gọi tool khác, nếu không agent sẽ fail closed.
                    if envelope.get('ok') and tc.name not in ('list_metrics', 'get_system_context'):
                        has_successful_tool_result = True
                    meta = envelope.setdefault('meta', {})
                    if envelope.get('ok') and tc.name not in ('list_metrics', 'get_system_context'):
                        # Nhắc lại ngay cạnh dữ liệu tool. Với model nhỏ (Gemma 12B),
                        # chỉ dẫn gần kết quả có độ bám tốt hơn phần đầu system prompt.
                        meta['electrical_terminology'] = _ELECTRICAL_TERMINOLOGY_REMINDER
                    if not envelope.get('ok'):
                        meta['instruction'] = (
                            'Tool lỗi: không dùng kết quả này làm dữ liệu. Hãy sửa tham số '
                            'hoặc gọi tool phù hợp khác.')
                    elif self._contains_unavailable(envelope.get('data')):
                        meta['instruction'] = (
                            'Có mục available=false: không báo số cho mục đó; hãy nêu '
                            'reason và chỉ dùng các mục available=true.')
                    elif tc.name in ('list_metrics', 'get_system_context'):
                        meta['instruction'] = (
                            'Đây chỉ là ngữ cảnh/danh mục metric, không phải số đo. Muốn trả lời '
                            'số liệu phải gọi tool dữ liệu.')
                    content = json.dumps(envelope, ensure_ascii=False, default=str)
                    messages.append(provider.tool_result_message(tc, content))

            # Chạm giới hạn vòng lặp: gọi LLM lần cuối (không tool) để chốt câu trả lời.
            if (self._question_requires_tool(question)
                    and not has_successful_tool_result):
                answer = _(
                    'Model chưa lấy được dữ liệu hợp lệ từ tool nên không thể đưa ra '
                    'số liệu đáng tin cậy. Vui lòng thử lại hoặc kiểm tra tham số/tool.')
                if self._stats_enabled():
                    answer += self._format_stats_block(usage_total)
                return answer
            messages.append({'role': 'user', 'content': (
                'Đã hết ngân sách truy vấn của lượt này. Tổng hợp từ bằng chứng đã lấy; '
                'nêu điều đã xác minh, dữ liệu thiếu và bước kiểm tra tiếp theo. Không yêu cầu thêm tool.')})
            final = provider.chat(ChatRequest(messages=messages, tool_choice='none'))
            self._merge_usage(usage_total, final.usage)
            answer = final.content or _("(Đã đạt giới hạn số bước)")
            if self._progress_enabled():
                answer += self._format_progress_block(progress_lines)
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
    def analyze_images(self, question, images, history=None, on_progress=None):
        """Phân tích ảnh do user gửi. Trả về chuỗi trả lời.

        Khác hẳn chat(): CHỈ gọi LLM MỘT lượt và KHÔNG truyền tools -> không chạy
        planner loop. Lý do:
          - Đúng yêu cầu "gửi ảnh thì phân tích, không gọi tool".
          - Tránh vướng chuyện nhiều model vision yếu về tool-calling.

        images: danh sách dict {'mime', 'b64'} (b64 là base64 thuần). Provider tự
        nhúng theo chuẩn của nó (OpenAI content-array vs Ollama field 'images')
        qua build_image_message() -> business layer không cần biết provider nào.

        history vẫn là text-only (không nhồi lại ảnh cũ) để tiết kiệm token.
        on_progress: callback(text) tùy chọn để báo tiến trình lên UI (xem chat()).
        """
        from ..providers.base import ChatRequest, ProviderError
        from ..providers.factory import get_provider

        if not images:
            # Không có ảnh -> quay về luồng chat thường (an toàn nếu bị gọi nhầm).
            return self.chat(question, history=history, on_progress=on_progress)

        if on_progress:
            try:
                on_progress(_('🖼️ Đang phân tích ảnh...'))
            except Exception as e:  # noqa: BLE001 - báo tiến trình không được làm chết luồng
                _logger.warning('SmartSolar AI: on_progress lỗi (bỏ qua): %s', e)

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
