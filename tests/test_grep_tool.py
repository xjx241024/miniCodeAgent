"""Grep 工具的单元测试。"""

from pathlib import Path

from tools.builtin.grep_tool import GrepTool


def _make_tree(tmp_path: Path) -> Path:
    (tmp_path / "a.py").write_text("def hello():\n    pass\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("def hello again\n", encoding="utf-8")
    return tmp_path


def test_grep_finds_matches(tmp_path):
    """能找到匹配并带行号，文件集合完整。"""
    tree = _make_tree(tmp_path)
    result = GrepTool().invoke({"pattern": "def hello", "path": str(tree)})
    assert result.status == "success"
    matches = result.data["matches"]
    files = {m["file"] for m in matches}
    assert len(matches) == 2
    assert any(f.endswith("a.py") for f in files)
    assert any(f.endswith("b.txt") for f in files)
    assert all(m["line"] == 1 for m in matches)
    # text 也要带上具体匹配（file:line: content），模型才能据此继续
    assert "a.py:1:" in result.text
    assert "b.txt:1:" in result.text


def test_grep_single_file_path(tmp_path):
    """path 传单个文件时，直接在该文件内搜索。"""
    tree = _make_tree(tmp_path)
    result = GrepTool().invoke({"pattern": "def hello", "path": str(tree / "a.py")})
    assert result.status == "success"
    matches = result.data["matches"]
    assert len(matches) == 1
    assert matches[0]["file"].endswith("a.py")
    assert matches[0]["line"] == 1


def test_grep_no_match_returns_empty(tmp_path):
    """没有匹配时返回空列表而非错误。"""
    tree = _make_tree(tmp_path)
    result = GrepTool().invoke({"pattern": "no_such_symbol", "path": str(tree)})
    assert result.status == "success"
    assert result.data["matches"] == []
    assert result.data["truncated"] is False


def test_grep_missing_dir_returns_error():
    """目录不存在应返回 NOT_FOUND。"""
    result = GrepTool().invoke({"pattern": "x", "path": "no_such_dir"})
    assert result.status == "error"
    assert result.error is not None and result.error.code == "NOT_FOUND"


def test_grep_invalid_regex_returns_error(tmp_path):
    """非法正则应返回 INVALID_PATTERN。"""
    tree = _make_tree(tmp_path)
    result = GrepTool().invoke({"pattern": "(", "path": str(tree)})
    assert result.status == "error"
    assert result.error is not None and result.error.code == "INVALID_PATTERN"