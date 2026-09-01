"""Message 数据结构的单元测试。"""

from core.message import Message, assistant, system, user


def test_user_message_to_api_dict():
    """用户消息转 API 字典：空字段应被剔除。"""
    msg = user("你好")
    assert msg.to_api_dict() == {"role": "user", "content": "你好"}


def test_role_helpers():
    """三个快捷构造函数的角色与内容正确。"""
    assert system("规则").role == "system"
    assert assistant("回复").content == "回复"
    assert user("问题").role == "user"


def test_tool_call_fields_are_preserved():
    """assistant 消息携带工具调用时，字段应完整保留。"""
    msg = Message(
        role="assistant",
        content="",
        tool_calls=[{"id": "call_1", "function": {"name": "read_file", "arguments": "{}"}}],
    )
    api = msg.to_api_dict()
    assert api["tool_calls"][0]["function"]["name"] == "read_file"