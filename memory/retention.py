"""运行期数据清理：按会话数量与保存天数裁剪 trace / transcript / artifact。"""

from __future__ import annotations

import time
from pathlib import Path

from memory.paths import project_key


def prune(
    data_dir: str | Path,
    *,
    keep_sessions: int = 30,
    max_age_days: int = 30,
    workspace_root: str | Path | None = None,
) -> int:
    """清理过期会话与 artifact，返回删除的文件数。

    规则（每个项目目录内）：
    - trace + transcript 按 session_id 归组，超过保存天数或超出最近 keep_sessions 个会话的删除；
    - artifacts 按修改时间，超过 max_age_days 的删除。
    """
    base = Path(data_dir)
    if not base.is_dir():
        return 0
    if workspace_root is not None:
        projects = [base / project_key(workspace_root)]
    else:
        projects = [p for p in base.iterdir() if p.is_dir()]
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for project in projects:
        sessions = _collect_sessions(project)
        # 按"该会话最新文件时间"排序（升序=最旧在前）
        ordered = sorted(sessions, key=lambda sid: _session_mtime(sessions[sid]))
        removable = set(ordered[:-keep_sessions]) if keep_sessions > 0 else set(ordered)
        for sid, paths in sessions.items():
            if _session_mtime(paths) < cutoff or sid in removable:
                for path in paths:
                    removed += _unlink(path)
        removed += _prune_artifacts(project / "artifacts", cutoff)
    return removed


def _collect_sessions(project: Path) -> dict[str, list[Path]]:
    """把 traces/ 与 transcripts/ 下同一 session_id 的文件归为一组。"""
    sessions: dict[str, list[Path]] = {}
    for kind in ("traces", "transcripts"):
        directory = project / kind
        if not directory.is_dir():
            continue
        for path in directory.glob("*.jsonl"):
            sessions.setdefault(path.stem, []).append(path)
    return sessions


def _session_mtime(paths: list[Path]) -> float:
    """会话最新文件的修改时间。"""
    return max(p.stat().st_mtime for p in paths)


def _prune_artifacts(artifact_dir: Path, cutoff: float) -> int:
    """删除超过保存天数的 artifact 文件。"""
    if not artifact_dir.is_dir():
        return 0
    removed = 0
    for path in artifact_dir.iterdir():
        if path.is_file():
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
    return removed


def _unlink(path: Path) -> int:
    """删除单文件，失败返回 0。"""
    try:
        path.unlink()
        return 1
    except OSError:
        return 0