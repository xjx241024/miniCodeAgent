"""Bash 兜底工具测试：禁止模式、正常执行、超时与非零退出。"""

import subprocess
from types import SimpleNamespace

from tools.builtin.bash_tool import BashTool


def test_blocked_commands_are_rejected():
    """命中禁止模式的命令一律拒绝，不真正执行。"""
    tool = BashTool()
    blocked = [
        "ls -la",
        "cat a.py",
        "head -n 5 a.py",
        "tail a.py",
        "grep foo a.py",
        "find . -name '*.py'",
        "rg foo",
        "vim a.py",
        "ssh user@host",
        "curl http://example.com",
        "wget http://example.com",
        "sudo rm -rf /",
        "rm -rf /",
        "mkfs.ext4 /dev/sda",
        "fdisk -l",
    ]
    for command in blocked:
        result = tool.invoke({"command": command})
        assert result.status == "error", command
        assert result.error is not None and result.error.code == "BLOCKED_COMMAND", command


def test_empty_command_is_rejected():
    """空命令返回 EMPTY_COMMAND。"""
    result = BashTool().invoke({"command": "   "})
    assert result.status == "error"
    assert result.error is not None and result.error.code == "EMPTY_COMMAND"


def test_allowed_command_runs(monkeypatch):
    """未命中禁止模式的命令正常执行，返回 stdout。"""

    def fake_run(command, **kwargs):
        return SimpleNamespace(returncode=0, stdout="hello\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = BashTool().invoke({"command": "echo hello"})
    assert result.status == "success"
    assert result.text == "hello"


def test_nonzero_exit_reported(monkeypatch):
    """非零退出码按 EXIT_NONZERO 返回，并把 stderr 带回来。"""

    def fake_run(command, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = BashTool().invoke({"command": "git status"})
    assert result.status == "error"
    assert result.error is not None and result.error.code == "EXIT_NONZERO"
    assert "boom" in result.error.message


def test_timeout_reported(monkeypatch):
    """超时返回 TIMEOUT。"""

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout", 10))

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = BashTool().invoke({"command": "sleep 100", "timeout": 1})
    assert result.status == "error"
    assert result.error is not None and result.error.code == "TIMEOUT"


def test_long_output_truncated(monkeypatch):
    """超长输出被截断，避免撑爆上下文。"""
    big = "x" * 10000

    def fake_run(command, **kwargs):
        return SimpleNamespace(returncode=0, stdout=big, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = BashTool().invoke({"command": "echo big"})
    assert result.status == "success"
    assert "…" in result.text
    assert len(result.text) < 5000