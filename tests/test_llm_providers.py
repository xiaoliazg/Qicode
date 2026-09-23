"""两个协议适配器的单测（T7、T8；F3、F4、F5、F8、F11；N7）。

T7/T8 当时是靠**实跑探针**验的：真的连了本地 Ollama、真的拿坏 key 打了一次上游。
那种验证能证明「此刻是对的」，但证明不了「以后还是对的」——上游 SDK 升个小版本、
谁顺手改一行分支，行为变了也没人拦。这里把那些结论固化成用例。

做法：把 `provider._client` 换成假客户端。假件只实现适配器**真正用到的那几个形状**
（`messages.stream(**params)`、`chat.completions.create(**params)`、事件对象上的
`.type` / `.delta.type` / `.delta.text` / `.choices[0].delta.content`），
不假装自己是个完整的 SDK——这样哪天适配器改用别的字段，用例会红，那正是我们想知道的。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from types import SimpleNamespace
from typing import Any, Self

import openai
import pytest

from qicode.config import ProviderConfig
from qicode.llm import (
    Message,
    StreamEvent,
    ToolCall,
    ToolDefinition,
    ToolResult,
    tool_input,
)
from qicode.llm.anthropic_provider import (
    MAX_TOKENS,
    THINKING_PARAMS,
    AnthropicProvider,
    _to_anthropic_messages,
)
from qicode.llm.openai_provider import OpenAIProvider, _to_sdk_messages
from qicode.prompt import system_prompt


def make_cfg(**over: Any) -> ProviderConfig:
    """造一份配置。密钥是假的——这些用例一个字节的网络都不碰。"""
    base: dict[str, Any] = {
        "name": "test",
        "protocol": "anthropic",
        "api_key": "sk-fake",
        "model": "test-model",
        "base_url": None,
        "thinking": False,
    }
    base.update(over)
    return ProviderConfig(**base)


async def collect(
    provider: Any,
    msgs: Sequence[Message] | None = None,
    tools: Sequence[ToolDefinition] | None = None,
) -> list[StreamEvent]:
    """把整条流收成一个列表。"""
    return [
        ev
        async for ev in provider.stream(
            list(msgs or [Message("user", "嗨")]), list(tools or [])
        )
    ]


def inject(provider: Any, client: Any) -> None:
    """把适配器里的 SDK 客户端换成假件。

    走 `setattr` 而不是 `provider._client = ...`：那个属性的类型标注是 SDK 的
    `AsyncAnthropic` / `AsyncOpenAI`，而假件只实现适配器真正用到的那几个形状，
     本来就不是完整的 SDK 客户端。这是测试**故意**的一次越界，就地写明。
    """
    # 参数标成 Any，所以这里 mypy 不会拦——换成具体类型就会报 assignment。
    provider._client = client


# ────────────────────────── 假件：Anthropic ──────────────────────────


def text_delta(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=text)
    )


def thinking_delta(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_delta",
        delta=SimpleNamespace(type="thinking_delta", thinking=text),
    )


def tool_use_block(call_id: str, name: str, args: dict[str, Any]) -> SimpleNamespace:
    """终态消息里的一个 `tool_use` 块（`input` 已经是解析好的对象）。"""
    return SimpleNamespace(type="tool_use", id=call_id, name=name, input=args)


def final_message(
    blocks: Sequence[Any], stop_reason: str = "end_turn"
) -> SimpleNamespace:
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks))


class FakeAnthropicStream:
    """`client.messages.stream(...)` 返回的那个上下文管理器。

    `hang=True` 时，吐完脚本里的事件就永远卡住——用来模拟「正等着下一个增量」的
    那一刻，好让取消真的落在适配器内部的 await 上。

    另外要能交出 `get_final_message()`：适配器在流读完之后靠它取这一轮的
    `stop_reason` 和内容块（工具调用就在里面）。这是 SDK 的既有接口，不是我们
    自创的——照抄教程只用 `async for` 的话，工具调用会一个都收不到，因为
    `input_json_delta` 只是参数碎片，完整对象只在终态消息里。
    """

    def __init__(
        self,
        events: Sequence[Any],
        error: Exception | None = None,
        hang: bool = False,
        final: Any = None,
    ) -> None:
        self._events = events
        self._error = error
        self._hang = hang
        self._final = final if final is not None else final_message([])
        self.closed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self.closed = True
        return False

    async def __aiter__(self) -> AsyncIterator[Any]:
        for event in self._events:
            yield event
        if self._hang:
            await asyncio.Event().wait()  # 永不返回，等外面来取消
        if self._error is not None:
            raise self._error

    async def get_final_message(self) -> Any:
        return self._final


class FakeAnthropicClient:
    def __init__(
        self,
        events: Sequence[Any],
        error: Exception | None = None,
        hang: bool = False,
        final: Any = None,
    ) -> None:
        self.params: dict[str, Any] | None = None
        self.stream_obj = FakeAnthropicStream(events, error, hang, final)
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **params: Any) -> FakeAnthropicStream:
        self.params = params
        return self.stream_obj


def anthropic_with(
    events: Sequence[Any], final: Any = None, **cfg_over: Any
) -> tuple[AnthropicProvider, FakeAnthropicClient]:
    provider = AnthropicProvider(make_cfg(**cfg_over))
    fake = FakeAnthropicClient(events, final=final)
    inject(provider, fake)
    return provider, fake


# ────────────────────────── 假件：OpenAI 兼容 ──────────────────────────


def chunk(
    content: str | None, choices: int = 1, tool_calls: Sequence[Any] | None = None
) -> SimpleNamespace:
    """一个流式分块。

    `delta` 上**始终**带 `tool_calls`（哪怕用不着）：真实的 SDK 是 pydantic 模型，
    这个字段永远存在，默认 None。假件少写一个字段，适配器就会在 AttributeError 上
    栽跟头，而那只会污染别的用例的结论。
    """
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=tool_calls)
            )
            for _ in range(choices)
        ]
    )


def tc_delta(
    index: int,
    call_id: str | None = None,
    name: str | None = None,
    args: str | None = None,
) -> SimpleNamespace:
    """一片工具调用增量。

    三段的到达规律不一样，这正是不容易写对的地方：`id` / `name` **只在第一片**里
    出现，`arguments` 则每片都是一段、必须首尾相接拼起来。
    """
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=args),
    )


class FakeOpenAIStream:
    """`create()` 返回的那个流对象。

    适配器对它的要求有三条：能 `async with`、能 `async for`、退出时关掉。
    第三条是**回归点**——上一轮就是靠它逮到「提前 break 时连接不关」的。
    """

    def __init__(
        self, chunks: Sequence[Any], error: Exception | None = None, hang: bool = False
    ) -> None:
        self._chunks = chunks
        self._error = error
        self._hang = hang
        self.closed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self.closed = True
        return False

    async def __aiter__(self) -> AsyncIterator[Any]:
        for item in self._chunks:
            yield item
        if self._hang:
            await asyncio.Event().wait()
        if self._error is not None:
            raise self._error


class FakeOpenAIClient:
    def __init__(
        self, chunks: Sequence[Any], error: Exception | None = None, hang: bool = False
    ) -> None:
        self.params: dict[str, Any] | None = None
        self.stream_obj = FakeOpenAIStream(chunks, error, hang)
        self.completions = SimpleNamespace(create=self._create)
        self.chat = SimpleNamespace(completions=self.completions)

    async def _create(self, **params: Any) -> FakeOpenAIStream:
        self.params = params
        return self.stream_obj


def openai_with(
    chunks: Sequence[Any], **cfg_over: Any
) -> tuple[OpenAIProvider, FakeOpenAIClient]:
    provider = OpenAIProvider(make_cfg(protocol="openai", **cfg_over))
    fake = FakeOpenAIClient(chunks)
    inject(provider, fake)
    return provider, fake


# ────────────────────────── F4：system prompt 注入 ──────────────────────────


def test_anthropic_sends_system_as_top_level_param() -> None:
    """F4：Anthropic 的 system 是顶层参数，不能混进 messages 里。

    混进去的后果不是报错，是**被当成普通对话内容**——模型看得见，
    但它不再是「系统指令」，权重完全不同。
    """
    provider, fake = anthropic_with([text_delta("好")])

    asyncio.run(
        collect(provider, [Message("user", "你好"), Message("assistant", "在")])
    )

    assert fake.params is not None
    assert fake.params["system"] == system_prompt("test", "test-model")
    assert fake.params["messages"] == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "在"},
    ]
    assert fake.params["model"] == "test-model"
    assert fake.params["max_tokens"] == MAX_TOKENS


def test_openai_sends_system_as_first_message() -> None:
    """F4：OpenAI 兼容侧没有顶层 system，system prompt 是 messages 的第一条。"""
    provider, fake = openai_with([chunk("好")])

    asyncio.run(collect(provider, [Message("user", "你好")]))

    assert fake.params is not None
    messages = fake.params["messages"]
    assert messages[0] == {
        "role": "system",
        "content": system_prompt("test", "test-model"),
    }
    assert messages[1] == {"role": "user", "content": "你好"}
    assert fake.params["stream"] is True


@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
def test_system_prompt_tells_the_model_which_model_it_is(protocol: str) -> None:
    """F4：两条协议都要把「你是谁、跑在什么模型上」交给模型。

    这两个值只有我们这边知道——它们是本地配置里的字段，模型自己看不到。不写进
    提示词，用户一问「你是什么模型」它就只会答「我不掌握这个信息」（实测原话），
    再不然凭训练数据编一个。

    对**两条协议**分别验，是因为注入位置本来就不同（Anthropic 走顶层 `system`，
    OpenAI 兼容走 messages 首条）；将来加第三个适配器时漏掉，这里能拦下来。
    """
    identity = {"name": "my-deepseek", "model": "deepseek-chat"}
    # 标成 Any 是故意的：两条协议返回的是两套不同的假件类型，这里只关心
    # `params` 里装了什么，不想为了让 mypy 满意去写一个联合类型。
    provider: Any
    fake: Any
    if protocol == "anthropic":
        provider, fake = anthropic_with([text_delta("好")], **identity)
    else:
        provider, fake = openai_with([chunk("好")], **identity)

    asyncio.run(collect(provider))

    assert fake.params is not None
    sent = (
        fake.params["system"]
        if protocol == "anthropic"
        else fake.params["messages"][0]["content"]
    )
    # 两个值都得在，而且得是**本次**这两个——只验其中一个的话，
    # 把另一个写死成常量也照样能过。
    assert "my-deepseek" in sent
    assert "deepseek-chat" in sent


# ────────────────────────── F5：thinking 识别但不渲染 ──────────────────────────


def test_anthropic_drops_thinking_deltas() -> None:
    """F5/AC5：思考增量一个字都不能漏进正文。

    这条是「照抄教程最容易踩」的地方：`content_block_delta` 里既有 text_delta
    也有 thinking_delta，只判断外层事件类型的话，思考内容会被当成正文流到屏幕上。
    """
    provider, _ = anthropic_with(
        [
            thinking_delta("让我想想……"),
            text_delta("答案是 42"),
            thinking_delta("再确认一下"),
            text_delta("。"),
        ]
    )

    events = asyncio.run(collect(provider))

    assert [ev.text for ev in events if ev.text] == ["答案是 42", "。"]
    assert "".join(ev.text for ev in events) == "答案是 42。"
    assert events[-1].done is True


def test_anthropic_thinking_flag_controls_the_param() -> None:
    """F5：`thinking: true` 才发参数，false 时**一个字节都不许多发**。

    `display` 必须是 `summarized`：它的缺省值是 `omitted`，那样服务端根本不发思考
    增量，我们也就没有「识别到但不渲染」的机会了。
    """
    provider, fake = anthropic_with([text_delta("好")], thinking=True)
    asyncio.run(collect(provider))
    assert fake.params is not None
    assert fake.params["thinking"] == THINKING_PARAMS
    assert fake.params["thinking"]["type"] == "adaptive"

    provider, fake = anthropic_with([text_delta("好")], thinking=False)
    asyncio.run(collect(provider))
    assert fake.params is not None
    assert "thinking" not in fake.params


def test_openai_ignores_thinking_flag() -> None:
    """OpenAI 协议没有 thinking 字段，配了也不发——不猜各家网关的私有形状。"""
    provider, fake = openai_with([chunk("好")], thinking=True)

    asyncio.run(collect(provider))

    assert fake.params is not None
    assert "thinking" not in fake.params


# ────────────────────────── F3：base_url 覆盖 ──────────────────────────


@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
def test_base_url_reaches_the_sdk(protocol: str) -> None:
    """F3：配了 base_url 就要真的传给 SDK。

    这一条只验「传下去了」——真能不能收发是 T14 端到端的事（已验证：DeepSeek
    的 anthropic 兼容端点、本地 Ollama 的 openai 端点都跑通了）。
    """
    cfg = make_cfg(protocol=protocol, base_url="https://example.invalid/v1")
    provider = (
        AnthropicProvider(cfg) if protocol == "anthropic" else OpenAIProvider(cfg)
    )

    assert str(provider._client.base_url).startswith("https://example.invalid/v1")


@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
def test_no_base_url_keeps_the_official_endpoint(protocol: str) -> None:
    """不配 base_url 时走 SDK 内置端点，别塞一个空串进去。"""
    cfg = make_cfg(protocol=protocol, base_url=None)
    provider = (
        AnthropicProvider(cfg) if protocol == "anthropic" else OpenAIProvider(cfg)
    )

    assert "example.invalid" not in str(provider._client.base_url)


# ────────────────────────── F8：正文增量 ──────────────────────────


def test_openai_yields_text_in_order_then_done() -> None:
    """F8：正文按到达顺序吐出，最后恰好一个 done。"""
    provider, _ = openai_with([chunk("你"), chunk("好"), chunk("呀")])

    events = asyncio.run(collect(provider))

    assert [ev.text for ev in events] == ["你", "好", "呀", ""]
    assert [ev.done for ev in events] == [False, False, False, True]


def test_openai_skips_empty_choices_and_null_content() -> None:
    """兼容实现的两种怪脾气，都不能把一轮对话判成失败。

    - 有的后端会额外发一个 `choices` 为空的收尾块（只带用量统计）；
      直接取 `[0]` 就是 IndexError。
    - 有的后端首个块 `delta.content` 是 `None`（只有 role）；把它当成增量会往
      正文里塞一个 `"None"`。
    """
    provider, _ = openai_with(
        [
            chunk(None),  # 只有 role，没有内容
            SimpleNamespace(choices=[]),  # 只带用量的收尾块
            chunk("正文"),
        ]
    )

    events = asyncio.run(collect(provider))

    assert [ev.text for ev in events if ev.text] == ["正文"]
    assert events[-1].done is True


# ────────────────────────── F11：错误翻译成事件 ──────────────────────────


@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
def test_upstream_error_becomes_an_err_event(protocol: str) -> None:
    """F11/AC11：上游炸了要翻译成 err 事件，**不能**让异常冒出去。

    冒出去的后果是界面那层被打崩——用户看到的是一屏 traceback 加一个死掉的进程，
    而不是「这轮失败了，你接着问」。
    """
    boom = RuntimeError("invalid api key")
    provider: Any
    if protocol == "anthropic":
        provider = AnthropicProvider(make_cfg())
        inject(provider, FakeAnthropicClient([text_delta("半句话")], error=boom))
    else:
        provider = OpenAIProvider(make_cfg(protocol="openai"))
        inject(provider, FakeOpenAIClient([chunk("半句话")], error=boom))

    events = asyncio.run(collect(provider))  # 不抛

    assert events[-1].err is boom
    # 出错就到此为止：不能再补一个 done，否则上层会以为本轮正常结束了。
    assert not any(ev.done for ev in events)
    # 出错之前已经收到的增量照常保留，界面能显示半截回复。
    assert [ev.text for ev in events if ev.text] == ["半句话"]


# ────────────────────────── N7：提前退出不泄漏连接 ──────────────────────────


@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
def test_early_break_still_closes_the_stream(protocol: str) -> None:
    """N7：调用方提前跳出（取消，或自己 break）时，流必须被关掉。

    OpenAI 的 `AsyncStream.close()` 文档写的是「读完才自动调用」——提前 break
    时它**不会**自己关，HTTP 连接就一直挂着。所以适配器必须有 `async with`。
    这条用例就是那个 bug 的回归测试：去掉 async with，`closed` 会停在 False。
    """
    if protocol == "anthropic":
        provider: Any = AnthropicProvider(make_cfg())
        fake: Any = FakeAnthropicClient([text_delta("一"), text_delta("二")])
    else:
        provider = OpenAIProvider(make_cfg(protocol="openai"))
        fake = FakeOpenAIClient([chunk("一"), chunk("二")])
    inject(provider, fake)
    stream_obj = fake.stream_obj

    async def half() -> list[str]:
        got = []
        async for ev in provider.stream([Message("user", "嗨")], []):
            if ev.text:
                got.append(ev.text)
                break  # 只读一个增量就走人
        return got

    assert asyncio.run(half()) == ["一"]
    assert stream_obj.closed is True


@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
def test_cancellation_is_not_swallowed(protocol: str) -> None:
    """N7：取消信号必须原样抛出去，不能混进宽 catch 里被吃掉。

    适配器里那两个 `except Exception` 是宽 catch（上游边界，故意的）。但
    `CancelledError` 在 Python 3.8 之后继承自 `BaseException`——万一哪天有人
    图省事改成 `except BaseException`，取消就断了：`task.cancel()` 之后任务
    会被当成「正常跑完了」，界面永远停在流式态，只能杀进程。

    关键在**取消要落在适配器内部的 await 上**：假流吐完第一个增量就永远卡住，
    这时 cancel 才会在 `async for` 那一行抛出，真的经过适配器的 except。
    """
    provider: Any
    if protocol == "anthropic":
        provider = AnthropicProvider(make_cfg())
        inject(provider, FakeAnthropicClient([text_delta("一")], hang=True))
    else:
        provider = OpenAIProvider(make_cfg(protocol="openai"))
        inject(provider, FakeOpenAIClient([chunk("一")], hang=True))

    seen: list[StreamEvent] = []

    async def run() -> None:
        async def consume() -> None:
            async for ev in provider.stream([Message("user", "嗨")], []):
                seen.append(ev)

        task = asyncio.create_task(consume())
        while not seen:  # 等它跑到假流卡住的那一行
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())

    # 取消**不是**错误：不能翻成 err 事件，否则界面会显示一条「出错」，
    # 而用户只是自己打断了它。
    assert [ev.text for ev in seen] == ["一"]
    assert not any(ev.err is not None for ev in seen)


# ────────────────── 工具调用参数的解析兜底（`docs/v2/checklist.md` T1 那条） ──────────────────


def test_tool_input_parses_a_well_formed_object() -> None:
    call = ToolCall(id="call_1", name="read_file", input='{"path": "a.py"}')

    assert tool_input(call) == {"path": "a.py"}


@pytest.mark.parametrize(
    "raw",
    [
        "不是 json",  # 模型偶尔真的会发出这种
        '{"path": "a.py"',  # 截断
        '{"a": 1,}',  # 多一个逗号
        "[1, 2]",  # 合法 JSON，但不是对象
        "null",
        "",
    ],
)
def test_tool_input_never_raises(raw: str) -> None:
    """回灌路径上**不许抛**（N4）。

    Anthropic 的 `tool_use` 块要的 `input` 是一个对象，适配器必须 `json.loads` 一次。
    那一下要是抛出去，崩的不是一条工具调用，而是**整轮请求**——界面上会变成一句
    看不懂的异常。所以解析不出来就给个空对象：工具侧本来就已经回了「参数不是合法
    JSON」的结构化错误，两边正好对得上。
    """
    call = ToolCall(id="call_1", name="read_file", input=raw)

    assert tool_input(call) == {}


# ────────────────── 工具定义注入（T10；F3、F7） ──────────────────


def tool_def(name: str = "read_file") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="读文件",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )


def test_anthropic_sends_tools_with_input_schema() -> None:
    """Anthropic 的字段叫 `input_schema`，而且**不套** `{"type": "function"}`。

    和 OpenAI 侧长得像但两处都不同，是两条协议各自的规矩。抄错任何一个，
    服务端都会以「参数不认识 / 缺字段」为由把整个请求打回去——不是工具失灵，
    是这一轮根本发不出去。
    """
    provider, fake = anthropic_with([text_delta("好")])

    asyncio.run(collect(provider, tools=[tool_def()]))

    assert fake.params is not None
    assert fake.params["tools"] == [
        {
            "name": "read_file",
            "description": "读文件",
            "input_schema": tool_def().input_schema,
        }
    ]


def test_anthropic_omits_tools_when_none_given() -> None:
    """没有工具就**不发**这个参数，而不是发一个空数组。"""
    provider, fake = anthropic_with([text_delta("好")])

    asyncio.run(collect(provider, tools=[]))

    assert fake.params is not None
    assert "tools" not in fake.params


def test_openai_sends_tools_wrapped_in_function() -> None:
    """OpenAI 侧要包一层 `{"type": "function"}`，参数字段叫 `parameters`。"""
    provider, fake = openai_with([chunk("好")])

    asyncio.run(collect(provider, tools=[tool_def()]))

    assert fake.params is not None
    assert fake.params["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "读文件",
                "parameters": tool_def().input_schema,
            },
        }
    ]


def test_openai_omits_tools_via_the_sdk_sentinel() -> None:
    """没有工具时传的是 SDK 的 `omit` 哨兵（「这个参数根本不要发」），不是空列表。

    有些兼容网关对 `tools: []` 直接报错，所以「空就不发」这件事必须落到**不发**上，
    而不只是「发了个空的」。
    """
    provider, fake = openai_with([chunk("好")])

    asyncio.run(collect(provider, tools=[]))

    assert fake.params is not None
    assert fake.params["tools"] is openai.omit


# ────────────────── supports_tools（T10；thinking 与工具互斥） ──────────────────


@pytest.mark.parametrize(("thinking", "expected"), [(False, True), (True, False)])
def test_anthropic_supports_tools_is_the_inverse_of_thinking(
    thinking: bool, expected: bool
) -> None:
    """开了 thinking 就不能带工具——理由见 `qicode.llm.Provider.supports_tools`。

    这条是**显式声明**：上层据它决定传不传 `tools`。适配器不会偷偷把 `tools` 丢掉，
    那样用户只会看到「工具莫名其妙不工作」，无处可查。
    """
    assert AnthropicProvider(make_cfg(thinking=thinking)).supports_tools is expected


def test_openai_supports_tools_regardless_of_thinking() -> None:
    """这条链路本来就不发 thinking 参数（v1 的结论），所以工具一直可用。"""
    cfg = make_cfg(protocol="openai", thinking=True)

    assert OpenAIProvider(cfg).supports_tools is True


# ────────────────── T11：anthropic 解析工具调用 ──────────────────


def test_anthropic_yields_tool_calls_before_done() -> None:
    """工具调用在 `done` **之前**一次性给全，上层才能「见 done 就收工」。

    注意 `input` 是**字符串**（`ToolCall.input` 的约定），不是对象——
    这里恰好是 dumps 回来的，看着像原样，其实那一步必须做。
    """
    final = final_message(
        [tool_use_block("call_1", "read_file", {"path": "a.py"})],
        stop_reason="tool_use",
    )
    provider, _ = anthropic_with([text_delta("我看一下")], final=final)

    events = asyncio.run(collect(provider))

    assert [ev.text for ev in events if ev.text] == ["我看一下"]
    assert events[-2].tool_calls == [
        ToolCall(id="call_1", name="read_file", input='{"path": "a.py"}')
    ]
    assert events[-1].done is True
    assert events[-1].tool_calls == []


def test_anthropic_keeps_non_ascii_tool_args_readable() -> None:
    """参数里的非 ASCII 字符**不许**被转义成 `\\uXXXX`。

    真机上发现的：一条中文路径 `/Users/me/琪琪作业/快排.txt` 在工具行里显示成
    `/Users/me/\\u742a\\u742a\\u4f5c\\u4e1a/...`。起因就是这里 `json.dumps` 漏了
    `ensure_ascii=False`——它的默认值是 `True`，意思是「输出只准落在 ASCII 里」。

    坏的不是功能（`json.loads` 两种写法解析结果一样，工具拿到的路径是对的），
    坏的是**给人看的那一行**：工具行印转义码，紧挨着的结果行回显真字符，同一块里
    两种写法打架，看着像两个不同的文件。

    JSON 本身就是 UTF-8 编码的，这层转义是给「只能跑 ASCII 的老通道」准备的保险，
    我们没有这个约束。
    """
    final = final_message(
        [tool_use_block("call_1", "write_file", {"path": "琪琪作业/快排.txt"})],
        stop_reason="tool_use",
    )
    provider, _ = anthropic_with([], final=final)

    events = asyncio.run(collect(provider))

    assert events[-2].tool_calls == [
        ToolCall(id="call_1", name="write_file", input='{"path": "琪琪作业/快排.txt"}')
    ]


def test_anthropic_finds_tool_calls_only_in_the_final_message() -> None:
    """工具调用**不是**从流式增量里拼的，只在终态消息里取。

    `input_json_delta` 只是参数碎片，自己拼容易错；SDK 的累加器已经把成品放在
    `get_final_message()` 里了。这条用例正是钉住这个选择——把假流的事件里也塞一份
    碎片，断言最终**只有**终态消息那一份被采纳。
    """
    fragments = [
        SimpleNamespace(
            type="content_block_delta",
            delta=SimpleNamespace(type="input_json_delta", partial_json='{"path":'),
        )
    ]
    final = final_message(
        [tool_use_block("call_1", "read_file", {"path": "a.py"})],
        stop_reason="tool_use",
    )
    provider, _ = anthropic_with([*fragments], final=final)

    events = asyncio.run(collect(provider))

    assert [ev.tool_calls for ev in events if ev.tool_calls] == [
        [ToolCall(id="call_1", name="read_file", input='{"path": "a.py"}')]
    ]


def test_anthropic_ignores_tool_blocks_when_stop_reason_says_otherwise() -> None:
    """`stop_reason` 不是 `tool_use` 就不执行——哪怕内容里躺着 tool_use 块。

    `max_tokens` 截断时也会留下这种块，而它的 input 是**残缺**的。拿去执行只会
    换来一句莫名其妙的参数错误，还不如让这一轮以正文结束，模型下一轮自己重来。
    """
    final = final_message(
        [tool_use_block("call_1", "bash", {"command": "ls"})],
        stop_reason="max_tokens",
    )
    provider, _ = anthropic_with([text_delta("话说到一半")], final=final)

    events = asyncio.run(collect(provider))

    assert not any(ev.tool_calls for ev in events)
    assert events[-1].done is True


# ────────────────── T11：anthropic 历史回灌 ──────────────────


def test_anthropic_message_conversion_covers_all_three_rounds() -> None:
    """三种回合的转换各断言一次（T11 的第 4 条）。

    最要紧的是第三条：`tool_result` 进的是 **user** 消息。照 OpenAI 的习惯写成
    `{"role": "tool"}` 的话，Anthropic 不认识这个角色，**整轮请求**当场失败。
    """
    sent = _to_anthropic_messages(
        [
            Message(role="user", content="看看"),
            Message(
                role="assistant",
                content="我读一下",
                tool_calls=[
                    ToolCall(id="call_1", name="read_file", input='{"path": "a.py"}')
                ],
            ),
            Message(
                role="tool",
                tool_results=[
                    ToolResult(tool_call_id="call_1", content="内容", is_error=False)
                ],
            ),
            Message(role="assistant", content="看完了"),
        ]
    )

    assert sent[0] == {"role": "user", "content": "看看"}
    assert sent[1] == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "我读一下"},
            {
                "type": "tool_use",
                "id": "call_1",
                "name": "read_file",
                # 对象，不是字符串——与 `ToolCall.input` 相反，这一步就是把它解回去。
                "input": {"path": "a.py"},
            },
        ],
    }
    assert sent[2] == {
        "role": "user",  # ← 协议规定，不是笔误
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "call_1",
                "content": "内容",
                "is_error": False,
            }
        ],
    }
    assert sent[3] == {"role": "assistant", "content": "看完了"}


def test_anthropic_skips_the_text_block_when_preamble_is_empty() -> None:
    """只调工具、一句话不说时，**不能**塞一个空的 text 块。

    服务端会以 `text content blocks must be non-empty` 直接 400。空正文就整个不放，
    此时 content 数组里只剩 tool_use 块，这是合法的。
    """
    sent = _to_anthropic_messages(
        [
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c", name="bash", input="{}")],
            )
        ]
    )

    assert sent[0]["content"] == [
        {"type": "tool_use", "id": "c", "name": "bash", "input": {}}
    ]


@pytest.mark.parametrize("bad", ["不是 json", '{"path": "a.py"', "[1, 2]", ""])
def test_anthropic_replay_survives_illegal_tool_input(bad: str) -> None:
    """回灌路径**不许抛**（N4）。

    这里要的是对象，所以必须解析一次；模型偶尔真的会发出非法 JSON。那一下要是抛
    出去，崩的不是一条工具调用，而是**整轮请求**。给个空对象即可——工具侧本来
    就已经回了「参数不是合法 JSON」的结构化错误，两边正好对得上。
    """
    sent = _to_anthropic_messages(
        [
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c", name="bash", input=bad)],
            )
        ]
    )

    assert sent[0]["content"][0]["input"] == {}


# ────────────────── T12：openai 解析工具调用 ──────────────────


def test_openai_accumulates_interleaved_tool_call_fragments() -> None:
    """两个工具的**分片交错**到达，也要各自拼对。

    真实的到达顺序就是这样：先给每个工具的 id/name，然后 arguments 一段一段来。
    按 index 归拢是唯一拼得对的办法——按到达顺序拼，两个工具的参数会搅在一起。
    """
    provider, _ = openai_with(
        [
            chunk(None, tool_calls=[tc_delta(0, call_id="c1", name="read_file")]),
            chunk(None, tool_calls=[tc_delta(1, call_id="c2", name="bash")]),
            chunk(None, tool_calls=[tc_delta(0, args='{"path":')]),
            chunk(None, tool_calls=[tc_delta(1, args='{"command": "ls"}')]),
            chunk(None, tool_calls=[tc_delta(0, args=' "a.py"}')]),
        ]
    )

    events = asyncio.run(collect(provider))

    calls = events[-2].tool_calls
    assert [c.id for c in calls] == ["c1", "c2"]
    assert [c.name for c in calls] == ["read_file", "bash"]
    assert calls[0].input == '{"path": "a.py"}'
    assert calls[1].input == '{"command": "ls"}'
    assert events[-1].done is True


def test_openai_output_order_follows_index_not_arrival() -> None:
    """输出按 index 排序，不按到达顺序——顺序飘忽会让「模型看到的调用顺序」
    和「它自己发起的顺序」对不上，回灌下去它就对不上账。"""
    provider, _ = openai_with(
        [
            chunk(None, tool_calls=[tc_delta(1, call_id="c2", name="bash")]),
            chunk(None, tool_calls=[tc_delta(0, call_id="c1", name="read_file")]),
        ]
    )

    events = asyncio.run(collect(provider))

    assert [c.id for c in events[-2].tool_calls] == ["c1", "c2"]


def test_openai_normalises_empty_arguments() -> None:
    """无参工具的 arguments 是空串（连分片都没有），回灌时必须是合法 JSON。

    `"arguments": ""` 会被严格端点判成 400，所以归一到 `"{}"`（`docs/v2/plan.md`
    「空参数归一」）。
    """
    provider, _ = openai_with(
        [chunk(None, tool_calls=[tc_delta(0, call_id="c1", name="bash")])]
    )

    events = asyncio.run(collect(provider))

    assert events[-2].tool_calls[0].input == "{}"


def test_openai_ignores_a_truncated_fragment_without_id_or_name() -> None:
    """网关把第一片（带 id/name 的那片）吞了时，不能丢一句看不懂的 KeyError。

    给空串走正常路径：空 name 在注册中心那里会变成一条「未知工具」的结构化错误，
    模型看得懂，会话照常继续。
    """
    provider, _ = openai_with([chunk(None, tool_calls=[tc_delta(0, args="{}")])])

    events = asyncio.run(collect(provider))

    assert events[-2].tool_calls == [ToolCall(id="", name="", input="{}")]
    assert events[-1].done is True


def test_openai_no_tool_calls_means_no_event() -> None:
    """纯文本回合不该冒出一个空的 tool_calls 事件。"""
    provider, _ = openai_with([chunk("你好")])

    events = asyncio.run(collect(provider))

    assert not any(ev.tool_calls for ev in events)
    assert events[-1].done is True


# ────────────────── T12：openai 历史回灌 ──────────────────


def test_openai_message_conversion_covers_all_three_rounds() -> None:
    """三种回合各断言一次。注意工具结果那一条与前两种**不是一条对一条**：
    几个结果就发几条 `{"role": "tool"}`，一个回合展开成了两条消息。"""
    sent = _to_sdk_messages  # 先取个短名，下面按回合分开调，好逐条断言
    user = sent(Message(role="user", content="看看"))
    assistant_calls = sent(
        Message(
            role="assistant",
            content="我读一下",
            tool_calls=[
                ToolCall(id="c1", name="read_file", input='{"path": "a.py"}'),
                ToolCall(id="c2", name="bash", input="{}"),
            ],
        )
    )
    tool_round = sent(
        Message(
            role="tool",
            tool_results=[
                ToolResult(tool_call_id="c1", content="内容", is_error=False),
                ToolResult(tool_call_id="c2", content="出错了", is_error=True),
            ],
        )
    )
    plain_assistant = sent(Message(role="assistant", content="看完了"))

    assert user == [{"role": "user", "content": "看看"}]
    assert assistant_calls == [
        {
            "role": "assistant",
            "content": "我读一下",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
                },
                {
                    "id": "c2",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                },
            ],
        }
    ]
    assert tool_round == [
        {"role": "tool", "tool_call_id": "c1", "content": "内容"},
        {"role": "tool", "tool_call_id": "c2", "content": "出错了"},
    ]
    assert plain_assistant == [{"role": "assistant", "content": "看完了"}]


def test_openai_sends_null_content_for_an_empty_preamble() -> None:
    """只调工具、不说话时正文发 `null`，不是 `""`。

    协议里 `null` 才是「这条没有正文」；有的严格实现会把空串当成一轮空回复。
    """
    sent = _to_sdk_messages(
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="c", name="bash", input="")],
        )
    )

    assert sent[0]["content"] is None
    # 顺带把另一处归一钉住：空的 arguments 回灌时必须是 `"{}"`，不能是 `""`。
    assert sent[0]["tool_calls"][0]["function"]["arguments"] == "{}"
