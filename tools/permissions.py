"""Bash 权限模型：风险分级（ALLOW / DENY / ASK）与审批网关。

参考 MyCodeAgent 的 RiskClassifier 思路：先按危险模式判 DENY，
再按写操作模式判 ASK，只读命令放行，未知命令兜底询问。
真正的"沙箱"仍靠用户审批 + 工作空间约束，本模块是决策层。
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class PermissionAction(str, Enum):
    """审批动作：放行 / 拒绝 / 询问用户。"""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class RiskLevel(str, Enum):
    """风险等级，用于向用户展示为什么需要审批。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PermissionDecision:
    """一次命令审批的决策结果。"""

    action: PermissionAction
    risk: RiskLevel
    reason: str
    policy_source: str = "classifier"


class RiskClassifier:
    """把一条 shell 命令分类为 ALLOW / DENY / ASK。"""

    # 高危：直接拒绝（破坏性 / 嵌套执行 / 改动 git 工作区）
    _DENY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
        (re.compile(r"(^|[;&|]\s*)rm\b"), "rm 会删除文件"),
        (re.compile(r"(^|[;&|]\s*)(?:sh|bash|zsh)\s+-c\b"), "嵌套 shell 会绕过命令分类"),
        (re.compile(r"(^|[;&|]\s*)python(?:3)?\s+-c\b"), "内联 Python 会绕过命令分类"),
        (re.compile(r"\|\s*(?:sh|bash|zsh)\b"), "管道进 shell 会执行未审代码"),
        (re.compile(r"`|\$\("), "命令替换会执行嵌套命令"),
        (re.compile(r"(^|[;&|]\s*)git\s+(?:checkout|reset|clean)\b"), "git 写操作会改动工作区"),
        (re.compile(r"(^|[;&|]\s*)(?:shutdown|reboot|halt|mkfs|fdisk)\b"), "系统级危险命令"),
    )
    # 中危：需要用户确认（写文件 / 装依赖 / git 写操作）
    _ASK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
        (re.compile(r"(^|[;&|]\s*)mv\b"), "移动/重命名可能改动项目文件"),
        (re.compile(r"(^|[;&|]\s*)cp\b"), "复制可能写入项目文件"),
        (re.compile(r"(^|[;&|]\s*)(?:chmod|chown)\b"), "修改文件权限/属主"),
        (re.compile(r">>?\s*\S"), "重定向可能覆盖文件"),
        (re.compile(r"\bpip\s+install\b"), "安装 Python 包改变环境"),
        (re.compile(r"\bnpm\s+install\b"), "安装依赖改变环境"),
        (re.compile(r"\bgit\s+(?:commit|push|pull|merge|rebase)\b"), "git 写操作"),
    )
    # 只读命令白名单（首个词精确匹配）
    _ALLOW_EXACT = {
        "pwd", "echo", "printf", "date", "sleep", "true", "false",
        "env", "printenv", "hostname", "whoami", "uname", "python", "python3",
    }
    # 只读命令白名单（git 子命令前缀）
    _ALLOW_GIT_READONLY = re.compile(r"^\s*git\s+(?:status|diff|log|show|branch|rev-parse)\b")

    def classify_bash(self, command: str) -> PermissionDecision:
        """分类命令：危险→DENY，写操作→ASK，只读→ALLOW，未知→ASK 兜底。"""
        for pattern, reason in self._DENY_PATTERNS:
            if pattern.search(command):
                return PermissionDecision(PermissionAction.DENY, RiskLevel.HIGH, reason)
        for pattern, reason in self._ASK_PATTERNS:
            if pattern.search(command):
                return PermissionDecision(PermissionAction.ASK, RiskLevel.MEDIUM, reason)
        is_readonly = self._ALLOW_GIT_READONLY.search(command)
        if is_readonly or self._first_token(command) in self._ALLOW_EXACT:
            return PermissionDecision(PermissionAction.ALLOW, RiskLevel.LOW, "只读命令")
        return PermissionDecision(PermissionAction.ASK, RiskLevel.UNKNOWN, "未知命令，默认需要确认")

    def _first_token(self, command: str) -> str:
        """取命令的第一个词（去掉管道/分隔符前缀），失败返回空串。"""
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = []
        return tokens[0] if tokens else ""


class PermissionGateway:
    """审批入口：策略化决策 + 会话内记忆 + 交互回调。

    ask_policy 含义：
    - ask   ：中高危都询问用户（默认，交互式推荐）
    - allow ：只拒绝高危，中危自动放行（类似 --dangerously-skip-permissions）
    - deny  ：只放行只读命令，其余一律拒绝（非交互模式 fail-closed）
    """

    def __init__(
        self,
        ask_policy: str = "ask",
        ask_handler: Callable[[str, PermissionDecision], bool] | None = None,
        remember: bool = True,
    ) -> None:
        self.classifier = RiskClassifier()
        self.ask_policy = ask_policy
        self.ask_handler = ask_handler
        self.remember = remember
        # 会话内记忆：命令原文 -> 用户选择，避免同一命令反复询问
        self._memory: dict[str, bool] = {}

    def decide(self, command: str) -> PermissionDecision:
        """按策略落地命令分类，返回最终动作。"""
        decision = self.classifier.classify_bash(command)
        if decision.action != PermissionAction.ASK:
            return decision
        if self.ask_policy == "allow":
            return PermissionDecision(
                PermissionAction.ALLOW, decision.risk, decision.reason, "policy=allow"
            )
        if self.ask_policy == "deny":
            return PermissionDecision(
                PermissionAction.DENY, decision.risk, decision.reason, "policy=deny"
            )
        return PermissionDecision(decision.action, decision.risk, decision.reason, "policy=ask")

    def ask(self, command: str, decision: PermissionDecision) -> bool:
        """执行一次询问：命中会话记忆直接返回，否则回调用户并记忆。"""
        if self.remember and command in self._memory:
            return self._memory[command]
        allowed = self._ask_once(command, decision)
        if self.remember:
            self._memory[command] = allowed
        return allowed

    def _ask_once(self, command: str, decision: PermissionDecision) -> bool:
        """没有交互回调时失败关闭（非交互模式不允许放行未审命令）。"""
        if self.ask_handler is None:
            return False
        return self.ask_handler(command, decision)