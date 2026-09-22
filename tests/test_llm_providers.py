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

import pytest

from qicode.config import ProviderConfig
from qicode.llm import Message, StreamEvent
from qicode.llm.anthropic_provider import MAX_TOKENS, THINKING_PARAMS, AnthropicProvider
from qicode.llm.openai_provider import OpenAIProvider
from qicode.prompt import SYSTEM_PROMPT


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
    provider: Any, msgs: Sequence[Message] | None = None
) -> list[StreamEvent]:
    """把整条流收成一个列表。"""
    return [ev async for ev in provider.stream(list(msgs or [Message("user", "嗨")]))]


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


class FakeAnthropicStream:
    """`client.messages.stream(...)` 返回的那个上下文管理器。

    `hang=True` 时，吐完脚本里的事件就永远卡住——用来模拟「正等着下一个增量」的
    那一刻，好让取消真的落在适配器内部的 await 上。
    """

    def __init__(
        self, events: Sequence[Any], error: Exception | None = None, hang: bool = False
    ) -> None:
        self._events = events
        self._error = error
        self._hang = hang
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


class FakeAnthropicClient:
    def __init__(
        self, events: Sequence[Any], error: Exception | None = None, hang: bool = False
    ) -> None:
        self.params: dict[str, Any] | None = None
        self.stream_obj = FakeAnthropicStream(events, error, hang)
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **params: Any) -> FakeAnthropicStream:
        self.params = params
        return self.stream_obj


def anthropic_with(
    events: Sequence[Any], **cfg_over: Any
) -> tuple[AnthropicProvider, FakeAnthropicClient]:
    provider = AnthropicProvider(make_cfg(**cfg_over))
    fake = FakeAnthropicClient(events)
    inject(provider, fake)
    return provider, fake


# ────────────────────────── 假件：OpenAI 兼容 ──────────────────────────


def chunk(content: str | None, choices: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(delta=SimpleNamespace(content=content))
            for _ in range(choices)
        ]
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
    assert fake.params["system"] == SYSTEM_PROMPT
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
    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert messages[1] == {"role": "user", "content": "你好"}
    assert fake.params["stream"] is True


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
    """N7：调用方提前跳出（用户按 Esc / Ctrl+C）时，流必须被关掉。

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
        async for ev in provider.stream([Message("user", "嗨")]):
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
            async for ev in provider.stream([Message("user", "嗨")]):
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
