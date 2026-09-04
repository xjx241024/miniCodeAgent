"""M7 演示：安全边界（工作空间约束 + Bash 审批）的离线演示。

不调用真实模型，展示：路径越界如何被拦截、命令风险如何分级、
审批网关如何在"询问/记忆/策略"下工作。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from tools.builtin.bash_tool import BashTool
from tools.permissions import PermissionGateway, RiskClassifier
from tools.workspace import Workspace


def _demo_workspace() -> None:
    """演示工作空间约束：合法路径放行，越界路径被拒绝。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "project"
        proj.mkdir()
        (proj / "a.py").write_text("print('hi')\n", encoding="utf-8")
        ws = Workspace(proj)
        print("==== 工作空间约束 ====")
        print(f"root: {ws.root}")
        print(f"resolve('a.py')      -> {ws.resolve('a.py')}  （放行）")
        for bad in ("../secret.txt", str(Path(tmp) / "outside.txt")):
            try:
                ws.resolve(bad)
            except Exception as exc:
                print(f"resolve({bad!r}) -> 拒绝（{exc.kind}）")


def _demo_classifier() -> None:
    """演示命令风险分级。"""
    classifier = RiskClassifier()
    print("\n==== Bash 命令风险分级 ====")
    commands = [
        "pwd", "echo hello", "git status", "mv a b",
        "pip install x", "rm -rf /", "sh -c 'x'",
    ]
    for command in commands:
        decision = classifier.classify_bash(command)
        print(f"{command!r:24} -> {decision.action.value:5} 风险={decision.risk.value}")


def _demo_gateway() -> None:
    """演示审批网关：ask 策略询问（用自动同意回调模拟），并展示记忆效果。"""
    print("\n==== Bash 审批网关（ask 策略） ====")
    calls: list[str] = []

    def auto_yes(command, decision):
        calls.append(command)
        return True

    gateway = PermissionGateway(ask_policy="ask", ask_handler=auto_yes)
    tool = BashTool(Workspace(Path.cwd()), gateway)
    for command in ["echo ok", "mv a b", "mv a b", "rm -rf /"]:
        result = tool.invoke({"command": command})
        code = result.error.code if result.error else "success"
        print(f"{command!r:14} -> {result.status}（{code}）")
    print(f"询问次数（mv a b 只应询问 1 次）: {calls.count('mv a b')}")


def main() -> None:
    _demo_workspace()
    _demo_classifier()
    _demo_gateway()


if __name__ == "__main__":
    main()