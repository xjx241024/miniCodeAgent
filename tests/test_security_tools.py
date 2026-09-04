"""工具级安全测试：工作空间越界拒绝、参数清洗与 Bash 审批流程。"""

import subprocess
from pathlib import Path
from types import SimpleNamespace

from tools.base import BaseTool, ToolResult
from tools.builtin.bash_tool import BashTool
from tools.builtin.glob_tool import GlobTool
from tools.builtin.grep_tool import GrepTool
from tools.builtin.read_tool import ReadTool
from tools.permissions import PermissionGateway
from tools.registry import ToolRegistry
from tools.workspace import Workspace


class EchoTool(BaseTool):
    """仅回显参数，用于验证注册中心的参数清洗。"""

    name = "echo"
    description = "测试用回显工具"
    parameters = {
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "flag": {"type": "boolean"},
            "label": {"type": "string"},
        },
        "required": ["count"],
    }

    def _run(self, arguments: dict) -> ToolResult:
        return ToolResult.success(data=dict(arguments), text=str(arguments))


def test_read_outside_workspace_rejected(tmp_path):
    """读取工作空间之外的文件返回 OUTSIDE_WORKSPACE。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    result = ReadTool(Workspace(proj)).invoke({"path": str(secret)})
    assert result.status == "error"
    assert result.error is not None and result.error.code == "OUTSIDE_WORKSPACE"


def test_read_escape_rejected(tmp_path):
    """.. 逃逸路径返回 OUTSIDE_WORKSPACE。"""
    result = ReadTool(Workspace(tmp_path)).invoke({"path": "../secret.txt"})
    assert result.status == "error"
    assert result.error is not None and result.error.code == "OUTSIDE_WORKSPACE"


def test_glob_escape_keeps_only_within(tmp_path):
    """glob 中带 .. 的匹配会被过滤，只保留工作空间内结果。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("x")
    workspace = Workspace(tmp_path)
    result = GlobTool(workspace).invoke({"pattern": "proj/../proj/*.py", "path": "."})
    assert result.status == "success"
    assert all(workspace.is_within(proj / p) for p in result.data["paths"])


def test_registry_clean_coerces_and_drops_unknown():
    """参数清洗：类型归一、未知字段丢弃。"""
    registry = ToolRegistry()
    registry.register(EchoTool())
    result = registry.call(
        "echo",
        {"count": "3", "flag": "1", "label": 42, "junk": "x"},
    )
    assert result.status == "success"
    assert result.data["count"] == 3 and isinstance(result.data["count"], int)
    assert result.data["flag"] is True
    assert result.data["label"] == "42"
    assert "junk" not in result.data


def test_registry_grep_string_int_coerced(tmp_path):
    """字符串数字传入 integer 参数也能正常执行。"""
    tree = tmp_path / "t"
    tree.mkdir()
    (tree / "a.py").write_text("def foo():\n    pass\n")
    workspace = Workspace(tree)
    registry = ToolRegistry(workspace)
    registry.register(GrepTool(workspace))
    result = registry.call("grep", {"pattern": "def", "path": ".", "max_results": "3"})
    assert result.status == "success"
    assert len(result.data["matches"]) == 1


def test_bash_ask_accept_runs(monkeypatch):
    """中危命令用户同意后执行。"""

    def fake_run(command, **kwargs):
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    gateway = PermissionGateway(ask_policy="ask", ask_handler=lambda c, d: True)
    result = BashTool(Workspace(Path.cwd()), gateway).invoke({"command": "mv a b"})
    assert result.status == "success"
    assert result.text == "ok"


def test_bash_ask_declined_blocked():
    """中危命令用户拒绝后返回 BLOCKED_COMMAND。"""
    gateway = PermissionGateway(ask_policy="ask", ask_handler=lambda c, d: False)
    result = BashTool(Workspace(Path.cwd()), gateway).invoke({"command": "mv a b"})
    assert result.status == "error"
    assert result.error is not None and result.error.code == "BLOCKED_COMMAND"


def test_bash_policy_deny_blocks():
    """deny 策略下中危命令直接拒绝，不询问。"""
    gateway = PermissionGateway(ask_policy="deny")
    result = BashTool(Workspace(Path.cwd()), gateway).invoke({"command": "mv a b"})
    assert result.status == "error"
    assert result.error is not None and result.error.code == "BLOCKED_COMMAND"


def test_bash_cwd_is_workspace_root(monkeypatch, tmp_path):
    """Bash 执行时 cwd 固定为工作空间根。"""
    captured = {}

    def fake_run(command, **kwargs):
        captured["cwd"] = kwargs.get("cwd")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    workspace = Workspace(tmp_path)
    result = BashTool(workspace).invoke({"command": "echo hi"})
    assert result.status == "success"
    assert captured["cwd"] == str(workspace.root)