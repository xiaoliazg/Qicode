"""`Conversation` 的单测（T6）。"""

from qicode.conversation import Conversation
from qicode.llm import Message


def test_empty_conversation_has_no_messages() -> None:
    assert Conversation().messages() == []


def test_roles_and_order_are_preserved() -> None:
    """后加的排在后面，role 各归各位。"""
    conv = Conversation()
    conv.add_user("第一问")
    conv.add_assistant("第一答")
    conv.add_user("第二问")

    assert conv.messages() == [
        Message(role="user", content="第一问"),
        Message(role="assistant", content="第一答"),
        Message(role="user", content="第二问"),
    ]


def test_multi_turn_accumulates_in_order() -> None:
    """多轮之后顺序不错乱——这是「上下文完整」的核心（F6）。"""
    conv = Conversation()
    for i in range(3):
        conv.add_user(f"问{i}")
        conv.add_assistant(f"答{i}")

    msgs = conv.messages()
    assert len(msgs) == 6
    assert [m.role for m in msgs] == ["user", "assistant"] * 3
    assert [m.content for m in msgs] == ["问0", "答0", "问1", "答1", "问2", "答2"]


def test_messages_returns_a_copy() -> None:
    """改动返回的列表不能影响内部历史——下一轮请求全靠它。"""
    conv = Conversation()
    conv.add_user("你好")

    snapshot = conv.messages()
    snapshot.append(Message(role="assistant", content="伪造的回复"))
    snapshot.clear()

    assert conv.messages() == [Message(role="user", content="你好")]


def test_each_call_returns_a_new_list() -> None:
    """是「每次新造一份」而不是「第一次造完一直发同一份」。"""
    conv = Conversation()
    conv.add_user("你好")

    assert conv.messages() is not conv.messages()


def test_empty_text_is_kept_as_is() -> None:
    """空串原样存下，这一层不做过滤。

    显式钉住这个边界：`Conversation` 只负责「存」，判断「这条该不该发出去」
    是上层的事。哪天有人想在这里加 strip / 跳过空串，这个测试会先亮红灯，
    逼他先想清楚该在哪一层做。
    """
    conv = Conversation()
    conv.add_user("")

    assert conv.messages() == [Message(role="user", content="")]
