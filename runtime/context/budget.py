"""上下文 token 估算：纯函数，离线启发式，不依赖网络或 tiktoken。"""

from __future__ import annotations

from core.message import Message

# CJK 统一表意文字起始码点（含中文与中文标点），粗略按 1 字符约 1 token
_CJK_START = 0x2E80

# 非 CJK 字符按 4 字符约 1 token 估算
_NON_CJK_PER_TOKEN = 4

# 每条消息的角色 / 结构固定开销（估算值）
_PER_MESSAGE_OVERHEAD = 4


def estimate_text_tokens(text: str) -> int:
    """估算一段文本的 token 数：CJK 1 字约 1 token，其余 4 字符约 1 token。"""
    if not text:
        return 0
    tokens = 0.0
    for ch in text:
        tokens += 1.0 if ord(ch) >= _CJK_START else 1.0 / _NON_CJK_PER_TOKEN
    return max(1, int(tokens))


def estimate_messages_tokens(messages: list[Message]) -> int:
    """估算整组消息的 token 数：正文 + 每条消息开销 + 工具调用字段。"""
    total = 0
    for msg in messages:
        total += estimate_text_tokens(msg.content or "")
        total += _PER_MESSAGE_OVERHEAD
        if msg.tool_calls:
            for call in msg.tool_calls:
                total += estimate_text_tokens(call.function.name or "")
                total += estimate_text_tokens(call.function.arguments or "")
    return total