"""协议无关的模型接入层。

上层（`conversation` / `tui`）只认这里的三个类型加一个工厂，完全不知道 HTTP 长什么样。
要接一种新协议，就是写一个适配器模块 + 在 `new_provider` 里加一行分派，上面的层零改动。
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol

from qicode.config import ProviderConfig


@dataclass
class Message:
    """一条对话消息。

    只有 user / assistant 两种角色：**system 提示词不由这里携带**，各适配器在发请求时
    自己注入（协议不同，注入的位置也不同——Anthropic 是顶层 `system` 参数，
    OpenAI 兼容侧是 messages 里的第一条）。
    """

    role: Literal["user", "assistant"]
    content: str


@dataclass
class StreamEvent:
    """流式过程中吐给上层的一个事件。

    三种互斥形态，上层按顺序判断即可：
    - `text` 非空  → 一段正文增量，追加显示
    - `done=True`  → 本轮正常结束
    - `err` 非 None → 出错，本轮到此为止

    思考增量**不走这里**：适配器识别到就直接丢弃（F5），别让它混进正文。
    """

    text: str = ""
    done: bool = False
    err: Exception | None = None


class Provider(Protocol):
    """一个后端接入点。

    刻意定义成 Protocol 而不是抽象基类：适配器不需要继承任何东西，结构对上就行，
    测试里想造个假 provider 也不用先学会继承体系。
    """

    @property
    def name(self) -> str:
        """状态栏左侧显示的名字。"""
        ...

    @property
    def model(self) -> str:
        """状态栏右侧显示的模型名。"""
        ...

    def stream(self, msgs: list[Message]) -> AsyncIterator[StreamEvent]:
        """发起一轮流式对话。

        `msgs` 是完整的对话历史（含本轮用户输入）。实现应当是 async generator：
        调用方 `cancel()` 掉跑它的 task 时，`async for` 会自然抛 `CancelledError`，
        SDK 的流由 `async with` 上下文自动清理。
        """
        ...


def new_provider(cfg: ProviderConfig) -> Provider:
    """按 `cfg.protocol` 构造对应的适配器。

    适配器用**函数内 import**，不写在文件顶部：新增协议时不必动这个文件的头部，
    也不会因为某一个适配器出问题就整个 `qicode.llm` 都 import 不了。
    """
    if cfg.protocol == "anthropic":
        from qicode.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(cfg)

    if cfg.protocol == "openai":
        from qicode.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(cfg)

    # `config` 已经挡过一道，这里是第二道：new_provider 是公开工厂，
    # 手工拼一个 ProviderConfig 也能调到这里来。
    raise ValueError(f"不认识的协议: {cfg.protocol!r}")
