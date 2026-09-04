"""工作空间约束：所有路径必须落在项目根内，防止工具越界操作。

安全边界的第一层：resolve() 统一解析"相对路径"与"绝对路径"，
但强制结果必须位于 root 之下，否则抛出 WorkspaceError（带错误码）。
"""

from __future__ import annotations

from pathlib import Path


class WorkspaceError(Exception):
    """路径越界或非法时抛出的异常，kind 便于上层映射为工具错误码。"""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


class Workspace:
    """把一个目录固定为可操作范围，所有文件工具经它解析路径。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def resolve(self, requested: str | Path) -> Path:
        """把用户/模型给的路径解析为 root 内的绝对路径。

        规则：空路径拒绝；相对路径以 root 为基准；绝对路径必须位于 root 内；
        含 .. 逃逸或软链接指向 root 之外都会被识别并拒绝。
        """
        if requested is None or str(requested).strip() == "":
            raise WorkspaceError("empty_path", "路径不能为空")
        candidate = Path(str(requested))
        if candidate.is_absolute():
            target = candidate.resolve(strict=False)
        else:
            target = (self.root / candidate).resolve(strict=False)
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceError("outside", f"路径在工作空间之外: {requested}") from exc
        except OSError as exc:
            raise WorkspaceError("io", f"路径解析失败: {exc}") from exc
        return target

    def relative(self, target: str | Path) -> str:
        """把 root 内的绝对路径转成相对路径（posix 风格，与 glob 输出一致）。"""
        path = Path(target).resolve()
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError as exc:
            raise WorkspaceError("outside", f"路径在工作空间之外: {target}") from exc

    def is_within(self, path: str | Path) -> bool:
        """判断路径是否位于 root 内（不抛异常）。"""
        try:
            self.resolve(path)
            return True
        except WorkspaceError:
            return False


def workspace_error_result(exc: WorkspaceError) -> object:
    """把 WorkspaceError 映射为统一的工具失败结果（延迟导入避免循环依赖）。"""
    from tools.base import ToolResult

    code = {
        "empty_path": "EMPTY_PATH",
        "outside": "OUTSIDE_WORKSPACE",
        "io": "PATH_IO_ERROR",
    }.get(exc.kind, "INVALID_PATH")
    return ToolResult.failure(code=code, message=exc.message)