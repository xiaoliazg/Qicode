"""测试共用的假件与夹具。

这里的假 provider 实现 `Provider` Protocol 的**全部形状**（name / model / stream），
但一个字节的网络都不碰。界面的测试因此是完全确定的：什么时候吐什么，全由用例说了算。
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence

import pytest

from qicode.config import ProtocolName, ProviderConfig
from qicode.llm import Message, StreamEvent


class ScriptedProvider:
    """按预设脚本吐事件的假 provider。"""

    def __init__(
        self,
        events: Sequence[StreamEvent],
        *,
        name: str = "fake",
        model: str = "fake-1",
        delay: float = 0.0,
    ) -> None:
        self._events = list(events)
        self._name = name
        self._model = model
        self._delay = delay
        # 记下**每一次**调用收到的历史（F6）。
        # 只留最后一次是不够的：多轮用例要验的是「第二轮把第一轮的上下文带上了」，
        # 那必须能同时看到两次请求各自发了什么。
        self.calls: list[list[Message]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    @property
    def received(self) -> list[Message]:
        """最近一次调用收到的历史。"""
        return self.calls[-1] if self.calls else []

    async def stream(self, msgs: list[Message]) -> AsyncIterator[StreamEvent]:
        self.calls.append(list(msgs))
        for event in self._events:
            if self._delay:
                await asyncio.sleep(self._delay)
            yield event


def build_provider(
    events: Sequence[StreamEvent],
    *,
    name: str = "fake",
    model: str = "fake-1",
    delay: float = 0.0,
) -> ScriptedProvider:
    return ScriptedProvider(events, name=name, model=model, delay=delay)


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
