"""Anthropic 协议适配器。

封装 `anthropic.AsyncAnthropic`，把它的事件流翻译成统一的 `StreamEvent`。
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from qicode.config import ProviderConfig
from qicode.llm import Message, StreamEvent
from qicode.prompt import SYSTEM_PROMPT

# 请求开启思考时发出去的参数。
#
# `adaptive` 而不是固定预算：`{"type": "enabled", "budget_tokens": N}` 那种写法在当前
# Claude 模型（Fable 5/5.1、Opus 5/4.8/4.7、Sonnet 5）上已被移除，传了直接返回 400。
# `adaptive` 交给模型按问题难度自己决定想多久。
#
# `display` 必须显式写：它缺省是 `omitted`，那样服务端根本不发思考内容，我们也就
# 没有增量可「识别但不渲染」（F5）。写 `summarized` 才会真的收到思考增量。
THINKING_PARAMS: dict[str, str] = {"type": "adaptive", "display": "summarized"}

# 单次回复的 token 上限。够长，不至于把正常回答截断。
MAX_TOKENS = 4096


class AnthropicProvider:
    """一个 Anthropic 接入点。

    只管三件事：把统一的 `Message` 转成 SDK 入参、注入 system prompt、
    把 SDK 的事件流翻成 `StreamEvent`。界面和状态一概不管。
    """

    def __init__(self, cfg: ProviderConfig) -> None:
        self._name = cfg.name
        self._model = cfg.model
        self._thinking = cfg.thinking
        self._client = anthropic.AsyncAnthropic(
            api_key=cfg.api_key,
            # None 表示不覆盖，用 SDK 内置的官方端点。
            base_url=cfg.base_url or None,
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    async def stream(self, msgs: list[Message]) -> AsyncIterator[StreamEvent]:
        params: dict[str, Any] = {
            "model": self._model,
            "max_tokens": MAX_TOKENS,
            # system 是 Anthropic 协议的顶层参数，不混在 messages 里。
            "system": SYSTEM_PROMPT,
            "messages": [{"role": m.role, "content": m.content} for m in msgs],
        }
        if self._thinking:
            params["thinking"] = THINKING_PARAMS

        try:
            async with self._client.messages.stream(**params) as stream:
                async for event in stream:
                    # 一次流里混杂着多种事件（消息开始、内容块起止、用量……），
                    # 只有 content_block_delta 里才有正文增量。
                    if (
                        event.type == "content_block_delta"
                        and event.delta.type == "text_delta"
                    ):
                        yield StreamEvent(text=event.delta.text)
                    # 其余一律丢弃：thinking_delta 属于这一类（F5 要求识别到但不渲染），
                    # 签名、用量、起止标记也都不是正文。
        except asyncio.CancelledError:
            # 用户按 Esc 打断时走这里。必须原样抛出，**不能**吞——
            # 吞掉的话 asyncio 会认为任务正常跑完了，取消语义就断了。
            raise
        except Exception as exc:  # noqa: BLE001
            # 这一层是**外部服务的边界**：网络断了、密钥不对、被限流、响应格式变了……
            # 什么都可能来。宽catch 是故意的——一律翻成 err 事件交给界面显示，
            # 让会话能继续下去（F11、AC11），而不是让异常冒出去把整个 App 打崩。
            yield StreamEvent(err=exc)
        else:
            yield StreamEvent(done=True)
