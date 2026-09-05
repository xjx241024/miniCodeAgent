"""memory.retention 清理测试：按会话数量与保存天数裁剪运行数据。"""

from __future__ import annotations

import os
import time
from pathlib import Path

from memory import retention
from memory.paths import project_key


def _write_session(project: Path, sid: str, mtime: float) -> None:
    """造一个会话：同时写 trace 与 transcript，并把 mtime 拨到指定时间。"""
    for kind in ("traces", "transcripts"):
        path = project / kind / f"{sid}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        os.utime(path, (mtime, mtime))


def test_prune_removes_expired_sessions(tmp_path):
    """超过保存天数的会话（trace + transcript 一起）被删除，新会话保留。"""
    data_dir = tmp_path / "data"
    project = data_dir / "proj"
    old = time.time() - 400 * 86400  # 400 天前
    recent = time.time() - 1
    _write_session(project, "old-session", old)
    _write_session(project, "new-session", recent)
    removed = retention.prune(data_dir, keep_sessions=30, max_age_days=30)
    assert removed == 2  # old-session 的 trace + transcript
    assert not (project / "traces" / "old-session.jsonl").exists()
    assert not (project / "transcripts" / "old-session.jsonl").exists()
    assert (project / "traces" / "new-session.jsonl").exists()


def test_prune_keeps_recent_sessions_by_count(tmp_path):
    """未过期的旧会话按数量裁剪：保留最近 keep_sessions 个。"""
    data_dir = tmp_path / "data"
    project = data_dir / "proj"
    now = time.time()
    for i in range(5):
        _write_session(project, f"s{i}", now - (5 - i))  # s4 最新，s0 最旧
    removed = retention.prune(data_dir, keep_sessions=3, max_age_days=30)
    assert removed == 4  # s0、s1 各 trace + transcript
    remaining = {p.stem for p in (project / "traces").glob("*.jsonl")}
    assert remaining == {"s2", "s3", "s4"}


def test_prune_removes_old_artifacts(tmp_path):
    """超过保存天数的 artifact 文件被删除。"""
    data_dir = tmp_path / "data"
    project = data_dir / "proj"
    artifact_dir = project / "artifacts"
    artifact_dir.mkdir(parents=True)
    old_file = artifact_dir / "old.txt"
    new_file = artifact_dir / "new.txt"
    old_file.write_text("x", encoding="utf-8")
    new_file.write_text("x", encoding="utf-8")
    os.utime(old_file, (time.time() - 400 * 86400, time.time() - 400 * 86400))
    os.utime(new_file, (time.time() - 1, time.time() - 1))
    removed = retention.prune(data_dir, keep_sessions=30, max_age_days=30)
    assert removed == 1
    assert not old_file.exists()
    assert new_file.exists()


def test_prune_scoped_to_workspace(tmp_path):
    """传入 workspace_root 时只清理对应项目的会话。"""
    data_dir = tmp_path / "data"
    project_a = data_dir / project_key(tmp_path / "proj-a")
    project_b = data_dir / project_key(tmp_path / "proj-b")
    old = time.time() - 400 * 86400
    _write_session(project_a, "old-a", old)
    _write_session(project_b, "old-b", old)
    removed = retention.prune(
        data_dir, max_age_days=30, workspace_root=tmp_path / "proj-a"
    )
    assert removed == 2  # 只删 project_a 的两个文件
    assert (project_b / "traces" / "old-b.jsonl").exists()