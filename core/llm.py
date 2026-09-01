"""OpenAI 兼容模型封装：对外只暴露 chat / chat_stream 两个接口。

支持非流式与流式（SSE）两种调用；chat 支持工具（function calling）；
失败按 HTTP 状态码分类并带指数退避重试。
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

import httpx
from pydantic import BaseModel, Field

from core.config import LLMConfig
from core.message import Message, ToolCall


class LLMError(Exception):
    """LLM 调用失败统一异常，携带可诊断信息。"""

    def __init__(self, message: str, *, status_code: int | None = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code  # HTTP 状态码；None 表示网络层错误
        self.body = body                # 响应体原文，便于排查


class ChatResponse(BaseModel):
    """一次非流式 chat 调用的结构化结果。"""

    content: str = ""
    model: str = ""
    usage: dict = Field(default_factory=dict)
    finish_reason: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)


class LLMClient:
    """Chat Completions 客户端，支持注入 transport 以便测试。"""

    def __init__(self, config: LLMConfig, transport: httpx.BaseTransport | None = None):
        self.config = config
        # transport 为 None 时使用默认网络传输；测试时注入 MockTransport
        self._client = httpx.Client(
            base_url=config.base_url,
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=config.timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        """释放连接资源（配合上下文管理器使用）。"""
        self._client.close()

    def _build_payload(self, messages: list[Message], *, stream: bool = False) -> dict:
        """构造请求体：模型名、消息列表、温度、是否流式。"""
        return {
            "model": self.config.model_id,
            "messages": [m.to_api_dict() for m in messages],
            "temperature": self.config.temperature,
            "stream": stream,
        }

    def _request_once(self, payload: dict, *, stream: bool = False) -> httpx.Response:
        """发一次请求；非 2xx 读取错误体并抛 LLMError。

        stream=True 时响应体延迟读取，由调用方负责关闭连接。
        """
        # 用 build_request + send 以便显式控制流式（Client.post 不支持 stream 参数）
        request = self._client.build_request("POST", "/chat/completions", json=payload)
        resp = self._client.send(request, stream=stream)
        if resp.status_code != 200:
            try:
                body = resp.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            resp.close()  # 流式响应出错时也要关闭连接
            raise LLMError(
                f"LLM 调用失败: HTTP {resp.status_code}",
                status_code=resp.status_code,
                body=body,
            )
        return resp

    def _is_retryable(self, exc: Exception) -> bool:
        """判断异常是否值得重试：网络层错误，或 429 / 5xx。"""
        if isinstance(exc, httpx.TransportError):
            return True  # 连接失败、超时等，可重试
        if isinstance(exc, LLMError):
            # 限流(429)与服务端错误(5xx)可重试；4xx 业务错误不重试
            code = exc.status_code
            return code is not None and (code == 429 or code >= 500)
        return False

    def _post_with_retries(self, payload: dict, *, stream: bool = False) -> httpx.Response:
        """带指数退避重试地发送请求，重试次数上限由配置决定。"""
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                return self._request_once(payload, stream=stream)
            except Exception as exc:
                last_error = exc
                if not self._is_retryable(exc):
                    raise  # 业务错误（如 401/400）不重试，直接抛出
                if attempt >= self.config.max_retries:
                    break  # 重试次数耗尽
                wait = self.config.retry_backoff_seconds * (2**attempt)
                time.sleep(wait)  # 指数退避：1s、2s、4s...
        if last_error is not None:
            raise last_error
        raise RuntimeError("unreachable")

    def chat(self, messages: list[Message], *, tools: list[dict] | None = None) -> ChatResponse:
        """非流式调用：发送一轮对话，返回完整回复。

        tools 传入工具 schema 后，模型可选择调用工具，结果体现在 tool_calls 里。
        """
        payload = self._build_payload(messages, stream=False)
        if tools:
            payload["tools"] = tools
        resp = self._post_with_retries(payload, stream=False)
        data = resp.json()
        choice = data["choices"][0]
        message = choice.get("message") or {}
        tool_calls = []
        for call in message.get("tool_calls") or []:
            tool_calls.append(ToolCall(**call))  # pydantic 归一化 dict -> 模型
        return ChatResponse(
            content=message.get("content") or "",
            model=data.get("model", ""),
            usage=data.get("usage") or {},
            finish_reason=choice.get("finish_reason") or "",
            tool_calls=tool_calls,
        )

    def chat_stream(self, messages: list[Message]) -> Iterator[str]:
        """流式调用：逐段产出回复文本，适合边生成边展示。

        SSE 协议：响应按行读取，`data: {...}` 是内容块，`data: [DONE]` 是结束标记。
        """
        payload = self._build_payload(messages, stream=True)
        resp = self._post_with_retries(payload, stream=True)
        # send(stream=True) 返回的响应没有 with 协议，需用 try/finally 手动关闭
        try:
            for line in resp.iter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue  # 跳过无法解析的行，保持流式健壮性
                delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
                if delta:
                    yield delta
        finally:
            resp.close()  # 迭代结束或异常时都释放连接

    def __enter__(self) -> LLMClient:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()