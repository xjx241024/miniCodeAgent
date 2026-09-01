"""one_shot 单轮入口测试：execute_once 与 main 的参数接线。"""

from app import one_shot
from core.llm import ChatResponse
from tools.builtin.glob_tool import GlobTool
from tools.registry import ToolRegistry


class FakeLLM:
    """离线假模型：直接返回预设回答。"""

    def __init__(self, responses=None):
        self.responses = list(responses) if responses else [ChatResponse(content="你好")]

    def chat(self, messages, tools=None):
        if self.responses:
            return self.responses.pop(0)
        return ChatResponse(content="done")


def _make_registry():
    registry = ToolRegistry()
    registry.register(GlobTool())
    return registry


def test_execute_once_writes_trace_and_transcript(tmp_path):
    """execute_once 一次执行结束，并落盘 trace 与会话记录。"""
    trace_path = tmp_path / "trace.jsonl"
    transcript_path = tmp_path / "session.jsonl"
    result = one_shot.execute_once(
        FakeLLM(),
        _make_registry(),
        "打个招呼",
        session_id="s1",
        trace_path=trace_path,
        transcript_path=transcript_path,
    )
    assert result.success is True
    assert trace_path.is_file()
    assert transcript_path.is_file()


def test_main_runs_and_prints(tmp_path, monkeypatch, capsys):
    """main 解析参数、组装注册中心并跑通一次调用。"""

    class FakeConfig:
        """假的配置对象：只提供 main 用到的 api_key。"""

        api_key = "test-key"

    class FakeClient:
        """假的 LLMClient：上下文管理器，返回 FakeLLM。"""

        def __init__(self, config):
            self.config = config

        def __enter__(self):
            return FakeLLM()

        def __exit__(self, *exc_info):
            return None

    monkeypatch.setattr(one_shot, "load_llm_config", lambda: FakeConfig())
    monkeypatch.setattr(one_shot, "LLMClient", FakeClient)

    trace_path = tmp_path / "trace.jsonl"
    transcript_path = tmp_path / "session.jsonl"
    code = one_shot.main([
        "-p", "打个招呼",
        "--trace", str(trace_path),
        "--transcript", str(transcript_path),
    ])
    assert code == 0
    output = capsys.readouterr().out
    assert "完成" in output
    assert trace_path.is_file()