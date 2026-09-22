"""`Agent.run` 的单测（AC8、AC9、AC11、N1、N4）。

写法沿用 v1：同步 `def test_...` 里 `asyncio.run(...)`，不引入 pytest-asyncio。

假 provider 来自 `tests/conftest.py` 的 `ScriptedProvider`，它支持**多轮脚本**：
传 `[[第一轮事件], [第二轮事件]]` 就能演「第一轮要调工具、第二轮给最终答复」。
这一层不 import textual，所以整条闭环可以在没有终端的情况下跑完。
"""

import asyncio
import json
from typing import Any

import pytest

from qicode.agent import (
    EMPTY_REPLY,
    MAX_ARGS_PREVIEW,
    TOOL_LIMIT,
    Agent,
    Event,
    Phase,
    preview_args,
)
from qicode.conversation import Conversation
from qicode.llm import Message, StreamEvent, ToolCall
from qicode.tool import Registry, Result


class CountingTool:
    """真注册中心里的一个假工具：记下每次被调用的入参。"""

    def __init__(self, tool_name: str = "read_file") -> None:
        self._name = tool_name
        self.calls: list[str] = []

    def name(self) -> str:
        return self._name

    def description(self) -> str:
        return "假的读文件工具"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }

    async def execute(self, args: str) -> Result:
        self.calls.append(args)
        return Result(f"读到了 {args}")


class HangingTool:
    """永远不返回的假工具，用来把「取消」精确地卡在**工具执行**那一刻。"""

    def name(self) -> str:
        return "read_file"

    def description(self) -> str:
        return "永远不返回"

    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, args: str) -> Result:
        await asyncio.Event().wait()  # 只有取消能把它叫醒
        raise AssertionError("本不该跑到这里")


def registry_with(*tools: CountingTool) -> Registry:
    registry = Registry()
    for tool in tools:
        registry.register(tool)
    return registry


def drive(agent: Agent, conv: Conversation) -> list[Event]:
    """把整轮事件收成列表。"""

    async def collect() -> list[Event]:
        return [event async for event in agent.run(conv)]

    return asyncio.run(collect())


def trace(events: list[Event]) -> list[str]:
    """把事件压成一行一个的短标签，方便整体断言顺序。

    顺序是这一层的**接口**：界面就按到达顺序画块，所以顺序错了界面必然画错，
    而单看某一个事件是看不出来的。
    """
    out: list[str] = []
    for event in events:
        if event.tool is not None:
            out.append(f"tool:{event.tool.phase.value}:{event.tool.name}")
        elif event.text:
            out.append(f"text:{event.text}")
        elif event.done:
            out.append("done")
        elif event.err is not None:
            out.append(f"err:{event.err}")
    return out


def read_call(path: str = "a.py", call_id: str = "call_1") -> ToolCall:
    return ToolCall(id=call_id, name="read_file", input=json.dumps({"path": path}))


def find_end(events: list[Event]) -> Any:
    """取工具结束那一帧。"""
    return next(e.tool for e in events if e.tool and e.tool.phase is Phase.END)


# ────────────────────────── AC8：工具轮闭环 ──────────────────────────


def test_tool_round_emits_text_tool_both_phases_then_done(make_provider) -> None:
    """一轮的完整事件顺序：正文 → 工具开始 → 工具结束 → 最终正文 → 结束。"""
    provider = make_provider(
        [
            [
                StreamEvent(text="我读一下"),
                StreamEvent(tool_calls=[read_call()]),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="文件已读取"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("看看 a.py")

    events = drive(Agent(provider, registry_with(CountingTool())), conv)

    assert trace(events) == [
        "text:我读一下",
        "tool:start:read_file",
        "tool:end:read_file",
        "text:文件已读取",
        "done",
    ]


def test_tool_round_writes_four_messages_into_history(make_provider) -> None:
    """AC8：一轮工具对话在历史里留下**四条**，而且顺序与协议要求一致。

    顺序错不得——协议靠 id 配对，assistant 的那条 tool_calls 必须排在 tool 结果
    之前，反过来就等于「先有结果后有调用」，服务端只会回一句对不上账。
    """
    provider = make_provider(
        [
            [
                StreamEvent(text="我读一下"),
                StreamEvent(tool_calls=[read_call()]),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="文件已读取"), StreamEvent(done=True)],
        ]
    )
    tool = CountingTool()
    conv = Conversation()
    conv.add_user("看看 a.py")

    drive(Agent(provider, registry_with(tool)), conv)

    msgs = conv.messages()
    assert [m.role for m in msgs] == ["user", "assistant", "tool", "assistant"]
    assert msgs[1].content == "我读一下"
    assert [c.name for c in msgs[1].tool_calls] == ["read_file"]
    assert msgs[2].tool_results[0].tool_call_id == "call_1"
    assert msgs[2].tool_results[0].content == '读到了 {"path": "a.py"}'
    assert msgs[3].content == "文件已读取"


def test_second_request_carries_the_tool_rounds(make_provider) -> None:
    """请求#2 必须带上「调用 + 结果」，否则模型根本不知道工具跑出了什么。

    这条顺带钉住 `Conversation.messages()` 那次**取副本**的时机：请求#1 发出时
    历史里只有 user，请求#2 发出时已经有四条（含本条提问）。取早了或取晚了都会错。
    """
    provider = make_provider(
        [
            [
                StreamEvent(tool_calls=[read_call()]),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="好了"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("看看 a.py")

    drive(Agent(provider, registry_with(CountingTool())), conv)

    assert [[m.role for m in call] for call in provider.calls] == [
        ["user"],
        ["user", "assistant", "tool"],
    ]


def test_tool_result_reaches_the_tool_event(make_provider) -> None:
    """工具跑出来的内容要原样交给界面（界面自己决定截几行）。"""
    provider = make_provider(
        [
            [StreamEvent(tool_calls=[read_call()]), StreamEvent(done=True)],
            [StreamEvent(text="好了"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("看看 a.py")

    events = drive(Agent(provider, registry_with(CountingTool())), conv)

    end = find_end(events)
    assert end.result == '读到了 {"path": "a.py"}'
    assert end.is_error is False
    # 结束帧也带 args：界面要用它把整行重画一遍（`● name(args)` → `⎿ 结果`）。
    assert end.args == '{"path": "a.py"}'


def test_tool_failure_is_replayed_as_an_error_result(make_provider) -> None:
    """工具失败走**结果**通道，不是异常（N4/AC12）：会话不断，模型能据此调整。

    这里用空的注册中心，走的是真实的「未知工具」分支——不造假，直接验真路径。
    """
    provider = make_provider(
        [
            [
                StreamEvent(tool_calls=[ToolCall(id="c1", name="no_such", input="{}")]),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="那我换个办法"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("试试")

    events = drive(Agent(provider, Registry()), conv)

    assert find_end(events).is_error is True
    assert "未知工具" in find_end(events).result
    # 错误结果照样入历史，而且**带着 is_error 标记**回灌——模型才知道那是失败。
    assert conv.messages()[2].tool_results[0].is_error is True
    assert trace(events)[-2:] == ["text:那我换个办法", "done"]


def test_long_tool_args_are_truncated_in_the_preview_only(make_provider) -> None:
    """参数预览截断**只影响界面**，真正执行时用的是完整入参。

    这两件事混起来就是一类很难查的 bug：界面上看着参数是对的，执行却用了半截。
    """
    long_input = json.dumps({"path": "x" * 200})
    provider = make_provider(
        [
            [
                StreamEvent(
                    tool_calls=[ToolCall(id="c1", name="read_file", input=long_input)]
                ),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="好了"), StreamEvent(done=True)],
        ]
    )
    tool = CountingTool()
    conv = Conversation()
    conv.add_user("读个超长路径")

    events = drive(Agent(provider, registry_with(tool)), conv)

    start = next(e.tool for e in events if e.tool and e.tool.phase is Phase.START)
    assert start.args == long_input[:MAX_ARGS_PREVIEW] + "…"
    # 工具收到的是**原封不动**的完整参数。
    assert tool.calls == [long_input]


# ────────────────────────── 参数预览的可读性 ──────────────────────────
#
# 这一组是居居在真机上发现的：工具行里一条中文路径显示成 `琪琪作业`。
# 功能没问题（`json.loads` 解析结果一样），坏的是**给人看的那一行**。
#
# 根因在 `anthropic_provider._tool_calls_of`（`json.dumps` 漏了 `ensure_ascii=False`），
# 已经在那儿修了。这里的还原是**防御性**的：`base_url` 由用户填，第三方网关有可能会
# 转义。实测过 DeepSeek 的 OpenAI 兼容端点直接发 UTF-8，所以这条路径目前不会被触发。


def test_preview_decodes_unicode_escapes() -> None:
    """`\\uXXXX` 转义要还原成真字符。

    `write_file` 那类的参数里，路径和内容都可能是中文——屏幕上印出六个字符一组的
    转义码，等于这条工具行没写给人看。
    """
    escaped = json.dumps({"path": "琪琪作业/快排.txt"})  # ensure_ascii=True，默认值

    assert "\\u742a" in escaped  # 前提：dumps 确实转义了
    assert preview_args(escaped) == '{"path": "琪琪作业/快排.txt"}'


def test_preview_leaves_unescaped_json_alone() -> None:
    """**没有**转义时逐字节原样返回，不顺手重新排版。

    模型发 `{"a":1}` 还是 `{"a": 1}` 是它的自由，预览没有理由替它统一——而且
    「先解析再 dumps」这条路对一次几 MB 的 `write_file` 是白解析一遍。
    """
    plain = '{"path":"a.py","content":"hello"}'

    assert preview_args(plain) == plain


def test_preview_falls_back_when_the_json_is_broken() -> None:
    """带转义但**不是合法 JSON** 时原样返回，绝不抛。

    模型偶尔真的会发截断的 JSON。这一层是给人看的，不该在画之前先把界面搞崩——
    那种情况由工具侧报「参数不是合法的 JSON」，不归预览管。

    注意 `\\u` 那三个字符本身也可能只是字符串里的普通内容（写正则、写转义序列），
    所以「含 `\\u`」只决定**要不要试**，不决定结果对不对。
    """
    for broken in ['{"path": "\\u742a', "\\u742a\\u742a", '["\\u742a"]']:
        assert preview_args(broken) == broken


def test_preview_truncates_after_decoding_not_before() -> None:
    """先还原、后截断。

    反过来的话，一条中文路径会被按六倍长度算，还没上屏就被砍掉了——而截断的**目的**
    是别让工具行撑成好几行，那本来就是按显示宽度算的账。
    """
    path = "作业/" * 20  # 转义后 480 字符，还原后 60 字符
    escaped = json.dumps({"path": path})

    preview = preview_args(escaped)

    assert "\\u" not in preview
    assert preview.endswith("…") is False  # 60 字符没超 80
    assert path in preview


# ────────────────────────── AC9：单轮上限 ──────────────────────────


def test_second_request_asking_for_tools_is_ignored(make_provider) -> None:
    """请求#2 又要求调工具？**不执行**，拿它已经说出的正文收尾（AC9）。

    这是「一次输入 = 一次工具批量」这条可预期性的落点。放开的话模型可以无限
    自问自答地调下去，一轮对话永远不结束——用户按一次 Enter 就可能烧掉一整晚。
    """
    provider = make_provider(
        [
            [StreamEvent(tool_calls=[read_call()]), StreamEvent(done=True)],
            [
                StreamEvent(text="我还想再读一个"),
                StreamEvent(tool_calls=[read_call("b.py", "call_2")]),
                StreamEvent(done=True),
            ],
        ]
    )
    tool = CountingTool()
    conv = Conversation()
    conv.add_user("看看 a.py")

    events = drive(Agent(provider, registry_with(tool)), conv)

    # 只执行了一次，也只画了一行工具。
    assert tool.calls == ['{"path": "a.py"}']
    assert trace(events).count("tool:start:read_file") == 1
    # 第二次请求要的调用**没有**进历史。
    assert [m.role for m in conv.messages()] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]


def test_tool_limit_gets_its_own_message(make_provider) -> None:
    """请求#2 又要工具、自己一个字没说 → 给**单轮上限提示**，不是「空回复」。

    AC9 的原话是「本轮以最终答复（**或单轮上限提示**）结束」，两者得能分开。
    说成「模型返回了空回复」是冤枉它：它明明说了，是想接着调工具，是我们主动停的手。
    用户看到那句只会以为模型抽风，不知道再发一条就能继续。
    """
    provider = make_provider(
        [
            [StreamEvent(tool_calls=[read_call()]), StreamEvent(done=True)],
            [
                StreamEvent(tool_calls=[read_call("b.py", "call_2")]),
                StreamEvent(done=True),
            ],
        ]
    )
    tool = CountingTool()
    conv = Conversation()
    conv.add_user("先读 a 再读 b")

    events = drive(Agent(provider, registry_with(tool)), conv)

    # 比消息文本而不是比异常对象：`RuntimeError` 没定义 `__eq__`，两个内容一样的
    # 实例永远不相等，那样写这条断言会**必定**失败，而且失败信息看着一模一样。
    assert [str(e.err) for e in events if e.err is not None] == [TOOL_LIMIT]
    assert TOOL_LIMIT != EMPTY_REPLY
    # 提示归提示，第二个工具**确实**没被执行。
    assert tool.calls == ['{"path": "a.py"}']


# ────────────────────────── supports_tools ──────────────────────────


def test_tools_are_withheld_when_the_provider_says_so(make_provider) -> None:
    """`supports_tools` 为假时，**一个工具定义都不发**。

    这是 thinking 与工具互斥那条决策的落点（`docs/v2/plan.md` 文末）：Anthropic
    开了 thinking 就回不了带 tool_use 的回合，所以干脆不发工具定义，让它只能纯文本作答。
    """
    provider = make_provider(
        [StreamEvent(text="我只能聊天"), StreamEvent(done=True)],
        supports_tools=False,
    )
    conv = Conversation()
    conv.add_user("帮我读个文件")

    drive(Agent(provider, registry_with(CountingTool())), conv)

    # 注册中心里明明有工具，但一个都没发出去。
    assert provider.tools == [[]]


def test_tools_are_sent_when_the_provider_supports_them(make_provider) -> None:
    """反过来也要验：支持工具时定义**真的发出去了**，不然上面那条可能只是永远不发。"""
    provider = make_provider([StreamEvent(text="好"), StreamEvent(done=True)])
    conv = Conversation()
    conv.add_user("嗨")

    drive(Agent(provider, registry_with(CountingTool())), conv)

    assert [d.name for d in provider.received_tools] == ["read_file"]


# ────────────────────────── 纯文本回合（与 v1 等价） ──────────────────────────


def test_text_only_round_sends_one_request_and_writes_two_messages(
    make_provider,
) -> None:
    """不调工具时只发一次请求，行为与 v1 完全一致。"""
    provider = make_provider(
        [StreamEvent(text="你"), StreamEvent(text="好"), StreamEvent(done=True)]
    )
    conv = Conversation()
    conv.add_user("打个招呼")

    events = drive(Agent(provider, registry_with(CountingTool())), conv)

    assert trace(events) == ["text:你", "text:好", "done"]
    assert len(provider.calls) == 1
    assert conv.messages() == [
        Message(role="user", content="打个招呼"),
        Message(role="assistant", content="你好"),
    ]


# ────────────────────────── 失败路径 ──────────────────────────


def test_error_event_ends_the_turn_and_writes_no_history(make_provider) -> None:
    """上游出错：翻成 err 事件交出去，**这一轮不进历史**（F11/AC11）。"""
    boom = RuntimeError("鉴权失败")
    provider = make_provider([StreamEvent(text="半"), StreamEvent(err=boom)])
    conv = Conversation()
    conv.add_user("嗨")

    events = drive(Agent(provider, registry_with(CountingTool())), conv)

    assert trace(events) == ["text:半", "err:鉴权失败"]
    # 半截回复不入历史，也没有 done——界面据此走错误收尾，而不是「正常结束」。
    assert [m.role for m in conv.messages()] == ["user"]
    assert not any(e.done for e in events)


def test_empty_reply_is_reported_and_not_written_to_history(make_provider) -> None:
    """空回复**不入历史**。

    Anthropic 对 content 为空的消息直接返回 400，存进去会让**下一轮**莫名其妙地
    失败，而用户完全看不出这跟上一轮有关。与其埋一颗这种雷，不如当场把这一轮标成失败。
    """
    provider = make_provider([StreamEvent(done=True)])
    conv = Conversation()
    conv.add_user("嗨")

    events = drive(Agent(provider, registry_with(CountingTool())), conv)

    assert trace(events) == [f"err:{EMPTY_REPLY}"]
    assert [m.role for m in conv.messages()] == ["user"]


def test_empty_reply_after_tools_keeps_the_tool_rounds(make_provider) -> None:
    """工具跑完却一个字没说：工具那两条**留着**。

    它们本身就是完整可用的上下文（模型看得见自己调过什么），下一轮接着问就是了；
    一起丢掉反而会让模型莫名其妙地「忘了」刚才的操作。
    """
    provider = make_provider(
        [
            [StreamEvent(tool_calls=[read_call()]), StreamEvent(done=True)],
            [StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("看看 a.py")

    events = drive(Agent(provider, registry_with(CountingTool())), conv)

    assert trace(events)[-1] == f"err:{EMPTY_REPLY}"
    assert [m.role for m in conv.messages()] == ["user", "assistant", "tool"]


def test_whitespace_only_reply_counts_as_empty(make_provider) -> None:
    """只有空白也算空——与 v1 的判据一致（`reply.strip()`）。"""
    provider = make_provider([StreamEvent(text="  \n "), StreamEvent(done=True)])
    conv = Conversation()
    conv.add_user("嗨")

    events = drive(Agent(provider, registry_with(CountingTool())), conv)

    assert conv.messages()[-1].role == "user"
    assert any(e.err is not None for e in events)


# ────────────────────────── 取消穿透（原 test_tui_stream.py 那条） ──────────────────────────


def test_cancellation_propagates_through_the_agent(make_provider) -> None:
    """取消必须**原样抛出去**，不能被翻成 err 事件。

    这条是从 `tests/test_tui_stream.py` 搬过来的（那个模块随 `stream.py` 一起删）。
    覆盖不能跟着一起丢：吞掉取消的话，asyncio 会认为任务正常跑完了，用户按 Ctrl+C
    之后界面永远停在流式态，HTTP 流也不会被关——只能杀进程。
    """
    provider = make_provider(
        [StreamEvent(text="一"), StreamEvent(done=True)], delay=5.0
    )
    conv = Conversation()
    conv.add_user("嗨")
    seen: list[Event] = []

    async def scenario() -> None:
        async def consume() -> None:
            async for event in Agent(provider, registry_with(CountingTool())).run(conv):
                seen.append(event)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.02)  # 让它进到假流的第一个 await 上
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert seen == []  # 取消得早，一个增量都还没送出去
    assert provider.cancelled == 1  # 取消**真的**落在了生成器内部
    # 历史没被写脏：半截的回复不能留下。
    assert conv.messages() == [Message(role="user", content="嗨")]


def test_cancellation_between_the_two_requests_leaves_clean_history(
    make_provider,
) -> None:
    """在第一轮工具**执行期间**被取消：已经入历史的那条 assistant 回合原样留着。

    这是取消最难看的一种时机，值得钉死：assistant(tool_calls) 已经写进去了，而
    工具结果永远等不到。留下来的历史是 **user + assistant(只调工具、无正文)**——
    它本身合法（下一次请求模型会看到自己上次调了工具但没拿到结果），但要是这里
    再多写一条空的 assistant，就会撞上「空 content 被服务端 400」那条老账。

    为了让取消**精确**落在工具执行那一下，这里的工具是个永远不返回的假货，
    靠轮询事件流而不是靠定时——定时在快机器上会变成「碰运气」。
    """
    provider = make_provider(
        [
            [StreamEvent(tool_calls=[read_call()]), StreamEvent(done=True)],
            [StreamEvent(text="好了"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("看看 a.py")
    seen: list[Event] = []

    async def scenario() -> None:
        async def consume() -> None:
            registry = Registry()
            registry.register(HangingTool())
            async for event in Agent(provider, registry).run(conv):
                seen.append(event)

        task = asyncio.create_task(consume())
        # 一直等到工具开始执行那一帧——那一刻它正卡在 await 上。
        while not any(e.tool and e.tool.phase is Phase.START for e in seen):
            await asyncio.sleep(0)
        # 此刻 assistant 那条已经入历史了（工具是它后面才执行的）。
        assert [m.role for m in conv.messages()] == ["user", "assistant"]

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    msgs = conv.messages()
    assert [m.role for m in msgs] == ["user", "assistant"]
    # 留下来的每一条都**有内容**：那条 assistant 靠 tool_calls 撑着，正文是空的，
    # 但它不是一个「空回合」——空回合（正文和 tool_calls 都空）才是会被 400 的那种。
    assert msgs[1].tool_calls != []
    # 而且没有留下半截的最终答复。
    assert not any(m.role == "tool" for m in msgs)
