# -*- coding: utf-8 -*-
"""AI Provider Layer — cấu trúc chuẩn hóa + interface trừu tượng.

Mục tiêu: business layer (planner loop trong ai_agent) CHỈ làm việc với một chuẩn
duy nhất (ChatRequest / ChatResponse / ToolCall), không biết đang chạy LLM nào.
Mỗi provider tự chịu trách nhiệm chuyển đổi sang/từ định dạng API của nó.

Chuẩn nội bộ dựa trên OpenAI Function Calling — provider nào khác chuẩn (vd Ollama)
sẽ convert NGAY TRONG provider đó, không rò rỉ ra ngoài.
"""
from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

_logger = logging.getLogger('smartsolar_ai.provider')


@dataclass
class ToolCall:
    """Một lời gọi tool do LLM yêu cầu, đã chuẩn hóa.

    id: định danh lời gọi (OpenAI cần để map kết quả; Ollama không có -> sinh giả).
    name: tên tool. arguments: dict tham số (đã parse từ JSON nếu cần).
    """
    id: str
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class ChatRequest:
    """Yêu cầu chat chuẩn hóa. Provider tự map sang payload API tương ứng."""
    messages: List[dict]
    tools: Optional[List[dict]] = None          # spec tool chuẩn OpenAI (function)
    tool_choice: Optional[str] = None           # 'auto' | 'none' | ... (tùy provider)
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    model: Optional[str] = None


@dataclass
class ChatResponse:
    """Phản hồi chat chuẩn hóa. Business layer chỉ đọc cấu trúc này."""
    content: str = ''
    finish_reason: str = ''
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class AIProvider(ABC):
    """Interface mọi provider phải cài đặt.

    Provider nhận cấu hình (url, api_key, model, timeout) lúc khởi tạo và tự lo
    việc gọi HTTP + convert định dạng.
    """

    def __init__(self, base_url=None, api_key=None, model=None, timeout=120):
        self.base_url = (base_url or '').rstrip('/')
        self.api_key = api_key or ''
        self.model = model
        self.timeout = timeout

    # ---- Bắt buộc ----
    @abstractmethod
    def chat(self, request: ChatRequest) -> ChatResponse:
        """Gọi LLM một lượt (blocking). Convert request -> gọi API -> parse
        thành ChatResponse. Ném ProviderError nếu lỗi kết nối/định dạng."""

    @abstractmethod
    def assistant_message(self, response: ChatResponse) -> dict:
        """Dựng lượt 'assistant' để nối lại vào messages cho vòng lặp kế tiếp,
        theo đúng shape mà API của provider này mong đợi (giữ tool_calls)."""

    @abstractmethod
    def tool_result_message(self, tool_call: ToolCall, content: str) -> dict:
        """Dựng message chứa KẾT QUẢ tool để gửi lại cho LLM, theo đúng chuẩn
        của từng API (yêu cầu #5). content là chuỗi JSON của phong bì tool."""

    # ---- Tùy chọn ----
    def stream_chat(self, request: ChatRequest):
        """Chat dạng stream. Mặc định chưa hỗ trợ; provider nào cần thì override."""
        raise NotImplementedError("stream_chat chưa được hỗ trợ cho provider này")


class ProviderError(Exception):
    """Lỗi ở tầng provider (kết nối, HTTP, parse). Business layer bắt để báo user."""


# ----------------------------------------------------------------------
# Parser dự phòng: bóc tool-call NẰM TRONG content (text) khi model KHÔNG
# phát native tool_calls.
#
# Một số model (vd Qwen) trả lời tool-call dưới dạng VĂN BẢN trong content
# thay vì trường tool_calls chuẩn của API. Khi đó provider _parse() thấy
# tool_calls rỗng -> planner tưởng LLM đã trả lời xong và in nguyên text ra
# chat. Hàm này nhận diện 2 định dạng text phổ biến và dựng lại ToolCall:
#
#   Kiểu XML (Qwen/vLLM hermes):
#     <tool_call>
#     <function=get_health_score>
#     <parameter=start>2026-07-03T09:21:00+07:00</parameter>
#     </tool_call>
#
#   Kiểu JSON (Qwen/Hermes/nhiều model khác):
#     <tool_call>{"name": "get_health_score", "arguments": {"start": "..."}}</tool_call>
# ----------------------------------------------------------------------

# <function=NAME> ... </function> hoặc kết thúc bằng </tool_call>
_RE_FN_BLOCK = re.compile(
    r'<function=([^>\s]+)\s*>(.*?)(?:</function>|</tool_call>|$)',
    re.DOTALL,
)
# <parameter=KEY>VALUE</parameter>
_RE_PARAM = re.compile(r'<parameter=([^>\s]+)\s*>(.*?)</parameter>', re.DOTALL)
# <tool_call>{...json...}</tool_call> — bóc phần JSON bên trong
_RE_TOOL_JSON = re.compile(r'<tool_call>\s*(\{.*?\})\s*</tool_call>', re.DOTALL)


def _coerce_scalar(text: str):
    """Ép chuỗi tham số về kiểu hợp lý (int/float/bool) nếu có thể, giữ nguyên
    nếu không. Kiểu XML chỉ cho ra chuỗi nên cần đoán nhẹ để tool nhận đúng type."""
    s = (text or '').strip()
    low = s.lower()
    if low in ('true', 'false'):
        return low == 'true'
    if low in ('null', 'none'):
        return None
    try:
        return int(s)
    except (TypeError, ValueError):
        pass
    try:
        return float(s)
    except (TypeError, ValueError):
        pass
    return s


def extract_text_tool_calls(content: str) -> Tuple[List[ToolCall], str]:
    """Bóc tool-call dạng text trong content.

    Trả về (tool_calls, content_còn_lại). content_còn_lại là phần văn bản sau
    khi đã gỡ bỏ các khối tool-call (dùng làm assistant content cho lượt kế).
    Không tìm thấy -> ([], content nguyên gốc).
    """
    if not content or '<tool_call' not in content and '<function=' not in content:
        return [], content

    tool_calls: List[ToolCall] = []
    idx = 0

    # 1) Ưu tiên kiểu JSON trong <tool_call>...</tool_call>
    for m in _RE_TOOL_JSON.finditer(content):
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        name = obj.get('name') or obj.get('function') or ''
        args = obj.get('arguments')
        if isinstance(args, str):
            try:
                args = json.loads(args or '{}')
            except json.JSONDecodeError:
                args = {}
        if name:
            tool_calls.append(ToolCall(
                id='call_text_%d_%s' % (idx, name), name=name, arguments=args or {},
            ))
            idx += 1

    # 2) Kiểu XML <function=...><parameter=...>
    if not tool_calls:
        for m in _RE_FN_BLOCK.finditer(content):
            name = (m.group(1) or '').strip()
            body = m.group(2) or ''
            args = {k.strip(): _coerce_scalar(v) for k, v in _RE_PARAM.findall(body)}
            if name:
                tool_calls.append(ToolCall(
                    id='call_text_%d_%s' % (idx, name), name=name, arguments=args,
                ))
                idx += 1

    if not tool_calls:
        return [], content

    _logger.info('SmartSolar AI: TEXT TOOL-CALL detected (model phát tool-call dạng '
                 'text, không phải native): %s', [(t.name, t.arguments) for t in tool_calls])

    # Gỡ toàn bộ khối tool-call khỏi content để phần text còn lại sạch.
    leftover = _RE_TOOL_JSON.sub('', content)
    leftover = re.sub(r'<tool_call>.*?</tool_call>', '', leftover, flags=re.DOTALL)
    leftover = _RE_FN_BLOCK.sub('', leftover)
    leftover = re.sub(r'</?tool_call>', '', leftover)
    return tool_calls, leftover.strip()
