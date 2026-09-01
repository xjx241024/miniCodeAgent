"""工具基类与统一响应协议。

所有工具返回相同结构 {status, data, text, error}，让模型与上层都能用同一套逻辑判断成败。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, Field

# 工具返回状态：success 成功；error 失败（含具体错误码）
ToolStatus = Literal["success", "error"]


class ToolError(BaseModel):
    """结构化错误信息，便于模型针对性纠错。"""

    code: str
    message: str


class ToolResult(BaseModel):
    """工具统一响应协议。"""

    status: ToolStatus
    data: dict = Field(default_factory=dict)
    text: str = ""
    # 注意：字段名 error 与下面的类方法不能重名，故失败构造方法命名为 failure
    error: ToolError | None = None

    @classmethod
    def success(cls, data: dict | None = None, text: str = "") -> ToolResult:
        """构造成功结果。"""
        return cls(status="success", data=data or {}, text=text)

    @classmethod
    def failure(cls, code: str, message: str) -> ToolResult:
        """构造失败结果，带错误码。"""
        return cls(status="error", error=ToolError(code=code, message=message))


class BaseTool(ABC):
    """工具基类：子类只需实现 name / description / parameters 与 _run。

    采用模板方法模式：invoke 统一处理异常包装，子类只关心具体逻辑。
    """

    name: str = ""          # 工具名（模型用这个名字发起调用）
    description: str = ""   # 工具说明（模型据此决定何时调用）
    parameters: dict = {}   # 参数 JSON Schema（OpenAI 函数调用格式）

    def schema(self) -> dict:
        """生成发给模型的函数调用 schema。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def invoke(self, arguments: dict) -> ToolResult:
        """统一入口：执行工具并包装结果；未知异常也转为 error，不让异常泄漏到上层。"""
        try:
            return self._run(arguments)
        except Exception as exc:
            return ToolResult.failure(code="TOOL_ERROR", message=str(exc))

    @abstractmethod
    def _run(self, arguments: dict) -> ToolResult:
        """子类实现具体逻辑。"""