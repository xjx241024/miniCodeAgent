"""AgentSession 交互式会话测试：跨轮次历史累积、恢复继续、流式回退。"""

from __future__ import annotations

from core.llm import ChatResponse
from core.message import assistant, user
from memory.transcript import TranscriptWriter, load_messages
from runtime.session import AgentSession
from tools.builtin.glob_tool import GlobTool
from tools.registry import ToolRegistry


class FakeLLM:
    """离线假模型：记录每次收到的消息长度，并返回预设回答。"""

    def __init__(self, responses=None):
        self.responses = list(responses) if responses else [ChatResponse(content="你好")]
        self.seen_lengths: list[int] = []

    def chat(self, messages, tools=None):
        self.seen_lengths.append(len(messages))
        if self.responses:
            return self.responses.pop(0)
        return ChatResponse(content="done")


def _make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(GlobTool())
    return registry


def test_session_accumulates_history(tmp_path):
    """同一会话内多次 ask：后续请求应携带此前全部消息（历史累积）。"""
    fake = FakeLLM([ChatResponse(content="一"), ChatResponse(content="二")])
    session = AgentSession(
        fake,
        _make_registry(),
        None,  # 不启用上下文构建器，走 loop 的简单拼装
        workspace_root=tmp_path,
        data_dir=tmp_path,
    )
    first = session.ask("问题一")
    assert first.answer == "一"
    second = session.ask("问题二")
    assert second.answer == "二"
    # 第二次请求的消息数应大于第一次（带着历史）；历史里应有 4 条非 system 消息
    assert fake.seen_lengths[1] > fake.seen_lengths[0]
    assert len(session.history) == 4


def test_session_resume_loads_history(tmp_path):
    """从既有 transcript 恢复会话：历史被载入，新消息继续写回原文件。"""
    transcript_path = tmp_path / "session.jsonl"
    with TranscriptWriter(transcript_path, "s1") as writer:
        writer.append(user("旧问题"))
        writer.append(assistant("旧回答"))
    fake = FakeLLM([ChatResponse(content="新回答")])
    session = AgentSession(
        fake,
        _make_registry(),
        None,
        workspace_root=tmp_path,
        data_dir=tmp_path,
        resume=transcript_path,
    )
    assert len(session.history) == 2
    result = session.ask("新问题")
    assert result.answer == "新回答"
    messages = load_messages(transcript_path)
    assert [m.content for m in messages[-2:]] == ["新问题", "新回答"]


def test_session_streaming_falls_back_when_no_stream_support(tmp_path):
    """模型没有流式聚合接口时，loop 自动回退到普通 chat，不触发 on_delta。"""
    fake = FakeLLM([ChatResponse(content="回答")])
    deltas: list[str] = []
    session = AgentSession(
        fake,
        _make_registry(),
        None,
        workspace_root=tmp_path,
        data_dir=tmp_path,
        streaming=True,
        on_text_delta=deltas.append,
    )
    result = session.ask("问题")
    assert result.answer == "回答"
    assert deltas == []