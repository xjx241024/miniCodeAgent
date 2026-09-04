"""Grep 工具：按正则表达式在文件或目录中搜索内容，返回带行号的匹配（路径经工作空间校验）。"""

from __future__ import annotations

import re
from pathlib import Path

from tools.base import BaseTool, ToolResult
from tools.workspace import Workspace, WorkspaceError, workspace_error_result

# 扫描时跳过的目录（相对搜索根判断），避免把依赖和缓存当源码
SKIP_DIRS = {".venv", ".git", "__pycache__", ".pytest_tmp", ".ruff_cache", "node_modules", ".idea"}


class GrepTool(BaseTool):
    """按正则模式搜索文本内容，返回匹配的文件、行号与行内容。

    path 可以是目录（递归搜索）或单个文件（只搜该文件），避免模型
    因"误传文件路径"而浪费整轮调用。
    """

    name = "grep"
    description = "按正则表达式搜索文件内容，返回带行号的匹配；path 可以是目录或单个文件"
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式，例如 class \\w+"},
            "path": {
                "type": "string",
                "description": "搜索起始目录或单个文件路径，默认为当前目录",
                "default": ".",
            },
            "max_results": {"type": "integer", "description": "最多返回的匹配数", "default": 20},
        },
        "required": ["pattern"],
    }

    def __init__(self, workspace: Workspace | None = None):
        self.workspace = workspace or Workspace(Path.cwd())

    def _run(self, arguments: dict) -> ToolResult:
        pattern = str(arguments.get("pattern", ""))
        max_results = int(arguments.get("max_results", 20))
        try:
            regex = re.compile(pattern)  # 编译正则表达式，如果表达式有误则直接返回失败
        except re.error as exc:
            return ToolResult.failure(code="INVALID_PATTERN", message=f"正则表达式错误: {exc}")
        try:
            target = self.workspace.resolve(str(arguments.get("path", ".")))
        except WorkspaceError as exc:
            return workspace_error_result(exc)

        if target.is_file():
            # 单文件搜索：path 是文件时直接在该文件内匹配
            base = target.parent
            candidates = [target]
        elif target.is_dir():
            base = target
            candidates = (p for p in target.rglob("*") if not _should_skip(p, target))
        else:
            return ToolResult.failure(
                code="NOT_FOUND",
                message=f"路径不存在: {target}，请先用 glob 确认目录或文件路径",
            )

        matches: list[dict] = []
        for path in candidates:
            if _scan_file(path, regex, base, matches, max_results):
                break
        truncated = len(matches) >= max_results
        text = f"找到 {len(matches)} 个匹配"
        if truncated:
            text += "（已达上限，已截断）"
        if matches:
            # 模型需要看到具体匹配（file:line: content），才能据此继续
            text += "：\n" + "\n".join(
                f"{m['file']}:{m['line']}: {m['text']}" for m in matches
            )
        return ToolResult.success(
            data={"matches": matches, "truncated": truncated},
            text=text,
        )


def _should_skip(path: Path, root: Path) -> bool:
    """是否跳过：目录本身或位于生成目录（.venv 等）之内。"""
    rel = path.relative_to(root).parts
    return path.is_dir() or any(part in SKIP_DIRS for part in rel)


def _scan_file(path: Path, regex, base: Path, matches: list[dict], max_results: int) -> bool:
    """搜索单个文件，把匹配追加进 matches；返回是否已达上限。"""
    if not _is_text(path):
        return False
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return False
    for i, line in enumerate(lines, start=1):
        if regex.search(line):
            matches.append({
                "file": path.relative_to(base).as_posix(),
                "line": i,
                "text": line,
            })
            if len(matches) >= max_results:
                return True
    return False


def _is_text(path: Path) -> bool:
    """粗略判断是否为文本文件：前 1KB 含 NUL 字节视为二进制。"""
    try:
        with path.open("rb") as f:
            return b"\x00" not in f.read(1024)
    except OSError:
        return False