"""`Conversation` 的单测（T6、T13）。"""

from qicode.conversation import Conversation
from qicode.llm import Message, ToolCall, ToolResult


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


# ────────────────────────── T13：工具回合入历史 ──────────────────────────


def test_tool_round_trip_lands_in_history_in_order() -> None:
    """一轮完整工具对话的四条历史：问 → 调工具 → 结果 → 最终答复。

    顺序错了就发不出去：协议靠 id 配对，assistant 的 tool_calls 和 tool 的结果
    必须紧挨着、且调用在前，缺一头或颠倒了，服务端只会回一句「对不上账」。
    """
    conv = Conversation()
    call = ToolCall(id="call_1", name="read_file", input='{"path": "a.py"}')
    result = ToolResult(tool_call_id="call_1", content="1\timport os")

    conv.add_user("看看 a.py")
    conv.add_assistant_with_tool_calls("我读一下", [call])
    conv.add_tool_results([result])
    conv.add_assistant("它导入了 os")

    msgs = conv.messages()

    assert len(msgs) == 4
    assert [m.role for m in msgs] == ["user", "assistant", "tool", "assistant"]
    assert msgs[1].content == "我读一下"
    assert msgs[1].tool_calls == [call]
    assert msgs[2].content == ""
    assert msgs[2].tool_results == [result]
    assert msgs[3].content == "它导入了 os"
    assert msgs[3].tool_calls == []


def test_assistant_tool_round_may_have_empty_preamble() -> None:
    """一句话不说直接开调是合法的，不能被当成「空回合」丢掉。"""
    conv = Conversation()
    conv.add_assistant_with_tool_calls("", [ToolCall(id="c", name="bash", input="{}")])

    msgs = conv.messages()

    assert len(msgs) == 1
    assert msgs[0].content == ""
    assert len(msgs[0].tool_calls) == 1


def test_tool_round_copies_the_caller_lists() -> None:
    """外面那份列表被改，历史不能跟着变——历史是后续每一轮的上下文。"""
    conv = Conversation()
    calls = [ToolCall(id="c1", name="bash", input="{}")]
    results = [ToolResult(tool_call_id="c1", content="ok")]

    conv.add_assistant_with_tool_calls("", calls)
    conv.add_tool_results(results)
    calls.append(ToolCall(id="c2", name="bash", input="{}"))
    results.clear()

    msgs = conv.messages()
    assert len(msgs[0].tool_calls) == 1
    assert len(msgs[1].tool_results) == 1
