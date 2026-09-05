"""Bash 兜底工具：执行低频 shell 命令，带黑白名单 + 审批 + 工作空间约束。

定位是"最后手段"：命中禁止模式的命令直接拒绝；中高危命令交给审批网关，
交互式 CLI 会询问用户；执行时把 cwd 固定在工作空间根内。
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from tools.base import BaseTool, ToolResult
from tools.permissions import PermissionAction, PermissionGateway
from tools.workspace import Workspace

# 禁止模式（参考 Extra09 设计）：禁止用 bash 做高频工具能做的事、禁止交互/网络/危险命令
BASH_DISABLED_PATTERNS = [
    # 高频动作：读 / 搜 / 列有专门工具，不让 bash 抄近道
    r"\bls\b", r"\bcat\b", r"\bhead\b", r"\btail\b",
    r"\bgrep\b", r"\bfind\b", r"\brg\b",
    # 交互式程序：会阻塞等待输入
    r"\bvim?\b", r"\bnano\b", r"\btop\b", r"\bssh\b",
    # 网络访问（默认禁用）
    r"\bcurl\b", r"\bwget\b",
    # 危险命令黑名单
    r"\brm\s+-rf\b", r"\bsudo\b", r"\bsu\b", r"\bmkfs\b", r"\bfdisk\b",
]

# 工具层的"保护性"输出上限：超过说明输出异常巨大（防内存爆炸），
# 正常超长输出不再头部截断，交由 runtime.output_guard 全文落盘 + 预览治理
RAW_OUTPUT_LIMIT = 200_000


class BashTool(BaseTool):
    """执行一条低频 shell 命令；命中禁止模式拒绝，中高危走审批，超时/非零退出按错误返回。"""

    name = "bash"
    description = "执行低频 shell 命令（禁止高频动作、交互、网络与危险命令；中高危需用户确认）"
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "timeout": {"type": "integer", "description": "超时秒数，默认 10", "default": 10},
        },
        "required": ["command"],
    }

    def __init__(
        self,
        workspace: Workspace | None = None,
        permission: PermissionGateway | None = None,
    ):
        # 默认工作空间为当前目录；permission 为 None 时只做黑名单拦截（测试/降级路径）
        self.workspace = workspace or Workspace(Path.cwd())
        self.permission = permission
        # description 按平台动态生成：cmd 与 bash 语法差异大，必须提前告知模型
        self.description = _build_description()

    def _run(self, arguments: dict) -> ToolResult:
        command = str(arguments.get("command", "")).strip()
        timeout = int(arguments.get("timeout", 10))
        if not command:
            return ToolResult.failure(code="EMPTY_COMMAND", message="命令为空")
        blocked = self._check_blocked(command)
        if blocked:
            return ToolResult.failure(code="BLOCKED_COMMAND", message=blocked)
        # 审批：策略化决策（DENY 直接拒，ASK 询问用户）
        if self.permission is not None:
            decision = self.permission.decide(command)
            if decision.action == PermissionAction.DENY:
                return ToolResult.failure(
                    code="BLOCKED_COMMAND",
                    message=f"命令被风险策略拒绝: {command}（{decision.reason}）",
                )
            if (
                decision.action == PermissionAction.ASK
                and not self.permission.ask(command, decision)
            ):
                return ToolResult.failure(
                    code="BLOCKED_COMMAND",
                    message=f"用户拒绝了命令: {command}（{decision.reason}）",
                )
        try:
            # 固定 cwd 在工作空间根内；shell=True 在 POSIX 用 sh/bash，在 Windows 用 cmd
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self.workspace.root),
            )
        except subprocess.TimeoutExpired:
            return ToolResult.failure(code="TIMEOUT", message=f"命令执行超过 {timeout} 秒")
        stdout = _raw_bounded((proc.stdout or "").strip())
        stderr = _raw_bounded((proc.stderr or "").strip())
        if proc.returncode != 0:
            message = (
                f"退出码 {proc.returncode}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"
            )
            return ToolResult.failure(code="EXIT_NONZERO", message=message)
        return ToolResult.success(
            data={"returncode": 0, "stderr": stderr},
            text=stdout or "(无输出)",
        )

    def _check_blocked(self, command: str) -> str | None:
        """检查命令是否命中禁止模式，命中则返回原因，否则返回 None。"""
        for pattern in BASH_DISABLED_PATTERNS:
            if re.search(pattern, command):
                return f"命令被禁止: {command}（命中模式 {pattern}），请使用专用工具或拆解任务"
        return None


def _build_description() -> str:
    """按平台生成 Bash 工具描述（动态而非写死，天然适配跨平台）。"""
    base = "执行低频 shell 命令（禁止高频动作、交互、网络与危险命令；中高危需用户确认）"
    if os.name == "nt":
        return base + (
            "。当前为 Windows cmd.exe：不支持 heredoc、; / & 连接、单引号与 $() 语法，"
            "复杂逻辑请先写脚本文件再执行"
        )
    return base + "。当前为 POSIX shell（bash/sh）"


def _raw_bounded(text: str) -> str:
    """极端超大输出才截断（保护性上限），正常范围返回全文交给上层治理。"""
    if len(text) <= RAW_OUTPUT_LIMIT:
        return text
    return text[:RAW_OUTPUT_LIMIT] + f"…(共 {len(text)} 字符，超出保护上限)"