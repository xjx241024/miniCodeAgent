"""M3 演示：用 AgentLoop 跑通一个真实的"搜索 → 读取 → 总结"任务。"""

from __future__ import annotations

import logging

from core.config import load_llm_config
from core.llm import LLMClient
from runtime.loop import AgentLoop
from tools.builtin.glob_tool import GlobTool
from tools.builtin.grep_tool import GrepTool
from tools.builtin.read_tool import ReadTool
from tools.registry import ToolRegistry


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    config = load_llm_config()
    if not config.api_key:
        print("提示: 未配置 LLM_API_KEY，请先配置 .env。")
        return

    # 只读任务，不注册 Edit，避免演示误改代码
    registry = ToolRegistry()
    registry.register(GlobTool())
    registry.register(GrepTool())
    registry.register(ReadTool())

    def on_tool_event(kind: str, name: str, payload) -> None:
        if kind == "tool_start":
            print(f"  → 调用工具: {name} {payload}")
        else:
            print(f"  ← {name} 状态: {getattr(payload, 'status', '?')}")

    task = (
        "找出 tools/builtin 目录下的所有 .py 文件，"
        "再用 grep 找出其中定义了 class 的文件，最后读取其中一个文件的前 15 行。"
    )
    with LLMClient(config) as client:
        loop = AgentLoop(client, registry, max_steps=10, on_tool_event=on_tool_event)
        result = loop.run(task)
        print(f"\n结果: {result.answer}")
        if not result.success:
            print(f"（未完成：{result.reason}）")


if __name__ == "__main__":
    main()