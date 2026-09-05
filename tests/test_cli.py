"""CLI 入口的单元测试：--cwd 切换工作目录（M10 任意目录运行）。"""

from pathlib import Path

import pytest

from app.cli import _apply_startup_cwd


def test_cwd_missing_argument(tmp_path, monkeypatch):
    """--cwd 后面没有目录参数时应报用法错误退出。"""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        _apply_startup_cwd(["--cwd"])


def test_cwd_directory_not_found(tmp_path, monkeypatch):
    """--cwd 指向不存在的目录时应报错退出，且不切换目录。"""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        _apply_startup_cwd(["--cwd", str(tmp_path / "not_exist")])
    assert Path.cwd() == tmp_path


def test_cwd_switches_directory(tmp_path, monkeypatch):
    """--cwd 指向存在的目录时应切换工作目录。"""
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "sub"
    target.mkdir()
    _apply_startup_cwd(["--cwd", str(target)])
    assert Path.cwd() == target
