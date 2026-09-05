"""Write 工具：创建新文件或整文件覆盖写入（路径经工作空间校验 + 内容长度上限）。

与 edit 的分工：edit 是"读后写 + 精确替换片段"，write 是"整文件写入"。
新建文件无需 read；覆盖已存在文件必须先 read（由注册中心前置校验），
写入前做乐观锁冲突检测，并用临时文件 + os.replace 原子替换，绝不留半截文件。
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from tools.base import BaseTool, ToolResult
from tools.workspace import Workspace, WorkspaceError, workspace_error_result

# 单次写入的内容长度上限，防止模型让 agent 一次写超大文件
MAX_CONTENT_CHARS = 100_000


class WriteTool(BaseTool):
    """创建新文件或整文件覆盖写入；覆盖已有文件前必须先 read。"""

    name = "write"
    description = "创建新文件或整文件覆盖写入（新建无需 read；覆盖已有文件前必须先 read）"
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "要写入的文件路径（可含不存在的父目录，会自动创建）",
            },
            "content": {"type": "string", "description": "要写入的完整内容"},
        },
        "required": ["path", "content"],
    }

    def __init__(self, workspace: Workspace | None = None):
        self.workspace = workspace or Workspace(Path.cwd())

    def _run(self, arguments: dict) -> ToolResult:
        try:
            path = self.workspace.resolve(str(arguments.get("path", "")))
        except WorkspaceError as exc:
            return workspace_error_result(exc)
        content = str(arguments.get("content", ""))
        if len(content) > MAX_CONTENT_CHARS:
            return ToolResult.failure(
                code="CONTENT_TOO_LONG",
                message=f"内容超过 {MAX_CONTENT_CHARS} 字符上限，请精简或分多次写入",
            )
        # 覆盖已有文件时的乐观锁：read 后文件被外部改过则拒绝（新建文件跳过）
        if path.is_file():
            try:
                current = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return ToolResult.failure(
                    code="DECODE_ERROR", message="文件不是 UTF-8 文本，无法覆盖写入"
                )
            conflict = _check_conflict(
                current,
                arguments.get("content_hash"),
                arguments.get("mtime_ms"),
                arguments.get("size_bytes"),
            )
            if conflict is not None:
                return conflict
        # 新建文件：父目录不存在则自动创建（write 语义与 edit 不同，允许直接建新文件）
        path.parent.mkdir(parents=True, exist_ok=True)
        # 原子写入：先写同目录临时文件，再 os.replace 覆盖，避免留下半截文件
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
        stat = path.stat()
        return ToolResult.success(
            data={
                "path": str(path),
                "size_bytes": stat.st_size,
                "mtime_ms": int(stat.st_mtime * 1000),
                # 内容哈希：write 后即可直接 edit（注册中心会记住这份新指纹）
                "content_hash": _content_hash(content),
            },
            text=f"已写入 {path}（{stat.st_size} 字节）",
        )


def _check_conflict(
    content: str,
    expected_hash: object,
    expected_mtime_ms: object,
    expected_size: object,
) -> ToolResult | None:
    """校验文件是否在 read 后被改动；内容哈希为主，mtime/size 兜底。"""
    if expected_hash is not None:
        if _content_hash(content) != expected_hash:
            return ToolResult.failure(
                code="CONFLICT",
                message="文件在读取后被修改，请重新 read 后再 write",
            )
        return None
    if expected_mtime_ms is not None and expected_size is not None:
        # 无内容哈希兜底时无法校验（write 新建路径无指纹），不校验
        return None
    return None


def _content_hash(content: str) -> str:
    """计算文本内容的 SHA-256 指纹（与 read 用同一规则：UTF-8 归一化文本）。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()