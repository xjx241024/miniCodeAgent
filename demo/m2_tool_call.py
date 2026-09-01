"""M2 演示：让模型根据任务自主选择工具，并执行一次函数调用。

流程：发 schema 给模型 → 模型返回 tool_calls → 用注册中心执行 → 结果回填给模型再要最终回答。
运行：`uv run python -m demo.m2_tool_call`（需先配置 .env）
"""

from __future__ import annotations

import json
import logging

from core.config import load_llm_config
from core.llm import LLMClient
from core.message import Message, system, user
from tools.builtin.glob_tool import GlobTool
from tools.builtin.read_tool import ReadTool
from tools.registry import ToolRegistry


def parse_arguments(raw: str) -> dict:
    """把工具参数的 JSON 字符串解析成 dict，解析失败返回空字典。"""
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    config = load_llm_config()
    if not config.api_key:
        print("提示: 未配置 LLM_API_KEY，请先复制 .env.example 为 .env 并填入 Key。")
        return

    # 注册工具：Glob 找文件、Read 读文件
    registry = ToolRegistry()
    registry.register(GlobTool())
    registry.register(ReadTool())

    task = "请找出当前项目里的所有 Python 源文件，然后读取 core/llm.py 的前 20 行。"
    messages: list[Message] = [
        system("你是一个会使用工具的代码助手。"),
        user(task),
    ]

    with LLMClient(config) as client:
        # 第一轮：带上工具 schema，让模型决定是否调用
        reply = client.chat(messages, tools=registry.schemas())
        print(f"模型: {config.model_id} | finish_reason: {reply.finish_reason}")

        if reply.tool_calls:
            # 把模型发起的调用记录进消息历史，再逐个执行
            messages.append(Message(
                role="assistant",
                content=reply.content,
                tool_calls=reply.tool_calls,
            ))
            for call in reply.tool_calls:
                result = registry.call(call.function.name, parse_arguments(call.function.arguments))
                print(f"\n--- 调用工具: {call.function.name} ---")
                print(f"状态: {result.status}")
                print(result.text)
                # 工具结果以 role=tool 的消息回填给模型
                messages.append(Message(
                    role="tool",
                    content=result.text,
                    tool_call_id=call.id,
                    name=call.function.name,
                ))
            # 第二轮：把工具结果交还模型，拿到最终回答
            final = client.chat(messages)
            print(f"\n最终回答: {final.content}")
        else:
            print(f"回答: {reply.content}")


if __name__ == "__main__":
    main()