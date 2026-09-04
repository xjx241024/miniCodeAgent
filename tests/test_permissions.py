"""Bash 风险分级与审批网关测试。"""

from tools.permissions import PermissionAction, PermissionGateway, RiskClassifier


def test_classify_deny_commands():
    """高危命令直接拒绝。"""
    classifier = RiskClassifier()
    for command in [
        "rm file.txt",
        "sh -c 'echo x'",
        "bash -c 'echo x'",
        "echo x | sh",
        "git checkout main",
        "`ls`",
        "$(whoami)",
    ]:
        decision = classifier.classify_bash(command)
        assert decision.action == PermissionAction.DENY, command


def test_classify_ask_commands():
    """写操作命令需要确认。"""
    classifier = RiskClassifier()
    for command in ["mv a b", "cp a b", "chmod +x a", "echo x > f", "pip install x"]:
        decision = classifier.classify_bash(command)
        assert decision.action == PermissionAction.ASK, command


def test_classify_allow_readonly():
    """只读命令直接放行。"""
    classifier = RiskClassifier()
    for command in ["pwd", "echo hello", "git status", "git diff", "sleep 1"]:
        decision = classifier.classify_bash(command)
        assert decision.action == PermissionAction.ALLOW, command


def test_classify_unknown_asks():
    """未知命令兜底询问。"""
    decision = RiskClassifier().classify_bash("some_weird_tool --flag")
    assert decision.action == PermissionAction.ASK


def test_policy_allow_promotes_ask():
    """allow 策略把中危提升为放行。"""
    decision = PermissionGateway(ask_policy="allow").decide("mv a b")
    assert decision.action == PermissionAction.ALLOW
    assert decision.policy_source == "policy=allow"


def test_policy_deny_blocks_ask():
    """deny 策略把中危改为拒绝。"""
    decision = PermissionGateway(ask_policy="deny").decide("mv a b")
    assert decision.action == PermissionAction.DENY


def test_policy_ask_keeps_ask():
    """ask 策略保留询问。"""
    decision = PermissionGateway(ask_policy="ask").decide("mv a b")
    assert decision.action == PermissionAction.ASK


def test_gateway_remembers_choice():
    """会话内同一命令只询问一次。"""
    calls = []

    def handler(command, decision):
        calls.append(command)
        return True

    gateway = PermissionGateway(ask_policy="ask", ask_handler=handler, remember=True)
    decision = gateway.decide("mv a b")
    assert gateway.ask("mv a b", decision) is True
    assert gateway.ask("mv a b", decision) is True
    assert len(calls) == 1


def test_gateway_no_handler_fails_closed():
    """没有交互回调时询问一律失败关闭。"""
    gateway = PermissionGateway(ask_policy="ask", ask_handler=None)
    decision = gateway.decide("mv a b")
    assert gateway.ask("mv a b", decision) is False