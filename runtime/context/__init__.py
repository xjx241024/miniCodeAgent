"""上下文工程包：L1 系统规则 / L2 项目规则 / L3 会话动态的分层拼装与水位 compact。

对外主要暴露 ContextBuilder 作为唯一入口，其余模块为内部实现。
"""

from runtime.context.budget import estimate_messages_tokens, estimate_text_tokens
from runtime.context.builder import ContextBuilder

__all__ = ["ContextBuilder", "estimate_text_tokens", "estimate_messages_tokens"]