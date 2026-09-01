"""消息数据结构：统一 system / user / assistant / tool 四种角色。

M1 阶段先打通 system / user / assistant 对话，tool 相关字段为 M2/M3 工具调用预留。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# 消息角色，与 OpenAI 兼容接口保持一致
Role = Literal["system", "user", "assistant", "tool"]


class FunctionCall(BaseModel):
    """模型发起的函数调用：函数名 + JSON 字符串参数。"""

    name: str
    arguments: str  # JSON 字符串，M2 阶段解析后执行对应工具


class ToolCall(BaseModel):
    """一条工具调用记录，挂在 assistant 消息上。"""

    id: str
    type: str = "function"
    function: FunctionCall


class Message(BaseModel):
    """一条对话消息。"""

    role: Role
    content: str = ""
    # ---- 工具相关字段（M2/M3 使用，M1 先预留）----
    name: str | None = None                      # 工具消息中标识工具名
    tool_call_id: str | None = None              # 工具结果消息关联的调用 id
    tool_calls: list[ToolCall] | None = None     # assistant 消息中的调用列表

    def to_api_dict(self) -> dict:
        """转成 OpenAI 兼容接口的字典：丢弃未填写的字段。"""
        return self.model_dump(exclude_none=True)


def system(content: str) -> Message:
    """快速构造系统消息。"""
    return Message(role="system", content=content)


def user(content: str) -> Message:
    """快速构造用户消息。"""
    return Message(role="user", content=content)


def assistant(content: str) -> Message:
    """快速构造助手消息。"""
    return Message(role="assistant", content=content)