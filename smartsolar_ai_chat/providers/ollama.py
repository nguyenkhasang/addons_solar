# -*- coding: utf-8 -*-
"""OllamaProvider — endpoint /api/chat của Ollama.

Ollama có định dạng request/response KHÁC OpenAI. Toàn bộ việc chuyển đổi nằm
GỌN trong class này — business layer không hề biết. Đây là lý do có Provider Layer.

Khác biệt chính so với OpenAI:
  - Endpoint /api/chat (không phải /v1/chat/completions).
  - Response: {message: {content, tool_calls: [{function:{name, arguments}}]}}
    (arguments là dict sẵn, không phải chuỗi JSON như OpenAI).
  - Không có tool_call id -> ta tự sinh id giả để hợp với chuẩn nội bộ.
  - Kết quả tool gửi lại bằng role='tool' (không cần tool_call_id).
"""
from __future__ import annotations

import json
import logging

from .base import (
    AIProvider, ChatRequest, ChatResponse, ToolCall, ProviderError,
    extract_text_tool_calls,
)

_logger = logging.getLogger('smartsolar_ai.provider')


def _redact_images_for_log(payload):
    """Trả bản sao payload để LOG: base64 trong 'images' bị cắt còn 32 ký tự đầu
    + độ dài, để không tràn log mà vẫn thấy cấu trúc và đầu chuỗi base64 (bắt lỗi
    prefix 'data:' lọt vào, chuỗi rỗng, hay ký tự lạ)."""
    import copy
    clone = copy.deepcopy(payload)
    for msg in clone.get('messages', []):
        imgs = msg.get('images')
        if isinstance(imgs, list):
            msg['images'] = ['<%d ký tự, đầu: %r>' % (len(i or ''), (i or '')[:32])
                             for i in imgs]
    return json.dumps(clone, ensure_ascii=False)


class OllamaProvider(AIProvider):

    def chat(self, request: ChatRequest) -> ChatResponse:
        import requests

        payload = {
            'model': request.model or self.model,
            'messages': request.messages,
            'stream': False,
        }
        if request.tools:
            payload['tools'] = request.tools
        options = {}
        if request.temperature is not None:
            options['temperature'] = request.temperature
        if request.max_tokens is not None:
            options['num_predict'] = request.max_tokens
        if request.context_window is not None:
            options['num_ctx'] = request.context_window
        if options:
            payload['options'] = options

        # Chẩn đoán ảnh: in payload THẬT gửi tới Ollama, nhưng CẮT base64 trong
        # 'images' để không tràn log. Giúp so trực tiếp với payload chạy tay của
        # bạn (kiểm tra: có message system không? images nằm đúng message user
        # không? base64 dài bao nhiêu, có ký tự lạ đầu chuỗi không?).
        # Payload chứa câu hỏi và kết quả tool; chỉ ghi ở DEBUG và luôn cắt base64.
        # Không dump payload ảnh đầy đủ ra file tạm vì có thể làm lộ dữ liệu người dùng.
        if _logger.isEnabledFor(logging.DEBUG):
            _logger.debug('SmartSolar AI -> Ollama /api/chat payload: %s',
                          _redact_images_for_log(payload))
        try:
            resp = requests.post(
                self.base_url + '/api/chat',
                json=payload, timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            _logger.warning('SmartSolar AI: Ollama provider lỗi: %s', e)
            raise ProviderError(str(e))

        return self._parse(data)

    def _parse(self, data: dict) -> ChatResponse:
        """Bóc tách response Ollama -> ChatResponse chuẩn."""
        msg = data.get('message') or {}

        tool_calls = []
        for i, tc in enumerate(msg.get('tool_calls') or []):
            fn = tc.get('function') or {}
            args = fn.get('arguments')
            # Ollama trả arguments dạng dict; phòng trường hợp là chuỗi JSON.
            if isinstance(args, str):
                try:
                    args = json.loads(args or '{}')
                except json.JSONDecodeError:
                    args = {}
            name = fn.get('name') or ''
            tool_calls.append(ToolCall(
                id='call_%d_%s' % (i, name),   # Ollama không có id -> sinh giả
                name=name,
                arguments=args or {},
            ))

        content = msg.get('content') or ''

        # Dự phòng: model (vd Qwen) phát tool-call dạng text trong content thay
        # vì trường tool_calls chuẩn. Chỉ bóc khi không có native tool_calls.
        if not tool_calls and content:
            text_calls, leftover = extract_text_tool_calls(content)
            if text_calls:
                tool_calls = text_calls
                content = leftover

        finish = 'tool_calls' if tool_calls else (data.get('done_reason') or 'stop')
        # Giữ prompt_tokens/completion_tokens (tên chuẩn nội bộ) + toàn bộ trường
        # native của Ollama (timing tính bằng NANOSECOND) để business layer dựng
        # khối thống kê hiệu năng ở cuối câu trả lời. Trường nào Ollama không trả
        # (vd prompt phục vụ từ cache có thể thiếu prompt_eval_count) sẽ bị lọc bỏ.
        usage = {
            'prompt_tokens': data.get('prompt_eval_count'),
            'completion_tokens': data.get('eval_count'),
            'total_duration': data.get('total_duration'),
            'load_duration': data.get('load_duration'),
            'prompt_eval_count': data.get('prompt_eval_count'),
            'prompt_eval_duration': data.get('prompt_eval_duration'),
            'eval_count': data.get('eval_count'),
            'eval_duration': data.get('eval_duration'),
        }
        return ChatResponse(
            content=content,
            finish_reason=finish,
            tool_calls=tool_calls,
            usage={k: v for k, v in usage.items() if v is not None},
        )

    def assistant_message(self, response: ChatResponse) -> dict:
        """Lượt assistant theo shape Ollama: content + tool_calls (arguments là dict)."""
        msg = {'role': 'assistant', 'content': response.content or ''}
        if response.tool_calls:
            msg['tool_calls'] = [{
                'function': {
                    'name': tc.name,
                    'arguments': tc.arguments,   # Ollama nhận dict, không phải chuỗi
                },
            } for tc in response.tool_calls]
        return msg

    def tool_result_message(self, tool_call: ToolCall, content: str) -> dict:
        """Ollama: role='tool' + name (không dùng tool_call_id)."""
        return {
            'role': 'tool',
            'name': tool_call.name,
            'content': content,
        }

    def build_image_message(self, text, images):
        """Ollama nhúng ảnh KHÁC OpenAI: content vẫn là chuỗi, ảnh đặt ở field
        riêng 'images' (mảng base64 THUẦN, không tiền tố 'data:'). Ollama tự nhận
        diện định dạng từ dữ liệu nên bỏ qua mime, chỉ lấy b64."""
        return {
            'role': 'user',
            'content': text or '',
            'images': [img.get('b64', '') for img in (images or [])],
        }
