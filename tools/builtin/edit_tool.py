"""Edit 工具：修改文件内容（必须先 read，带乐观锁与原子写入；路径经工作空间校验）。"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from tools.base import BaseTool, ToolResult
from tools.workspace import Workspace, WorkspaceError, workspace_error_result


class EditTool(BaseTool):
    """把文件中的 old_string 替换为 new_string。

    读后写 + 乐观锁 + 原子写入三件套：
    - 乐观锁：写入前比对 read 时记录的指纹，内容哈希为主、mtime/size 兜底；
      哈希能识破"mtime/size 被还原但内容不同"的 ABA 类伪装；
    - 原子写入：临时文件 + os.replace，绝不留半写文件。
    注意：乐观锁是"冲突检测"而非互斥锁，校验与替换之间有一个并发窗口，
    最坏结果是覆盖并发改动（丢失更新），但不会损坏文件。
    """

    name = "edit"
    description = "把文件中的一段文本替换为另一段（修改前必须先 read）"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要修改的文件路径"},
            "old_string": {"type": "string", "description": "要替换的原文"},
            "new_string": {"type": "string", "description": "替换后的新文本"},
        },
        "required": ["path", "old_string", "new_string"],
    }

    def __init__(self, workspace: Workspace | None = None):
        self.workspace = workspace or Workspace(Path.cwd())

    def _run(self, arguments: dict) -> ToolResult:
        try:
            path = self.workspace.resolve(str(arguments.get("path", "")))
        except WorkspaceError as exc:
            return workspace_error_result(exc)
        old_string = str(arguments.get("old_string", ""))
        new_string = str(arguments.get("new_string", ""))
        # 乐观锁参数由注册中心注入（来自最近一次成功 read）
        expected_hash = arguments.get("content_hash")
        expected_mtime_ms = arguments.get("mtime_ms")
        expected_size = arguments.get("size_bytes")

        if not path.is_file():
            return ToolResult.failure(code="FILE_NOT_FOUND", message=f"文件不存在: {path}")

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult.failure(code="DECODE_ERROR", message="文件不是 UTF-8 文本，无法编辑")

        # 乐观锁（冲突检测，非互斥锁）：read 后文件被外部改过则拒绝写入
        conflict = self._check_conflict(
            content, expected_hash, expected_mtime_ms, expected_size, path
        )
        if conflict is not None:
            return conflict

        if old_string not in content:
            return ToolResult.failure(
                code="OLD_NOT_FOUND",
                message="未找到要替换的原文，请核对内容",
            )

        new_content = content.replace(old_string, new_string, 1)
        # 原子写入：先写同目录临时文件，再 os.replace 覆盖
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(new_content)
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

        new_stat = path.stat()
        return ToolResult.success(
            data={
                "path": str(path),
                "size_bytes": new_stat.st_size,
                "mtime_ms": int(new_stat.st_mtime * 1000),
                "content_hash": _content_hash(new_content),
            },
            text=f"已修改 {path}",
        )

    def _check_conflict(
        self,
        content: str,
        expected_hash: object,
        expected_mtime_ms: object,
        expected_size: object,
        path: Path,
    ) -> ToolResult | None:
        """校验文件是否在 read 后被改动；内容哈希为主，mtime/size 兜底。"""
        if expected_hash is not None:
            # 权威校验：内容不一致即冲突，能识破 mtime/size 被还原的 ABA 伪装
            if _content_hash(content) != expected_hash:
                return ToolResult.failure(
                    code="CONFLICT",
                    message="文件在读取后被修改，请重新 read 后再编辑",
                )
            return None
        if expected_mtime_ms is not None and expected_size is not None:
            stat = path.stat()
            mtime_match = int(stat.st_mtime * 1000) == int(expected_mtime_ms)
            size_match = stat.st_size == int(expected_size)
            if not (mtime_match and size_match):
                return ToolResult.failure(
                    code="CONFLICT",
                    message="文件在读取后被修改，请重新 read 后再编辑",
                )
        return None


def _content_hash(content: str) -> str:
    """计算文本内容的 SHA-256 指纹（与 read 用同一规则：UTF-8 归一化文本）。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()