"""工作空间约束测试：路径解析、越界拒绝与错误码。"""

import pytest

from tools.workspace import Workspace, WorkspaceError


def test_resolve_relative_within_root(tmp_path):
    """相对路径以 root 为基准解析，结果在 root 内。"""
    ws = Workspace(tmp_path)
    assert ws.resolve("a/b.py") == (tmp_path / "a" / "b.py").resolve()
    assert ws.is_within("a/b.py")


def test_resolve_absolute_within_root(tmp_path):
    """绝对路径在 root 内时允许。"""
    ws = Workspace(tmp_path)
    inner = tmp_path / "x.py"
    inner.write_text("x")
    assert ws.resolve(str(inner)) == inner.resolve()


def test_resolve_absolute_outside_rejected(tmp_path):
    """绝对路径在 root 之外时抛 outside。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("s")
    ws = Workspace(proj)
    with pytest.raises(WorkspaceError) as exc_info:
        ws.resolve(str(secret))
    assert exc_info.value.kind == "outside"


def test_resolve_escape_rejected(tmp_path):
    """.. 逃逸到 root 之外时抛 outside。"""
    ws = Workspace(tmp_path)
    with pytest.raises(WorkspaceError) as exc_info:
        ws.resolve("../outside")
    assert exc_info.value.kind == "outside"


def test_resolve_empty_rejected(tmp_path):
    """空路径抛 empty_path。"""
    ws = Workspace(tmp_path)
    with pytest.raises(WorkspaceError) as exc_info:
        ws.resolve("")
    assert exc_info.value.kind == "empty_path"


def test_relative_roundtrip(tmp_path):
    """relative() 把绝对路径还原为相对路径。"""
    ws = Workspace(tmp_path)
    assert ws.relative(tmp_path / "a" / "b.py") == "a/b.py"