"""OpenAI 兼容协议适配器。

封装 `openai.AsyncOpenAI`。凡是以 OpenAI 的 `/chat/completions` 为接口的服务都归这条
链路：OpenAI 官方、本地 Ollama、各类兼容网关。差别只在 `base_url`。
"""

import asyncio
from collections.abc import AsyncIterator

import openai
from openai.types.chat import ChatCompletionMessageParam

from qicode.config import ProviderConfig
from qicode.llm import Message, StreamEvent
from qicode.prompt import system_prompt


def _to_sdk_message(msg: Message) -> ChatCompletionMessageParam:
    """把统一的 `Message` 转成 SDK 的入参 dict。

    这里必须按 role 分支、分两次**字面量**构造，不能图省事写成
    `{"role": msg.role, "content": msg.content}`：SDK 的消息类型是若干 TypedDict 组成的
    联合，每个成员各自要求 `role` 是它自己的那个字面量常量。role 用变量填，就一个成员也
    对不上；mypy 找不到匹配的 overload，`create()` 的返回类型退回联合，连后面迭代流的
    那行也会跟着报错。分支写清楚，类型就一路推下去了。
    """
    if msg.role == "assistant":
        return {"role": "assistant", "content": msg.content}
    return {"role": "user", "content": msg.content}


class OpenAIProvider:
    """一个 OpenAI 兼容接入点。"""

    def __init__(self, cfg: ProviderConfig) -> None:
        self._name = cfg.name
        self._model = cfg.model
        # cfg.thinking 在这条链路上是**忽略**的：OpenAI 协议本身没有 thinking 字段，
        # 各家兼容网关自行发挥（有的用 chat_template_kwargs，有的用 reasoning_effort），
        # 形状不统一。v1 不猜，需要时再为一个具体后端加适配（见 docs/v1/plan.md）。
        self._client = openai.AsyncOpenAI(
            api_key=cfg.api_key,
            base_url=cfg.base_url or None,
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    async def stream(self, msgs: list[Message]) -> AsyncIterator[StreamEvent]:
        # 这条协议没有顶层 system 参数，system prompt 是 messages 的第一条。
        # 现拼而不是用常量：里面要带上本次的接入点和模型名，模型才知道自己是谁
        # （理由见 `qicode.prompt.system_prompt`）。
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt(self._name, self._model)}
        ]
        messages += [_to_sdk_message(m) for m in msgs]

        try:
            stream = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                stream=True,
            )
            # 这层 async with 不是装饰：AsyncStream.close() 的文档写得很清楚——
            # 「Automatically called **if the response body is read to completion**」，
            # 也就是**只有把流读完**才自动关连接。用户按 Esc 取消、或中途报错跳出循环时，
            # 流没读完，就没人关它，HTTP 连接会一直挂着直到 GC。
            # async with 保证任何退出路径（正常、异常、取消）都走到 close()。
            async with stream:
                async for chunk in stream:
                    # 有些兼容实现会发 choices 为空的收尾块（只带用量统计）。
                    # 直接取 [0] 会在这些后端上抛 IndexError，白白把一轮对话判成失败。
                    if not chunk.choices:
                        continue
                    text = chunk.choices[0].delta.content
                    if text:
                        yield StreamEvent(text=text)
        except asyncio.CancelledError:
            # 同 anthropic 适配器：打断的取消信号必须原样传出去。
            raise
        except Exception as exc:  # noqa: BLE001
            # 同 anthropic 适配器：这里是外部服务的边界，宽catch 是故意的——
            # 任何失败都翻译成 err 事件交给界面，会话继续（F11、AC11）。
            yield StreamEvent(err=exc)
        else:
            yield StreamEvent(done=True)
