# -*- coding: utf-8 -*-
"""OpenAICompatibleProvider — chuẩn /v1/chat/completions.

Dùng chung cho MỌI nhà cung cấp nói chuẩn OpenAI: OpenAI, NVIDIA Build API,
OpenRouter, LM Studio, và các provider OpenAI-compatible khác trong tương lai.
Chúng chỉ khác nhau base_url + api_key -> KHÔNG cần class riêng cho từng cái.

Vì chuẩn nội bộ (ChatRequest/ChatResponse) đã dựa trên OpenAI, provider này gần
như "đi thẳng": chỉ đóng gói payload và bóc tách response.
"""
from __future__ import annotations

import json
import logging

from .base import (
    AIProvider, ChatRequest, ChatResponse, ToolCall, ProviderError,
    extract_text_tool_calls,
)

_logger = logging.getLogger('smartsolar_ai.provider')


class OpenAICompatibleProvider(AIProvider):

    def chat(self, request: ChatRequest) -> ChatResponse:
        import requests

        payload = {
            'model': request.model or self.model,
            'messages': request.messages,
        }
        if request.tools:
            payload['tools'] = request.tools
            payload['tool_choice'] = request.tool_choice or 'auto'
        if request.temperature is not None:
            payload['temperature'] = request.temperature
        if request.max_tokens is not None:
            payload['max_tokens'] = request.max_tokens

        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = 'Bearer %s' % self.api_key

        url = self.base_url + '/chat/completions'
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
        except Exception as e:
            _logger.warning('SmartSolar AI: OpenAI-compatible provider lỗi mạng: %s -> %s', url, e)
            raise ProviderError('Lỗi kết nối tới %s: %s' % (url, e))

        # Bắt lỗi HTTP kèm status + body để biết provider từ chối vì lý do gì
        # (401 sai key, 404 sai model/URL, 400 payload sai...).
        if resp.status_code >= 400:
            body = (resp.text or '')[:500]
            _logger.warning('SmartSolar AI: OpenAI-compatible provider HTTP %s tại %s | model=%s | body=%s',
                            resp.status_code, url, payload.get('model'), body)
            raise ProviderError('HTTP %s từ %s: %s' % (resp.status_code, url, body))

        try:
            data = resp.json()
        except Exception as e:
            _logger.warning('SmartSolar AI: OpenAI-compatible provider: response không phải JSON: %s', e)
            raise ProviderError('Response không hợp lệ từ %s: %s' % (url, (resp.text or '')[:300]))

        return self._parse(data)

    def _parse(self, data: dict) -> ChatResponse:
        """Bóc tách response OpenAI -> ChatResponse chuẩn."""
        choices = data.get('choices') or []
        if not choices:
            return ChatResponse(finish_reason='empty', usage=data.get('usage') or {})
        choice = choices[0]
        msg = choice.get('message') or {}

        tool_calls = []
        for tc in msg.get('tool_calls') or []:
            fn = tc.get('function') or {}
            raw_args = fn.get('arguments')
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args or '{}')
                except json.JSONDecodeError:
                    args = {}
            else:
                args = raw_args or {}
            tool_calls.append(ToolCall(
                id=tc.get('id') or fn.get('name') or '',
                name=fn.get('name') or '',
                arguments=args,
            ))

        content = msg.get('content') or ''
        finish = choice.get('finish_reason') or ''

        # Dự phòng: model (vd Qwen) phát tool-call dạng text trong content thay
        # vì trường tool_calls chuẩn. Chỉ bóc khi API không trả native tool_calls.
        if not tool_calls and content:
            text_calls, leftover = extract_text_tool_calls(content)
            if text_calls:
                tool_calls = text_calls
                content = leftover
                finish = 'tool_calls'

        return ChatResponse(
            content=content,
            finish_reason=finish,
            tool_calls=tool_calls,
            usage=data.get('usage') or {},
        )

    def assistant_message(self, response: ChatResponse) -> dict:
        """Lượt assistant theo shape OpenAI: content + tool_calls (nếu có)."""
        msg = {'role': 'assistant', 'content': response.content or ''}
        if response.tool_calls:
            msg['tool_calls'] = [{
                'id': tc.id,
                'type': 'function',
                'function': {
                    'name': tc.name,
                    'arguments': json.dumps(tc.arguments, ensure_ascii=False),
                },
            } for tc in response.tool_calls]
        return msg

    def tool_result_message(self, tool_call: ToolCall, content: str) -> dict:
        """Chuẩn OpenAI: role=tool + tool_call_id để map về đúng lời gọi."""
        return {
            'role': 'tool',
            'tool_call_id': tool_call.id,
            'name': tool_call.name,
            'content': content,
        }
