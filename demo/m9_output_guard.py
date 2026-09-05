"""M9 演示：输出治理（超长输出全文落盘 + 预览提示）与 usage 水位（M9）。

不调用真实模型，展示：工具输出超阈值如何被治理（正文进 artifact、
模型只看预览 + 精读提示），以及模型实测 token 如何校准上下文水位。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from core.config import ContextConfig
from core.message import system
from runtime.context import ContextBuilder
from runtime.output_guard import OutputGuard
from tools.base import ToolResult


def _demo_governance() -> None:
    """演示输出治理：超阈值全文落盘，模型收到预览 + 路径提示。"""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        guard = OutputGuard(workspace_root=Path(tmp), data_dir=data_dir, max_chars=200)
        body = "\n".join(f"line-{i:03d}: " + "x" * 20 for i in range(100))
        print("==== 输出治理 ====")
        print(f"原始输出: {len(body)} 字符 / {100} 行（超过阈值 200 字符）")
        guarded = guard.guard("bash", ToolResult.success(data={}, text=body), session_id="demo")
        print(f"治理后文本长度: {len(guarded.text)} 字符")
        print(f"预览开头: {guarded.text.splitlines()[0]}")
        print(f"含落盘提示: {'完整内容已保存到' in guarded.text}")
        print(f"artifact 目录: {guard.artifact_dir}")
        for path in guard.artifact_dir.glob("*.txt"):
            print(f"落盘文件: {path.name}（{path.stat().st_size} 字节，正文完整）")


def _demo_usage_watermark() -> None:
    """演示 usage 水位：实测 token 比纯估算更早触发 compact。"""
    with tempfile.TemporaryDirectory() as tmp:
        print("\n==== usage 水位 ====")
        builder = ContextBuilder(
            Path(tmp), ContextConfig(max_tokens=200, compact_ratio=0.8)
        )
        messages = [system("x" * 120)]
        print(f"纯估算水位: needs_compact = {builder.needs_compact(messages)}")
        builder.note_usage({"prompt_tokens": 170}, messages)
        print(f"记录实测 170 token 后: needs_compact = {builder.needs_compact(messages)}")


def main() -> None:
    _demo_governance()
    _demo_usage_watermark()


if __name__ == "__main__":
    main()