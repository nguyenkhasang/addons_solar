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

from odoo import models, api, _

_logger = logging.getLogger(__name__)

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

        messages = [{'role': 'system', 'content': cfg['system_prompt']}]
        if history:
            messages.extend(history)
            _logger.info('SmartSolar AI: nạp %d tin ngữ cảnh hội thoại trước', len(history))
        messages.append({'role': 'user', 'content': question})

        try:
            for _i in range(cfg['max_iterations']):
                response = provider.chat(ChatRequest(messages=messages, tools=tools))

                # LLM trả lời cuối (không gọi thêm tool) -> xong.
                if not response.has_tool_calls:
                    _logger.info('SmartSolar AI: AGENT vòng %d: LLM trả lời cuối (không '
                                 'gọi tool), độ dài content=%d', _i, len(response.content or ''))
                    return response.content or _("(LLM không trả về nội dung)")

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
            return final.content or _("(Đã đạt giới hạn số bước)")
        except ProviderError as e:
            _logger.warning('SmartSolar AI: lỗi provider: %s', e)
            return _("Không kết nối được tới LLM.\nChi tiết: %s\n\nKiểm tra cấu hình "
                     "AI trong Settings > Smart Solar AI (provider, base URL, API key, model).") % e
