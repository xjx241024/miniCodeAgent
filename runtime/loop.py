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
from runtime.context.builder import ContextBuilder, load_system_prompt
from runtime.output_guard import OutputGuard
from runtime.state import AgentRunResult, StepRecord
from tools.base import ToolResult
from tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# 事件回调：收到工具调用开始/结束通知（供 CLI / demo 实时展示）
ToolEventCallback = Callable[[str, str, Any], None]

# trace 里单段文本的最大长度，避免超大工具输出撑爆 trace 文件
TRACE_TEXT_LIMIT = 2000

# 同一工具同参数连续失败达到该次数时，注入"换策略"提示
REPEATED_FAILURE_LIMIT = 2


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
        max_steps: int = 20,
        system_prompt: str | None = None,
        on_tool_event: ToolEventCallback | None = None,
        session_id: str | None = None,
        trace_path: str | Path | None = None,
        transcript_path: str | Path | None = None,
        context_builder: ContextBuilder | None = None,
        streaming: bool = False,
        on_text_delta: Callable[[str], None] | None = None,
        output_guard: OutputGuard | None = None,
        data_dir: str | Path | None = None,
    ):
        self.llm = llm
        self.registry = registry
        self.max_steps = max_steps
        self.system_prompt = system_prompt or load_system_prompt()
        self.context_builder = context_builder
        self.on_tool_event = on_tool_event
        self.session_id = session_id
        self.trace_path = str(trace_path) if trace_path else None
        self.transcript_path = str(transcript_path) if transcript_path else None
        self.streaming = streaming
        self.on_text_delta = on_text_delta
        # 输出治理：未注入时默认治理（data_dir 与 trace/transcript 同目录保持一致）
        self.output_guard = output_guard or OutputGuard(
            workspace_root=Path.cwd(), data_dir=data_dir
        )
        # 打转检测：同一工具同参数连续失败的次数
        self._failure_counts: dict[str, int] = {}

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
        result.messages = messages  # 把本轮最终消息回传，供会话层累积历史
        result.session_id = session_id
        result.trace_path = session.trace_path
        result.transcript_path = session.transcript_path
        return result

    def _build_messages(self, task: str, history: list[Message] | None) -> list[Message]:
        """组装本轮消息：优先用上下文构建器，否则退回简单拼装。"""
        if self.context_builder is not None:
            return self.context_builder.build(task, history)
        if history:
            return list(history) + [Message(role="user", content=task)]
        return [system(self.system_prompt), Message(role="user", content=task)]

    def _loop(self, messages, trace, session) -> AgentRunResult:
        for step in range(1, self.max_steps + 1):
            # 每轮发请求前做水位检测，超阈值先压缩历史再继续
            if self.context_builder is not None and self.context_builder.needs_compact(messages):
                before = len(messages)
                compacted = self.context_builder.compact(messages)
                if compacted is not messages:
                    messages[:] = compacted  # 原地替换，让 run() 拿到压缩后的最终列表
                if len(messages) != before:
                    record = StepRecord(
                        step=step,
                        kind="context",
                        detail="上下文超水位，已折叠更早对话",
                    )
                    trace.append(record)
                    session.log_step(record)
            response = self._complete(messages, tools=self.registry.schemas())
            # 用模型实测 token 校准水位：下一轮预测 = 本轮实测 + 新增估算
            if self.context_builder is not None:
                self.context_builder.note_usage(response.usage, messages)

            if not response.tool_calls:
                # 模型直接给出最终回答，任务完成
                final_msg = Message(role="assistant", content=response.content)
                messages.append(final_msg)  # 最终回答也进消息列表，供会话层累积完整历史
                record = StepRecord(step=step, kind="answer", detail=response.content)
                trace.append(record)
                session.log_step(record)
                session.log_message(final_msg)
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
                # 超长输出：全文落盘 artifact，模型只看到预览 + 精读提示
                result = self.output_guard.guard(
                    call.function.name, result, session_id=session.session_id
                )
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
                tool_text = result.text or ""
                if not tool_text and result.error:
                    tool_text = f"{result.error.code}: {result.error.message}"
                tool_msg = Message(
                    role="tool",
                    content=tool_text,
                    tool_call_id=call.id,
                    name=call.function.name,
                )
                messages.append(tool_msg)
                session.log_message(tool_msg)
                # 打转检测：同一工具同参数连续失败达到阈值，注入"换策略"提示
                self._track_repeated_failure(
                    call.function.name, arguments, result, messages, session
                )

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
            summary = self._complete(messages).content
        except Exception:
            summary = "达到最大步数仍未完成任务。"
        summary_msg = Message(role="assistant", content=summary)
        messages.append(summary_msg)  # 强制总结同样回传，保证 result.messages 完整
        final_record = StepRecord(step=self.max_steps + 1, kind="answer", detail=summary)
        trace.append(final_record)
        session.log_step(final_record)
        session.log_message(summary_msg)
        return AgentRunResult(
            success=False,
            answer=summary,
            steps_used=self.max_steps + 1,
            max_steps=self.max_steps,
            reason="max_steps",
            partial=True,
            trace=trace,
        )

    def _complete(self, messages: list[Message], tools: list[dict] | None = None) -> Any:
        """发起一轮模型调用；开启流式且模型支持流式聚合时用流式（便于实时展示）。"""
        if self.streaming and hasattr(self.llm, "chat_stream_response"):
            return self.llm.chat_stream_response(messages, tools=tools, on_delta=self.on_text_delta)
        return self.llm.chat(messages, tools=tools)

    def _track_repeated_failure(self, name, arguments, result, messages, session) -> None:
        """打转检测：工具连续失败时追加一条"换策略"提示，避免模型原地打转。"""
        key = _call_key(name, arguments)
        if result.status != "error":
            self._failure_counts.pop(key, None)
            return
        count = self._failure_counts.get(key, 0) + 1
        self._failure_counts[key] = count
        if count < REPEATED_FAILURE_LIMIT:
            return
        self._failure_counts[key] = 0  # 已提示过，重置计数避免无限刷屏
        hint = Message(
            role="user",
            content=(
                f"（系统提示：工具 {name} 已连续失败 {count} 次，"
                f"参数 {json.dumps(arguments, ensure_ascii=False)}。"
                "请不要再重复同样的调用，换一种策略：换路径、换工具或换关键词。）"
            ),
        )
        messages.append(hint)
        session.log_message(hint)

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
        self.session_id = session_id
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


def _call_key(name: str, arguments: dict) -> str:
    """生成工具调用的稳定指纹：工具名 + 排序后的参数 JSON。"""
    return f"{name}:{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"


def _tool_payload(arguments: dict, result: ToolResult) -> dict:
    """把工具调用与结果整理成可写 JSON 的 trace 数据，超长文本截断。"""
    data = result.model_dump()
    text = data.get("text") or ""
    if len(text) > TRACE_TEXT_LIMIT:
        data["text"] = text[:TRACE_TEXT_LIMIT] + f"…(共 {len(text)} 字符，已截断)"
    return {"arguments": arguments, "result": data}




def _parse_arguments(raw: str) -> dict:
    """解析工具参数 JSON，失败返回空字典。"""
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}