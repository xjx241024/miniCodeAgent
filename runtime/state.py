"""Agent 运行状态：步骤记录与最终结果。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StepRecord:
    """记录一步的输入输出，用于可观测与排查。"""

    step: int
    kind: str   # "assistant" | "tool" | "answer" | "error"
    name: str = ""
    detail: str = ""
    # 附加数据（工具参数、结果等），供 trace 落盘使用
    payload: dict = field(default_factory=dict)


@dataclass
class AgentRunResult:
    """一次 Agent 任务的运行结果。"""

    success: bool
    answer: str
    steps_used: int
    max_steps: int
    reason: str = ""            # 结束原因：completed / max_steps / error
    partial: bool = False       # 是否只是阶段性结果（任务未完成，含强制总结）
    trace: list[StepRecord] = field(default_factory=list)
    session_id: str = ""        # 本次运行会话 id（对应 trace / transcript 文件名）
    trace_path: str = ""        # trace 落盘路径（若启用）
    transcript_path: str = ""   # 会话记录落盘路径（若启用）