"""测试共用的假件与夹具。

这里的假 provider 实现 `Provider` Protocol 的**全部形状**
（name / model / supports_tools / stream），但一个字节的网络都不碰。
界面的测试因此是完全确定的：什么时候吐什么，全由用例说了算。
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence

import pytest

from qicode.config import ProtocolName, ProviderConfig
from qicode.llm import Message, StreamEvent, ToolDefinition


class ScriptedProvider:
    """按预设脚本吐事件的假 provider。

    `script` 支持**多轮**：每调用一次 `stream` 就取走下一组事件，用完了就重复最后一组
    （那时通常说明用例少写了一段脚本，但重复比抛异常好——界面测试里「一直这么答」
    是常见的收尾写法）。
    """

    def __init__(
        self,
        events: Sequence[StreamEvent] | Sequence[Sequence[StreamEvent]],
        *,
        name: str = "fake",
        model: str = "fake-1",
        delay: float = 0.0,
        supports_tools: bool = True,
    ) -> None:
        # 传进来的是「一组事件」还是「多组事件」：看第一个元素是不是列表。
        # 这样老用例（传一组）原样还能用，新用例想按轮次写脚本也一眼就懂。
        if events and isinstance(events[0], (list, tuple)):
            self._script = [list(group) for group in events]  # type: ignore[arg-type]
        else:
            self._script = [list(events)]  # type: ignore[arg-type]
        self._name = name
        self._model = model
        self._delay = delay
        self._supports_tools = supports_tools
        # 记下**每一次**调用收到的历史（F6）。
        # 只留最后一次是不够的：多轮用例要验的是「第二轮把第一轮的上下文带上了」，
        # 那必须能同时看到两次请求各自发了什么。
        self.calls: list[list[Message]] = []
        # 同理记下每次调用收到的工具定义：AC7 要验「传不传工具」，
        # 那看的就是这里，而不是 provider 内部怎么处理它们。
        self.tools: list[list[ToolDefinition]] = []
        #: 被 `cancel()` 打断了几次。取消语义的用例靠它分辨「真的被取消了」
        #: 还是「飞快跑完了，只是碰巧没看到流式态」。
        self.cancelled = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    @property
    def supports_tools(self) -> bool:
        return self._supports_tools

    @property
    def received(self) -> list[Message]:
        """最近一次调用收到的历史。"""
        return self.calls[-1] if self.calls else []

    @property
    def received_tools(self) -> list[ToolDefinition]:
        """最近一次调用收到的工具定义。"""
        return self.tools[-1] if self.tools else []

    async def stream(
        self, msgs: list[Message], tools: list[ToolDefinition]
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append(list(msgs))
        self.tools.append(list(tools))
        group = self._script[min(len(self.calls) - 1, len(self._script) - 1)]
        try:
            for event in group:
                if self._delay:
                    await asyncio.sleep(self._delay)
                yield event
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


def build_provider(
    events: Sequence[StreamEvent] | Sequence[Sequence[StreamEvent]],
    *,
    name: str = "fake",
    model: str = "fake-1",
    delay: float = 0.0,
    supports_tools: bool = True,
) -> ScriptedProvider:
    return ScriptedProvider(
        events, name=name, model=model, delay=delay, supports_tools=supports_tools
    )


def build_config(
    *,
    name: str = "test-provider",
    protocol: ProtocolName = "anthropic",
    model: str = "test-model",
    api_key: str = "sk-test-key",
    base_url: str | None = None,
    thinking: bool = False,
) -> ProviderConfig:
    """造一份 ProviderConfig。

    `api_key` 的默认值是个一眼能认出来的假串——`tests/test_tui_app.py` 里有一条
    用例专门断言它不会出现在界面上（N5）。
    """
    return ProviderConfig(
        name=name,
        protocol=protocol,
        api_key=api_key,
        model=model,
        base_url=base_url,
        thinking=thinking,
    )


#: 造 provider 的工厂形状。
ProviderFactory = Callable[..., ScriptedProvider]
#: 造配置的工厂形状。
ConfigFactory = Callable[..., ProviderConfig]


@pytest.fixture
def make_provider() -> ProviderFactory:
    """给用例一个「造假 provider」的工厂。

    给工厂而不是现成实例：一个用例常常要造好几个（比如先失败一次、再成功一次）。
    """
    return build_provider


@pytest.fixture
def make_config() -> ConfigFactory:
    """给用例一个「造配置」的工厂。"""
    return build_config
