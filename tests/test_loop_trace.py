"""AgentLoop 接入 trace/transcript 的测试：验证 JSONL 落盘与继续会话。"""

from core.llm import ChatResponse
from core.message import FunctionCall, ToolCall, assistant, user
from memory.trace import load_records
from memory.transcript import TranscriptWriter, load_messages
from runtime.loop import AgentLoop
from tools.builtin.glob_tool import GlobTool
from tools.registry import ToolRegistry


class FakeLLM:
    """按顺序返回预设响应的假模型（鸭子类型）。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        if not self.responses:
            return ChatResponse(content="done")
        return self.responses.pop(0)


def _tool_call_response(name, arguments):
    """构造一个"发起工具调用"的模型响应。"""
    call = ToolCall(id="call_1", function=FunctionCall(name=name, arguments=arguments))
    return ChatResponse(content="", tool_calls=[call], finish_reason="tool_calls")


def _make_registry():
    registry = ToolRegistry()
    registry.register(GlobTool())
    return registry


def test_loop_writes_trace(tmp_path):
    """配置 trace_path 后，工具与回答步骤都会落盘，且带参数/结果。"""
    path_str = tmp_path.as_posix()
    trace_path = tmp_path / "trace.jsonl"
    fake = FakeLLM([
        _tool_call_response("glob", f'{{"pattern": "*.py", "path": "{path_str}"}}'),
        ChatResponse(content="完成"),
    ])
    loop = AgentLoop(fake, _make_registry(), trace_path=trace_path)
    result = loop.run("找文件")
    assert result.success is True
    assert result.trace_path == str(trace_path)
    records = load_records(trace_path)
    kinds = [r["kind"] for r in records]
    assert "tool" in kinds and "answer" in kinds
    tool_record = next(r for r in records if r["kind"] == "tool")
    assert tool_record["payload"]["arguments"]["path"] == path_str
    assert tool_record["payload"]["result"]["status"] == "success"


def test_loop_writes_transcript(tmp_path):
    """配置 transcript_path 后，用户/助手/工具消息都会记录。"""
    path_str = tmp_path.as_posix()
    transcript_path = tmp_path / "session.jsonl"
    fake = FakeLLM([
        _tool_call_response("glob", f'{{"pattern": "*.py", "path": "{path_str}"}}'),
        ChatResponse(content="完成"),
    ])
    loop = AgentLoop(fake, _make_registry(), transcript_path=transcript_path)
    result = loop.run("找文件")
    assert result.success is True
    assert result.transcript_path == str(transcript_path)
    roles = [m.role for m in load_messages(transcript_path)]
    assert "user" in roles and "assistant" in roles and "tool" in roles


def test_loop_continues_from_history(tmp_path):
    """用历史消息继续会话：新任务与最终回答追加进原 transcript。"""
    transcript_path = tmp_path / "session.jsonl"
    with TranscriptWriter(transcript_path, "s1") as writer:
        writer.append(user("之前的问题"))
        writer.append(assistant("之前的回答"))
    history = load_messages(transcript_path)
    fake = FakeLLM([ChatResponse(content="新回答")])
    loop = AgentLoop(fake, _make_registry(), transcript_path=transcript_path)
    result = loop.run("新问题", history=history)
    assert result.answer == "新回答"
    messages = load_messages(transcript_path)
    assert messages[-2].role == "user" and messages[-2].content == "新问题"
    assert messages[-1].role == "assistant" and messages[-1].content == "新回答"