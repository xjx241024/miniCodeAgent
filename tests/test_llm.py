"""LLMClient 的单元测试：用 MockTransport 模拟 HTTP，不触网。"""

import json

import httpx
import pytest

from core.config import LLMConfig
from core.llm import LLMClient, LLMError
from core.message import user


def _make_client(handler, **config_overrides) -> LLMClient:
    """构造注入 MockTransport 的客户端（可覆盖配置便于测重试）。"""
    config = LLMConfig(
        api_key="test-key",
        base_url="https://example.com/v1",
        **config_overrides,
    )
    return LLMClient(config, transport=httpx.MockTransport(handler))


def test_chat_success():
    """正常返回：正确解析内容、模型名与用量。"""

    def handler(request: httpx.Request) -> httpx.Response:
        # 校验请求头与请求体
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = request.read()
        assert b"gpt-4o-mini" in payload  # 请求应带上配置里的模型 id
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "你好！"}}],
            "model": "mock-model",
            "usage": {"total_tokens": 10},
        })

    client = _make_client(handler)
    try:
        reply = client.chat([user("hi")])
    finally:
        client.close()
    assert reply.content == "你好！"
    assert reply.model == "mock-model"
    assert reply.usage["total_tokens"] == 10


def test_chat_http_error_raises_llm_error():
    """非 2xx 响应应抛出带状态码的 LLMError。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid api key")

    client = _make_client(handler)
    try:
        with pytest.raises(LLMError) as exc_info:
            client.chat([user("hi")])
    finally:
        client.close()
    assert exc_info.value.status_code == 401
    assert "invalid api key" in exc_info.value.body


def test_chat_stream_yields_deltas():
    """流式调用：按 SSE 逐段产出文本，并在 [DONE] 处结束。"""
    sse = (
        'data: {"choices":[{"delta":{"content":"你"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"好"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        assert payload.get("stream") is True  # 流式请求应带 stream: true
        return httpx.Response(200, text=sse, headers={"Content-Type": "text/event-stream"})

    client = _make_client(handler)
    try:
        chunks = list(client.chat_stream([user("hi")]))
    finally:
        client.close()
    assert chunks == ["你", "好"]


def test_retry_on_server_error():
    """5xx 可重试：前两次 503，第三次成功，共 3 次请求。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "恢复成功"}}],
            "model": "mock-model",
        })

    client = _make_client(handler, max_retries=2, retry_backoff_seconds=0)
    try:
        reply = client.chat([user("hi")])
    finally:
        client.close()
    assert reply.content == "恢复成功"
    assert calls["n"] == 3


def test_retry_exhausted_raises():
    """重试耗尽后抛出最后一次异常。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="unavailable")

    client = _make_client(handler, max_retries=1, retry_backoff_seconds=0)
    try:
        with pytest.raises(LLMError) as exc_info:
            client.chat([user("hi")])
    finally:
        client.close()
    assert exc_info.value.status_code == 503
    assert calls["n"] == 2  # 初始 1 次 + 重试 1 次


def test_retry_skipped_for_client_error():
    """4xx 业务错误（如 401）不应重试，只请求一次。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="invalid api key")

    client = _make_client(handler, max_retries=3, retry_backoff_seconds=0)
    try:
        with pytest.raises(LLMError):
            client.chat([user("hi")])
    finally:
        client.close()
    assert calls["n"] == 1

def test_chat_with_tools_returns_tool_calls():
    """模型返回工具调用时，应解析成结构化的 ToolCall 列表。"""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        assert "tools" in payload  # 请求应携带工具 schema
        return httpx.Response(200, json={
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read", "arguments": '{"path": "a.py"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "model": "mock-model",
        })

    client = _make_client(handler)
    try:
        tools_schema = [{"type": "function", "function": {"name": "read"}}]
        reply = client.chat([user("hi")], tools=tools_schema)
    finally:
        client.close()
    assert reply.finish_reason == "tool_calls"
    assert len(reply.tool_calls) == 1
    assert reply.tool_calls[0].function.name == "read"
    assert reply.tool_calls[0].function.arguments == '{"path": "a.py"}'