"""上下文拼装协调器：把 L1 系统规则 / L2 项目规则 / L3 会话动态组成完整消息列表。

同时提供 token 估算、水位检测与截断式 compact，为后续 LLM 摘要式 compact 预留位置。
"""

from __future__ import annotations

import platform
import sys
from datetime import datetime
from pathlib import Path

from core.config import ContextConfig
from core.message import Message, system
from runtime.context.budget import estimate_messages_tokens
from runtime.context.project import ProjectRulesLoader
from runtime.context.repomap import RepoMapBuilder

# 中文星期名，用于环境信息里的日期展示
_WEEKDAYS = ("一", "二", "三", "四", "五", "六", "日")

# compact 折叠占位提示：告知模型历史被压缩，避免其假设旧信息仍然成立
_COMPACT_PLACEHOLDER = (
    "（为控制上下文长度，已折叠更早的对话记录；"
    "如需旧信息请重新用工具查询，不要假设被折叠的内容仍然成立。）"
)


class ContextBuilder:
    """上下文工程入口：按 L1 → L2 → L3 顺序组装消息，并管理水位与 compact。"""

    def __init__(self, cwd: str | Path | None = None, config: ContextConfig | None = None) -> None:
        self.cwd = Path(cwd or Path.cwd())
        self.config = config or ContextConfig()
        self._loader = ProjectRulesLoader(self.cwd)
        self._system_text: str | None = None

    # ---- L1 系统规则（全局固定）----

    def env_block(self) -> str:
        """L1 环境事实：当前时间、工作目录、平台与 Python 版本。"""
        now = datetime.now().astimezone()
        return (
            "## 环境信息\n"
            f"- 当前时间：{now:%Y-%m-%d %H:%M}（星期{_WEEKDAYS[now.weekday()]}）\n"
            f"- 工作目录：{self.cwd}\n"
            f"- 平台：{sys.platform}\n"
            f"- Python 版本：{platform.python_version()}"
        )

    # ---- L2 项目规则（按仓库）----

    def project_block(self) -> str | None:
        """L2 项目规则文本；未配置或未找到规则文件时返回 None。"""
        if not self.config.project_files:
            return None
        return self._loader.load()

    def repo_map_block(self) -> str | None:
        """L2 文件地图文本；未配置时返回 None。"""
        if not self.config.repo_map:
            return None
        return RepoMapBuilder(self.cwd).build(self.config.repo_map_max_lines)

    def system_block(self) -> str:
        """组装 L1 + L2 为一条 system 文本（结果缓存，会话内只算一次）。"""
        if self._system_text is not None:
            return self._system_text
        parts = [load_system_prompt(), self.env_block()]
        project = self.project_block()
        if project:
            parts.append(project)
        repo_map = self.repo_map_block()
        if repo_map:
            parts.append(f"## 文件地图\n{repo_map}")
        self._system_text = "\n\n".join(parts)
        return self._system_text

    # ---- L3 会话动态 ----

    def build(self, task: str, history: list[Message] | None = None) -> list[Message]:
        """拼装完整消息：system（L1+L2）→ 历史（如有）→ 本轮任务（L3）。"""
        messages = [system(self.system_block())]
        if history:
            messages.extend(history)
        messages.append(Message(role="user", content=task))
        return messages

    # ---- 水位检测与 compact ----

    def estimate_tokens(self, messages: list[Message]) -> int:
        """估算整组消息的 token 数。"""
        return estimate_messages_tokens(messages)

    def needs_compact(self, messages: list[Message]) -> bool:
        """水位检测：估算 token 达到预算 * compact_ratio 即触发压缩。"""
        trigger = int(self.config.max_tokens * self.config.compact_ratio)
        return self.estimate_tokens(messages) >= trigger

    def compact(self, messages: list[Message]) -> list[Message]:
        """截断式 compact：保留 system 与最近 keep_turns 个原子单元，旧的折叠为占位。

        原子单元 = 单条普通消息，或 assistant(工具调用) + 其全部 tool 结果的一组，
        保证不会把 tool 结果与其调用拆开（避免违反接口的消息顺序约束）。
        无可折叠内容时原样返回原列表，便于调用方判断是否发生了压缩。
        """
        system_msgs = [m for m in messages if m.role == "system"]
        body = [m for m in messages if m.role != "system"]
        units: list[list[Message]] = []
        current: list[Message] = []
        pending = 0  # 尚未闭合的工具调用数
        for msg in body:
            current.append(msg)
            if msg.role == "assistant" and msg.tool_calls:
                pending += len(msg.tool_calls)
            elif msg.role == "tool":
                pending = max(0, pending - 1)
            if pending == 0:
                units.append(current)
                current = []
        if current:  # 未闭合的残尾（正常不应出现），兜底成独立单元
            units.append(current)
        kept_units = units[-self.config.keep_turns:]
        dropped = len(units) - len(kept_units)
        if dropped <= 0:
            return messages
        kept: list[Message] = []
        for unit in kept_units:
            kept.extend(unit)
        placeholder = Message(role="user", content=_COMPACT_PLACEHOLDER)
        return system_msgs + [placeholder] + kept


DEFAULT_SYSTEM_PROMPT = """你是一个运行在本地代码仓库中的编程助手。
工作流程：
1. 先收集证据（glob / grep / read）再下结论，不要凭空猜测文件名。
2. 互不依赖的工具调用可同时发起，尽量在一个回合批量完成。
3. 修改文件前必须先 read；遇到 error 根据错误码换策略，不要重复同样失败的调用。
4. 完成后用中文总结；若无法完成也要如实说明卡在哪里。"""


def load_system_prompt() -> str:
    """读取 prompts/system.md；文件缺失时返回内置默认提示词。"""
    path = Path(__file__).resolve().parents[2] / "prompts" / "system.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_SYSTEM_PROMPT