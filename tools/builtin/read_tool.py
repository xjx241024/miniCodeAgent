"""Read 工具：读取文件内容，带行号与文件元信息。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from tools.base import BaseTool, ToolResult


class ReadTool(BaseTool):
    """读取文件，返回带行号内容；同时返回大小、mtime 与内容哈希供 Edit 做乐观锁校验。"""

    name = "read"
    description = "读取文件内容（带行号），并返回文件大小、修改时间与内容哈希"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要读取的文件路径"},
            "max_lines": {"type": "integer", "description": "最多返回的行数", "default": 200},
        },
        "required": ["path"],
    }

    def _run(self, arguments: dict) -> ToolResult:
        path = Path(str(arguments.get("path", "")))
        if not path.is_file():
            return ToolResult.failure(code="NOT_FOUND", message=f"文件不存在: {path}")
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult.failure(code="DECODE_ERROR", message="文件不是 UTF-8 文本，无法读取")
        lines = content.splitlines()
        max_lines = int(arguments.get("max_lines", 200))
        truncated = len(lines) > max_lines
        shown = lines[:max_lines]
        # 带行号输出，方便模型引用具体行
        numbered = "\n".join(f"{i + 1:4d} | {line}" for i, line in enumerate(shown))
        text = numbered
        if truncated:
            # 提示被截断，避免模型误以为文件只有这些行
            text += f"\n（文件共 {len(lines)} 行，仅显示前 {max_lines} 行）"
        stat = path.stat()
        return ToolResult.success(
            data={
                "path": str(path),
                "size_bytes": stat.st_size,
                "mtime_ms": int(stat.st_mtime * 1000),
                # 内容哈希：read 与 edit 都按"UTF-8 文本"归一化计算，规则一致才可比对
                "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "total_lines": len(lines),
                "truncated": truncated,
            },
            text=text,
        )