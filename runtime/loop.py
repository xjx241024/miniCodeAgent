"""ReAct 主循环：思考 → 工具调用 → 观察 → 再思考，直到得到最终回答。

M4 起支持把每一步写入 JSONL trace、把消息写入 transcript，便于排查与继续会话。
M5 起超步数上限时追加一次"禁止工具"的强制总结，向用户如实标记任务未完成。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.message import Message, system
from memory.trace import TraceWriter, new_session_id
from memory.transcript import TranscriptWriter
from runtime.state import AgentRunResult, StepRecord
from tools.base import ToolResult
from tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# 事件回调：收到工具调用开始/结束通知（供 CLI / demo 实时展示）
ToolEventCallback = Callable[[str, str, Any], None]

# trace 里单段文本的最大长度，避免超大工具输出撑爆 trace 文件
TRACE_TEXT_LIMIT = 2000

DEFAULT_SYSTEM_PROMPT = """你是一个运行在本地代码仓库中的编程助手。
工作流程：
1. 先收集证据（glob / grep / read）再下结论，不要凭空猜测文件名。
2. 互不依赖的工具调用可同时发起，尽量在一个回合批量完成。
3. 修改文件前必须先 read；遇到 error 根据错误码换策略，不要重复同样失败的调用。
4. 完成后用中文总结；若无法完成也要如实说明卡在哪里。"""


class AgentLoop:
    """驱动"模型 ↔ 工具"循环的运行时。

    llm 只需提供 chat(messages, tools=...) 接口（鸭子类型），
    因此可以注入假模型做离线测试。

    可选 trace_path / transcript_path：传入后会把运行过程落盘，
    便于回放与"读档继续"（配合 history 参数）。
    """

    def __init__(
        self,
        llm: Any,
        registry: ToolRegistry,
        max_steps: int = 10,
        system_prompt: str | None = None,
        on_tool_event: ToolEventCallback | None = None,
        session_id: str | None = None,
        trace_path: str | Path | None = None,
        transcript_path: str | Path | None = None,
    ):
        self.llm = llm
        self.registry = registry
        self.max_steps = max_steps
        self.system_prompt = system_prompt or _load_system_prompt()
        self.on_tool_event = on_tool_event
        self.session_id = session_id
        self.trace_path = str(trace_path) if trace_path else None
        self.transcript_path = str(transcript_path) if transcript_path else None

    def run(self, task: str, *, history: list[Message] | None = None) -> AgentRunResult:
        """执行一次任务；可传入历史消息以继续之前的会话。"""
        messages = self._build_messages(task, history)
        trace: list[StepRecord] = []
        session_id = self.session_id or new_session_id()
        session = _SessionLogger(
            session_id=session_id,
            trace_path=self.trace_path,
            transcript_path=self.transcript_path,
        )
        # 把本次新增的用户任务写入 transcript（system 提示词固定，不重复记录）
        session.log_message(messages[-1])
        try:
            try:
                result = self._loop(messages, trace, session)
            except Exception as exc:
                result = AgentRunResult(
                    success=False,
                    answer=f"运行出错: {exc}",
                    steps_used=len(trace),
                    max_steps=self.max_steps,
                    reason="error",
                    trace=trace,
                )
        finally:
            session.close()
        result.session_id = session_id
        result.trace_path = session.trace_path
        result.transcript_path = session.transcript_path
        return result

    def _build_messages(self, task: str, history: list[Message] | None) -> list[Message]:
        """组装本轮消息：全新会话或基于历史继续。"""
        if history:
            return list(history) + [Message(role="user", content=task)]
        return [system(self.system_prompt), Message(role="user", content=task)]

    def _loop(self, messages, trace, session) -> AgentRunResult:
        for step in range(1, self.max_steps + 1):
            response = self.llm.chat(messages, tools=self.registry.schemas())

            if not response.tool_calls:
                # 模型直接给出最终回答，任务完成
                record = StepRecord(step=step, kind="answer", detail=response.content)
                trace.append(record)
                session.log_step(record)
                session.log_message(Message(role="assistant", content=response.content))
                return AgentRunResult(
                    success=True,
                    answer=response.content,
                    steps_used=step,
                    max_steps=self.max_steps,
                    reason="completed",
                    trace=trace,
                )

            # 记录模型发起的工具调用，并追加进消息历史
            assistant_msg = Message(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls,
            )
            messages.append(assistant_msg)
            session.log_message(assistant_msg)
            for call in response.tool_calls:
                arguments = _parse_arguments(call.function.arguments)
                self._emit("tool_start", call.function.name, arguments)
                result = self.registry.call(call.function.name, arguments)
                self._emit("tool_end", call.function.name, result)
                record = StepRecord(
                    step=step,
                    kind="tool",
                    name=call.function.name,
                    detail=f"status={result.status}",
                    payload=_tool_payload(arguments, result),
                )
                trace.append(record)
                session.log_step(record)
                # 工具结果以 role=tool 的消息回填，供模型下一轮观察
                tool_msg = Message(
                    role="tool",
                    content=result.text,
                    tool_call_id=call.id,
                    name=call.function.name,
                )
                messages.append(tool_msg)
                session.log_message(tool_msg)

        # 超过步数上限仍未完成：追加一次"禁止工具"的最终总结，向用户如实标记未完成
        summary_prompt = Message(
            role="user",
            content="你已用尽工具调用轮数但任务仍未完成。请不要再调用任何工具，"
                    "直接基于以上对话，用中文给出阶段性总结：开头明确说明任务尚未完成，"
                    "然后列出已完成的进展、遇到的问题、下一步建议。",
        )
        messages.append(summary_prompt)
        session.log_message(summary_prompt)
        try:
            summary = self.llm.chat(messages).content
        except Exception:
            summary = "达到最大步数仍未完成任务。"
        final_record = StepRecord(step=self.max_steps + 1, kind="answer", detail=summary)
        trace.append(final_record)
        session.log_step(final_record)
        session.log_message(Message(role="assistant", content=summary))
        return AgentRunResult(
            success=False,
            answer=summary,
            steps_used=self.max_steps + 1,
            max_steps=self.max_steps,
            reason="max_steps",
            partial=True,
            trace=trace,
        )

    def _emit(self, kind: str, name: str, payload: Any) -> None:
        """触发事件回调（如果有）。"""
        if self.on_tool_event is not None:
            self.on_tool_event(kind, name, payload)


class _SessionLogger:
    """把运行过程增量写入 trace 与 transcript；未配置时全部为 no-op。"""

    def __init__(
        self,
        *,
        session_id: str,
        trace_path: str | None = None,
        transcript_path: str | None = None,
    ):
        self.trace_path = trace_path
        self.transcript_path = transcript_path
        self._trace = TraceWriter(trace_path, session_id=session_id) if trace_path else None
        self._transcript = (
            TranscriptWriter(transcript_path, session_id=session_id) if transcript_path else None
        )

    def log_step(self, record: StepRecord) -> None:
        """写一步 trace；磁盘异常只告警，不影响主流程。"""
        if self._trace is None:
            return
        try:
            self._trace.write(
                step=record.step,
                kind=record.kind,
                name=record.name,
                detail=record.detail,
                payload=record.payload,
            )
        except OSError as exc:
            logger.warning("trace 写入失败: %s", exc)

    def log_message(self, message: Message) -> None:
        """追加一条消息到 transcript；磁盘异常只告警。"""
        if self._transcript is None:
            return
        try:
            self._transcript.append(message)
        except OSError as exc:
            logger.warning("transcript 写入失败: %s", exc)

    def close(self) -> None:
        """关闭底层写文件。"""
        if self._trace is not None:
            self._trace.close()
        if self._transcript is not None:
            self._transcript.close()


def _tool_payload(arguments: dict, result: ToolResult) -> dict:
    """把工具调用与结果整理成可写 JSON 的 trace 数据，超长文本截断。"""
    data = result.model_dump()
    text = data.get("text") or ""
    if len(text) > TRACE_TEXT_LIMIT:
        data["text"] = text[:TRACE_TEXT_LIMIT] + f"…(共 {len(text)} 字符，已截断)"
    return {"arguments": arguments, "result": data}


def _load_system_prompt() -> str:
    """从 prompts/system.md 读取系统提示词，缺失时用默认值。"""
    path = Path(__file__).resolve().parents[1] / "prompts" / "system.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_SYSTEM_PROMPT


def _parse_arguments(raw: str) -> dict:
    """解析工具参数 JSON，失败返回空字典。"""
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}