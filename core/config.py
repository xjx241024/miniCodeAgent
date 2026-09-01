"""配置加载：把 .env 与系统环境变量集中成类型化配置对象。

统一配置入口：换模型 / 换 Key 只改 .env，不改业务代码。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    """LLM 连接配置（字段与 .env 一一对应）。"""

    provider: str = Field(default="openai", description="模型服务商，仅作标识")
    model_id: str = Field(default="gpt-4o-mini", description="模型 id")
    api_key: str = Field(default="", description="API Key")
    base_url: str = Field(default="https://api.openai.com/v1", description="OpenAI 兼容接口地址")
    timeout_seconds: float = Field(default=60.0, description="HTTP 请求超时（秒）")
    temperature: float = Field(default=0.2, description="采样温度，越低越稳定")
    max_retries: int = Field(default=2, description="失败重试次数上限")
    # 重试退避基数（秒），第 n 次等待 base * 2^n
    retry_backoff_seconds: float = Field(default=1.0, description="重试退避基数（秒）")


def load_llm_config(env_file: str | Path = ".env") -> LLMConfig:
    """从 .env（若存在）读取配置并返回类型化对象。

    显式传入 env_file 便于测试时指向临时文件。
    """
    env_path = Path(env_file)
    if env_path.is_file():
        load_dotenv(env_path)
    else:
        load_dotenv()  # 默认从当前工作目录找 .env
    return LLMConfig(
        provider=os.getenv("LLM_PROVIDER", "openai"),
        model_id=os.getenv("LLM_MODEL_ID", "gpt-4o-mini"),
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
        timeout_seconds=float(os.getenv("LLM_TIMEOUT", "60")),
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
        retry_backoff_seconds=float(os.getenv("LLM_RETRY_BACKOFF", "1.0")),
    )