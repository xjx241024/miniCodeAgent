"""交互式命令行：输入任务 → Agent 执行 → 实时展示工具调用与最终回答。"""

from __future__ import annotations

import logging
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from core.config import load_context_config, load_llm_config
from core.llm import LLMClient
from memory.trace import default_trace_path, new_session_id
from memory.transcript import default_transcript_path
from runtime.context import ContextBuilder
from runtime.loop import AgentLoop
from tools.builtin.bash_tool import BashTool
from tools.builtin.edit_tool import EditTool
from tools.builtin.glob_tool import GlobTool
from tools.builtin.grep_tool import GrepTool
from tools.builtin.read_tool import ReadTool
from tools.registry import ToolRegistry

console = Console()

# 默认最大工具调用轮数；配合超限"强制总结"，任务未完成也能给出阶段性结果
DEFAULT_MAX_STEPS = 20


def build_registry() -> ToolRegistry:
    """组装并注册内置工具：4 个高频原子工具 + Bash 低频兜底。"""
    registry = ToolRegistry()
    registry.register(GlobTool())
    registry.register(GrepTool())
    registry.register(ReadTool())
    registry.register(EditTool())
    registry.register(BashTool())
    return registry


def on_tool_event(kind: str, name: str, payload) -> None:
    """把工具调用过程实时打印到终端（观察者回调）。"""
    if kind == "tool_start":
        console.print(f"[cyan]→ 调用工具[/] [bold]{name}[/] {payload}")
    else:
        status = getattr(payload, "status", "?")
        console.print(f"[green]← {name}[/] 状态: {status}")


def _result_panel(result) -> Panel:
    """按结果类型渲染面板：完成 / 未完成（阶段性总结）/ 出错。"""
    if result.reason == "completed":
        return Panel(result.answer, title="最终回答", border_style="green")
    if result.partial:
        return Panel(
            result.answer,
            title="未完成（已达步数上限）—— 阶段性总结",
            border_style="yellow",
        )
    return Panel(result.answer, title=f"未完成: {result.reason}", border_style="red")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    config = load_llm_config()
    if not config.api_key:
        console.print("[red]未配置 LLM_API_KEY，请复制 .env.example 为 .env 并填写。[/]")
        return

    registry = build_registry()
    # 上下文构建器：负责 L1 系统规则 / L2 项目规则 / L3 会话动态的拼装与水位 compact
    context_builder = ContextBuilder(Path.cwd(), load_context_config())
    console.print(Panel("输入任务开始执行；/exit 退出；Ctrl-C 中断", title="JobAgent CLI"))

    with LLMClient(config) as client:
        while True:
            try:
                task = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]已退出。[/]")
                return
            if not task:
                continue
            if task in ("/exit", "/quit"):
                return
            if task == "/help":
                console.print("输入任务即可执行；/exit 退出。")
                continue

            # 每次任务独立会话：trace / transcript 自动落到 memory/ 下
            session_id = new_session_id()
            loop = AgentLoop(
                client,
                registry,
                max_steps=DEFAULT_MAX_STEPS,
                on_tool_event=on_tool_event,
                session_id=session_id,
                trace_path=default_trace_path(session_id),
                transcript_path=default_transcript_path(session_id),
                context_builder=context_builder,
            )
            result = loop.run(task)
            console.print(_result_panel(result))
            console.print(f"[dim]trace: {result.trace_path}[/]")


if __name__ == "__main__":
    main()