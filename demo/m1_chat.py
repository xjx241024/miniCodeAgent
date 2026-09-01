"""M1 演示：与模型完成一次对话（发消息 → 收到回复）。

运行方式：在项目根目录执行 `uv run python -m demo.m1_chat`。
"""

from __future__ import annotations

import logging

from core.config import load_llm_config
from core.llm import LLMClient, LLMError
from core.message import system, user


def main() -> None:
    """加载配置、发送一轮对话并打印回复。"""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    config = load_llm_config()
    if not config.api_key:
        print("提示: 未配置 LLM_API_KEY。请复制 .env.example 为 .env 并填入 Key 后重试。")
        return

    messages = [
        system("你是一个简洁、友好的助手。"),
        user("你好，请用一句话介绍你自己。"),
    ]
    try:
        with LLMClient(config) as client:  # 上下文管理器负责释放连接
            reply = client.chat(messages)
    except LLMError as exc:
        print(f"调用失败: {exc}")
        if exc.status_code is not None:
            print(f"HTTP 状态码: {exc.status_code}")
            print(f"响应体: {exc.body}")
        return

    print(f"模型: {config.model_id}")
    print(f"回复: {reply.content}")
    print(f"用量: {reply.usage}")


if __name__ == "__main__":
    main()