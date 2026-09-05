"""Glob 工具：按通配符模式在目录中查找文件（目录经工作空间校验）。"""

from __future__ import annotations

from pathlib import Path

from tools.base import BaseTool, ToolResult
from tools.workspace import Workspace, WorkspaceError, workspace_error_result

# 常见依赖/构建噪声目录（参考 MyCodeAgent 的 DEFAULT_IGNORED_NAMES），
# include_ignored=False 时 glob 默认跳过它们，避免把 .venv 等目录算进"工作区"
_DEFAULT_IGNORED_NAMES = frozenset(
    {
        ".git", ".hg", ".svn", ".mypy_cache", ".pytest_cache", ".ruff_cache",
        ".tox", ".venv", ".idea", ".vscode", "__pycache__",
        "build", "dist", "node_modules", "target", "venv",
    }
)


def _as_bool(value: object) -> bool:
    """宽松解析布尔参数（注册中心已做类型清洗，这里兜底字符串写法）。"""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return bool(value)


def _is_visible(rel_parts: tuple[str, ...], include_hidden: bool, include_ignored: bool) -> bool:
    """路径段中出现隐藏项或忽略项时按开关过滤。

    只判断相对搜索起点的每一段：隐藏目录以 . 开头；噪声目录在忽略名单里。
    模型要精确统计"工作区 .py 数"时，默认排除 .venv 等是合理的（可开关恢复）。
    """
    for part in rel_parts:
        if not include_hidden and part.startswith("."):
            return False
        if not include_ignored and part in _DEFAULT_IGNORED_NAMES:
            return False
    return True


class GlobTool(BaseTool):
    """按模式（如 **/*.py）查找文件，返回相对起始目录的路径列表。

    pattern 只写"相对 path 的模式"，不要带目录前缀——目录放在 path 参数里，
    避免模型把目录误拼进 pattern 导致匹配为空；越界匹配（../ 逃逸）会被过滤。
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
            "include_hidden": {
                "type": "boolean",
                "description": "是否包含点开头（隐藏）文件与目录，默认 False",
                "default": False,
            },
            "include_ignored": {
                "type": "boolean",
                "description": (
                    "是否包含常见依赖/构建目录（.venv、node_modules、__pycache__、"
                    "build、dist 等），默认 False"
                ),
                "default": False,
            },
        },
        "required": ["pattern"],
    }

    def __init__(self, workspace: Workspace | None = None):
        self.workspace = workspace or Workspace(Path.cwd())

    def _run(self, arguments: dict) -> ToolResult:
        pattern = str(arguments.get("pattern", ""))
        include_hidden = _as_bool(arguments.get("include_hidden", False))
        include_ignored = _as_bool(arguments.get("include_ignored", False))
        try:
            root = self.workspace.resolve(str(arguments.get("path", ".")))
        except WorkspaceError as exc:
            return workspace_error_result(exc)
        if not root.is_dir():
            return ToolResult.failure(code="NOT_FOUND", message=f"目录不存在: {root}")
        # 过滤越界匹配：pattern 可能带 ../ 逃逸，逐个校验是否仍在工作空间内
        matches: list[str] = []
        for p in root.glob(pattern):
            if not self.workspace.is_within(p):
                continue
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            # 默认跳过隐藏项与噪声目录（可开关）；as_posix() 统一为正斜杠
            if not _is_visible(rel.parts, include_hidden, include_ignored):
                continue
            matches.append(rel.as_posix())
        matches = sorted(matches)
        text = f"找到 {len(matches)} 个匹配文件"
        if matches:
            # 模型需要看到具体路径，才能据此 read / 进一步定位
            text += "：\n" + "\n".join(matches)
        return ToolResult.success(
            data={"paths": matches},
            text=text,
        )