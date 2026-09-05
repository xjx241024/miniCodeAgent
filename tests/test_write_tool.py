"""Write 工具测试：新建/覆盖、读后写保护、越界与长度限制。"""

from __future__ import annotations

from tools.builtin.read_tool import ReadTool
from tools.builtin.write_tool import MAX_CONTENT_CHARS, WriteTool
from tools.registry import ToolRegistry
from tools.workspace import Workspace


def _registry(workspace) -> ToolRegistry:
    """组装带 workspace 的注册中心（含 read/write）。"""
    registry = ToolRegistry(workspace=workspace)
    registry.register(ReadTool(workspace))
    registry.register(WriteTool(workspace))
    return registry


def test_write_creates_new_file_with_parents(tmp_path):
    """新建文件成功：父目录自动创建，内容与返回信息正确。"""
    registry = _registry(Workspace(tmp_path))
    result = registry.call("write", {"path": "sub/new.txt", "content": "你好"})
    assert result.status == "success"
    assert (tmp_path / "sub" / "new.txt").read_text(encoding="utf-8") == "你好"
    assert result.data["size_bytes"] == len("你好".encode())


def test_write_overwrites_existing_after_read(tmp_path):
    """覆盖已有文件：先 read 后 write 成功，内容被替换。"""
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    registry = _registry(Workspace(tmp_path))
    registry.call("read", {"path": "a.txt"})
    result = registry.call("write", {"path": "a.txt", "content": "new"})
    assert result.status == "success"
    assert target.read_text(encoding="utf-8") == "new"


def test_write_existing_without_read_rejected(tmp_path):
    """覆盖已存在但未读过的文件：FILE_NOT_READ，防止盲写覆盖。"""
    (tmp_path / "a.txt").write_text("old", encoding="utf-8")
    registry = _registry(Workspace(tmp_path))
    result = registry.call("write", {"path": "a.txt", "content": "new"})
    assert result.status == "error"
    assert result.error is not None and result.error.code == "FILE_NOT_READ"
    # 文件内容保持不变
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "old"


def test_write_direct_tool_overwrites_without_guard(tmp_path):
    """不经注册中心直接调用工具：允许覆盖（读后写保护在注册层，工具层不强制）。"""
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    tool = WriteTool(Workspace(tmp_path))
    result = tool.invoke({"path": "a.txt", "content": "new"})
    assert result.status == "success"
    assert target.read_text(encoding="utf-8") == "new"


def test_write_rejects_outside_workspace(tmp_path):
    """越界路径被工作空间约束拒绝。"""
    outside = tmp_path.parent / "outside.txt"
    tool = WriteTool(Workspace(tmp_path))
    result = tool.invoke({"path": str(outside), "content": "x"})
    assert result.status == "error"
    assert result.error is not None and "OUTSIDE" in result.error.code


def test_write_rejects_too_long_content(tmp_path):
    """超过内容长度上限：CONTENT_TOO_LONG。"""
    tool = WriteTool(Workspace(tmp_path))
    result = tool.invoke({"path": "big.txt", "content": "x" * (MAX_CONTENT_CHARS + 1)})
    assert result.status == "error"
    assert result.error is not None and result.error.code == "CONTENT_TOO_LONG"


def test_write_conflict_detected_after_external_edit(tmp_path):
    """read 后文件被外部修改：write 触发乐观锁冲突，拒绝覆盖。"""
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    registry = _registry(Workspace(tmp_path))
    registry.call("read", {"path": "a.txt"})
    target.write_text("changed-by-others", encoding="utf-8")  # 外部改动
    result = registry.call("write", {"path": "a.txt", "content": "new"})
    assert result.status == "error"
    assert result.error is not None and result.error.code == "CONFLICT"