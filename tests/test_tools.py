"""工具层单元测试：统一协议、Glob/Read 与注册中心。"""

import logging
from pathlib import Path

from tools.builtin.glob_tool import GlobTool
from tools.builtin.read_tool import ReadTool
from tools.registry import ToolRegistry


def _make_tree(tmp_path: Path) -> Path:
    """在临时目录里造一棵小文件树。"""
    (tmp_path / "a.py").write_text("print('a')\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("hello\n", encoding="utf-8")
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "mod.py").write_text("print('mod')\n", encoding="utf-8")
    return tmp_path


def test_glob_finds_py_files(tmp_path):
    """按 **/*.py 递归找到 py 文件，且不匹配 txt。"""
    tree = _make_tree(tmp_path)
    result = GlobTool().invoke({"pattern": "**/*.py", "path": str(tree)})
    assert result.status == "success"
    paths = result.data["paths"]
    assert "a.py" in paths
    assert "pkg/mod.py" in paths
    assert all(p.endswith(".py") for p in paths)
    # text 也要带上路径，模型才能据此继续（不能只给计数）
    assert "a.py" in result.text and "pkg/mod.py" in result.text


def test_glob_missing_dir_returns_error():
    """目录不存在应返回 NOT_FOUND 而非抛异常。"""
    result = GlobTool().invoke({"pattern": "**/*.py", "path": "no_such_dir"})
    assert result.status == "error"
    assert result.error is not None and result.error.code == "NOT_FOUND"


def test_read_with_line_numbers(tmp_path):
    """读取文件：带行号、元信息完整。"""
    tree = _make_tree(tmp_path)
    result = ReadTool().invoke({"path": str(tree / "a.py")})
    assert result.status == "success"
    assert "1 | print('a')" in result.text
    assert result.data["size_bytes"] > 0
    assert result.data["mtime_ms"] > 0
    assert len(result.data["content_hash"]) == 64
    assert result.data["total_lines"] == 1


def test_read_missing_file_returns_error():
    """文件不存在应返回 NOT_FOUND。"""
    result = ReadTool().invoke({"path": "no_such_file.py"})
    assert result.status == "error"
    assert result.error is not None and result.error.code == "NOT_FOUND"


def test_read_truncation(tmp_path):
    """超过 max_lines 时应截断并标记 truncated。"""
    tree = _make_tree(tmp_path)
    file = tree / "big.py"
    file.write_text("\n".join(f"line{i}" for i in range(10)), encoding="utf-8")
    result = ReadTool().invoke({"path": str(file), "max_lines": 3})
    assert result.data["truncated"] is True
    assert result.data["total_lines"] == 10
    assert "4 |" not in result.text  # 只展示前 3 行
    assert "仅显示前 3 行" in result.text  # 截断提示让模型知道还有更多行


def test_registry_register_and_call(tmp_path):
    """注册中心：注册、schema 汇总、调用分发、未知工具兜底。"""
    tree = _make_tree(tmp_path)
    registry = ToolRegistry()
    registry.register(GlobTool())
    registry.register(ReadTool())
    assert registry.names() == ["glob", "read"]
    schemas = registry.schemas()
    assert {s["function"]["name"] for s in schemas} == {"glob", "read"}
    # 成功路径
    result = registry.call("glob", {"pattern": "*.py", "path": str(tree)})
    assert result.status == "success"
    assert "a.py" in result.data["paths"]
    # 未知工具返回 UNKNOWN_TOOL 而非抛异常
    unknown = registry.call("nope", {})
    assert unknown.status == "error"
    assert unknown.error is not None and unknown.error.code == "UNKNOWN_TOOL"


def test_registry_logs_on_register(caplog):
    """注册成功与重复注册都应产生日志。"""
    registry = ToolRegistry()
    with caplog.at_level(logging.INFO):
        registry.register(GlobTool())
        assert "glob" in caplog.text and "已注册" in caplog.text
        caplog.clear()
        # 重复注册应记 warning 并抛错
        try:
            registry.register(GlobTool())
        except ValueError:
            pass
    assert "已存在" in caplog.text


def test_registry_logs_on_call(tmp_path, caplog):
    """调用成功与未知工具都应产生日志。"""
    tree = _make_tree(tmp_path)
    registry = ToolRegistry()
    registry.register(GlobTool())
    with caplog.at_level(logging.INFO):
        result = registry.call("glob", {"pattern": "*.py", "path": str(tree)})
        assert result.status == "success"
        assert "glob" in caplog.text and "success" in caplog.text
        caplog.clear()
        registry.call("nope", {})
    assert "未注册的工具" in caplog.text