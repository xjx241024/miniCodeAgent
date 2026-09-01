"""AgentLoop 的单元测试：用假模型驱动，验证循环行为。"""

from core.llm import ChatResponse
from core.message import FunctionCall, ToolCall
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


def _tool_call_response(name: str, arguments: str) -> ChatResponse:
    """构造一个"发起工具调用"的模型响应。"""
    call = ToolCall(id="call_1", function=FunctionCall(name=name, arguments=arguments))
    return ChatResponse(content="", tool_calls=[call], finish_reason="tool_calls")


def _make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(GlobTool())
    return registry


def test_loop_immediate_answer():
    """模型直接回答时，一步完成。"""
    fake = FakeLLM([ChatResponse(content="你好")])
    result = AgentLoop(fake, _make_registry()).run("打个招呼")
    assert result.success is True
    assert result.answer == "你好"
    assert result.reason == "completed"
    assert fake.calls == 1


def test_loop_executes_tool_then_answers(tmp_path):
    """模型先调工具，观察结果后再回答。"""
    registry = _make_registry()
    fake = FakeLLM([
        _tool_call_response("glob", f'{{"pattern": "*.py", "path": "{tmp_path.as_posix()}"}}'),
        ChatResponse(content="完成了"),
    ])
    result = AgentLoop(fake, registry, max_steps=10).run("找文件")
    assert result.success is True
    assert result.answer == "完成了"
    assert fake.calls == 2
    # trace 里应记录了工具调用
    assert any(s.kind == "tool" and s.name == "glob" for s in result.trace)


def test_loop_stops_at_max_steps_with_partial_summary():
    """模型一直要调工具时应在 max_steps 停下，并追加一次强制总结（标记未完成）。"""
    fake = FakeLLM(
        [_tool_call_response("glob", '{"pattern": "*.py"}')] * 3
        + [ChatResponse(content="尚未完成：已找到部分文件")]
    )
    result = AgentLoop(fake, _make_registry(), max_steps=3).run("任务")
    assert result.success is False
    assert result.reason == "max_steps"
    assert result.partial is True
    assert "尚未完成" in result.answer
    assert fake.calls == 4  # 3 轮工具 + 1 次强制总结
    # trace 最后一条是总结
    assert result.trace[-1].kind == "answer"