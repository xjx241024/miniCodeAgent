"""Glob 工具：按通配符模式在目录中查找文件。"""

from __future__ import annotations

from pathlib import Path

from tools.base import BaseTool, ToolResult


class GlobTool(BaseTool):
    """按模式（如 **/*.py）查找文件，返回相对起始目录的路径列表。

    pattern 只写"相对 path 的模式"，不要带目录前缀——目录放在 path 参数里，
    避免模型把目录误拼进 pattern 导致匹配为空。
    """

    name = "glob"
    description = "按通配符模式查找文件（目录放 path，pattern 只写相对模式，例如 **/*.py）"
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "相对 path 的通配符模式，如 **/*.py；不要带目录前缀",
            },
            "path": {
                "type": "string",
                "description": "搜索起始目录，默认为当前目录",
                "default": ".",
            },
        },
        "required": ["pattern"],
    }

    def _run(self, arguments: dict) -> ToolResult:
        pattern = str(arguments.get("pattern", ""))
        path = str(arguments.get("path", "."))
        root = Path(path)
        if not root.is_dir():
            return ToolResult.failure(code="NOT_FOUND", message=f"目录不存在: {path}")
        # as_posix() 把 Windows 反斜杠统一为正斜杠，保证输出跨平台一致
        matches = sorted(p.relative_to(root).as_posix() for p in root.glob(pattern))
        text = f"找到 {len(matches)} 个匹配文件"
        if matches:
            # 模型需要看到具体路径，才能据此 read / 进一步定位
            text += "：\n" + "\n".join(matches)
        return ToolResult.success(
            data={"paths": matches},
            text=text,
        )