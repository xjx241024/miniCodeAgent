"""文件地图（L2 静态层）：生成最多两层的目录树概览，帮助模型定位文件。"""

from __future__ import annotations

from pathlib import Path

# 忽略的噪音目录：虚拟环境、缓存、版本库等
IGNORED_DIRS = {
    ".git", ".venv", "__pycache__", ".ruff_cache", ".pytest_cache",
    ".pytest_tmp", "node_modules", "dist", "build", ".idea", ".vscode",
}

# 文件地图最大扫描深度（层），避免大仓库撑爆上下文
MAX_DEPTH = 2


class RepoMapBuilder:
    """扫描工作目录生成两层的文件树文本；超出上限行数时截断并提示。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def build(self, max_lines: int = 150) -> str | None:
        """生成文件地图文本；目录为空或不可读时返回 None。"""
        lines: list[str] = []
        self._walk(self.root, depth=0, lines=lines, max_lines=max_lines)
        if not lines:
            return None
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines.append("  …(已截断，如需更多细节请用 glob 查询)")
        return "\n".join(lines)

    def _walk(self, base: Path, depth: int, lines: list[str], max_lines: int) -> None:
        """递归收集目录树：目录带斜杠，叶子文件只列名字。

        收集到 max_lines+1 行即停止，由 build() 统一截断并追加提示。
        """
        if depth > MAX_DEPTH or len(lines) > max_lines:
            return
        try:
            entries = sorted(
                (p for p in base.iterdir() if self._visible(p)),
                key=lambda p: (p.is_file(), p.name),
            )
        except OSError:
            return
        for entry in entries:
            if len(lines) > max_lines:
                return
            indent = "  " * depth
            if entry.is_dir():
                lines.append(f"{indent}{entry.name}/")
                self._walk(entry, depth + 1, lines, max_lines)
            else:
                lines.append(f"{indent}{entry.name}")

    def _visible(self, path: Path) -> bool:
        """过滤噪音目录与隐藏目录；隐藏文件（如 .env.example）保留。"""
        if path.name in IGNORED_DIRS:
            return False
        if path.is_dir() and path.name.startswith("."):
            return False
        return True