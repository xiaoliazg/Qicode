"""OpenAI 兼容协议适配器。

封装 `openai.AsyncOpenAI`。凡是以 OpenAI 的 `/chat/completions` 为接口的服务都归这条
链路：OpenAI 官方、本地 Ollama、各类兼容网关。差别只在 `base_url`。
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import openai
from openai.types.chat import ChatCompletionMessageParam

from qicode.config import ProviderConfig
from qicode.llm import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    Message,
    StreamEvent,
    ToolCall,
    ToolDefinition,
)
from qicode.prompt import system_prompt


def _to_openai_tools(tools: list[ToolDefinition]) -> list[Any]:
    """把注册中心的工具定义转成 OpenAI 的 `tools` 参数。

    与 Anthropic 侧的两处差异都是协议规定：外面要包一层 `{"type": "function"}`，
    里面的参数字段叫 `parameters`（Anthropic 叫 `input_schema`）。schema 本身
    **整份**塞进去——注册中心存的就已经是完整的 JSON Schema，不用拆。
    """
    return [
        {
            "type": "function",
            "function": {
                "name": d.name,
                "description": d.description,
                "parameters": d.input_schema,
            },
        }
        for d in tools
    ]


def _to_sdk_messages(msg: Message) -> list[ChatCompletionMessageParam]:
    """把一条统一 `Message` 转成 SDK 的入参 dict——**可能不止一条**。

    这里必须按 role 分支、分次**字面量**构造，不能图省事写成
    `{"role": msg.role, "content": msg.content}`：SDK 的消息类型是若干 TypedDict 组成的
    联合，每个成员各自要求 `role` 是它自己的那个字面量常量。role 用变量填，就一个成员也
    对不上；mypy 找不到匹配的 overload，`create()` 的返回类型退回联合，连后面迭代流的
    那行也会跟着报错。分支写清楚，类型就一路推下去了。

    返回列表是因为一个回合不总对应一条消息。**工具结果回合**尤其如此：OpenAI 协议
    要求每个 `tool_call_id` 各占一条 `{"role": "tool"}`，几个工具的结果就得发几条，
    不能像 Anthropic 那样合并进一条消息的 content 数组里。
    """
    if msg.role == ROLE_TOOL:
        return [
            {
                "role": "tool",
                "tool_call_id": r.tool_call_id,
                "content": r.content,
            }
            for r in msg.tool_results
        ]

    if msg.role == ROLE_ASSISTANT and msg.tool_calls:
        return [
            {
                "role": "assistant",
                # 只调工具、不说话时正文是空串。发 `None` 而不是 `""`：协议里
                # `null` 才是「这条没有正文」，有的严格实现会把空串当成一轮空回复。
                "content": msg.content or None,
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {
                            "name": c.name,
                            # 回灌时 arguments 必须是合法 JSON 字符串。无参工具在流里
                            # 可能就是空串，而 `"arguments": ""` 会被严格端点判成
                            # 400，所以在这里归一成 `"{}"`（`docs/v2/plan.md`「空参数归一」）。
                            "arguments": c.input or "{}",
                        },
                    }
                    for c in msg.tool_calls
                ],
            }
        ]

    if msg.role == ROLE_ASSISTANT:
        return [{"role": "assistant", "content": msg.content}]
    return [{"role": "user", "content": msg.content}]


def _tool_calls_of(buf: dict[int, dict[str, str]]) -> list[ToolCall]:
    """把按 index 攒好的分片整理成 `ToolCall` 列表。

    按 index **排序**再输出，不能依赖 dict 的插入顺序：分片到达的顺序取决于服务端，
    而工具是按这个顺序执行的，返回的顺序会原样进历史、下一轮又原样发回去——顺序飘忽
    会让「模型看到的调用顺序」和「它自己发起的顺序」对不上。

    `id` / `name` 用 `.get` 取、缺了给空串，而不是直接下标：真缺了说明网关发的分片
    本身就不完整（第一片被吞了）。那种情况下丢一个 `KeyError` 出来，会被上面那个宽
    `except Exception` 接住、翻成一句和真因毫无关系的 err 事件；给空串则走正常路径——
    空 name 在注册中心那里会变成一条「未知工具」的结构化错误，模型看得懂，会话照常继续。
    """
    return [
        ToolCall(
            id=part.get("id", ""),
            name=part.get("name", ""),
            # 空 arguments 归一为 `"{}"`（同上）。
            input=part.get("args") or "{}",
        )
        for _, part in sorted(buf.items())
    ]


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

    @property
    def supports_tools(self) -> bool:
        """这条协议没有 thinking 的约束，工具一直可用。"""
        return True

    async def stream(
        self, msgs: list[Message], tools: list[ToolDefinition]
    ) -> AsyncIterator[StreamEvent]:
        # 这条协议没有顶层 system 参数，system prompt 是 messages 的第一条。
        # 现拼而不是用常量：里面要带上本次的接入点和模型名，模型才知道自己是谁
        # （理由见 `qicode.prompt.system_prompt`）。
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt(self._name, self._model)}
        ]
        for m in msgs:
            # 一条 Message 可能展开成多条（工具结果回合就是），所以是 +=。
            messages += _to_sdk_messages(m)

        # 工具调用的分片。key 是服务端给的 index，不是到达顺序——一次要调多个工具时，
        # 它们的片段会**交错**到达（t0 的片、t1 的片、t0 的片……）。按 index 归拢
        # 是唯一能拼对的办法，按到达顺序拼只会把两个工具的参数搅在一起。
        buf: dict[int, dict[str, str]] = {}

        try:
            stream = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                # 这个字面量 `True` 不只是「开流式」：SDK 的 `create` 是一串 overload，
                # 只有把 stream 写成字面量才能选中返回 `AsyncStream` 的那一个。
                # 换成变量或者 `**kwargs` 展开，mypy 会退回第一个 overload，
                # 类型变成 `ChatCompletion`，下面 `async with stream:` 当场报错。
                stream=True,
                # 没有工具时传 `omit`（SDK 的「这个参数根本不要发」哨兵），而不是空列表：
                # 部分兼容网关对 `tools: []` 直接报错。
                tools=_to_openai_tools(tools) or openai.omit,
            )
            # 这层 async with 不是装饰：AsyncStream.close() 的文档写得很清楚——
            # 「Automatically called **if the response body is read to completion**」，
            # 也就是**只有把流读完**才自动关连接。中途被取消（用户退出 App）、
            # 或中途报错跳出循环时，流没读完，就没人关它，HTTP 连接会一直挂着直到 GC。
            # async with 保证任何退出路径（正常、异常、取消）都走到 close()。
            async with stream:
                async for chunk in stream:
                    # 有些兼容实现会发 choices 为空的收尾块（只带用量统计）。
                    # 直接取 [0] 会在这些后端上抛 IndexError，白白把一轮对话判成失败。
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta.content:
                        yield StreamEvent(text=delta.content)
                    # 工具调用的片段：可能一次来好几个（`tool_calls` 是个列表）。
                    for tc in delta.tool_calls or []:
                        part = buf.setdefault(tc.index, {})
                        # id / name 只在**第一片**里出现，后面是空的；arguments 则相反，
                        # 每一片都只是一段，必须首尾相接地拼起来。所以前者「有才写」，
                        # 后者是「有就追加」——写反了都会得到一个残缺的调用。
                        if tc.id:
                            part["id"] = tc.id
                        if tc.function and tc.function.name:
                            part["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            part["args"] = part.get("args", "") + tc.function.arguments

            # 流干净地读完了。这里**不看 `finish_reason`**，而是「攒到东西就交」：
            # 各家兼容网关对 `finish_reason` 的取值并不统一（`tool_calls` / `function_call`
            # / 干脆是 `stop`），拿它当闸门的话，换个网关就可能把工具调用整个丢掉。
            # buf 里有内容才是不会骗人的事实。
            if buf:
                yield StreamEvent(tool_calls=_tool_calls_of(buf))
        except asyncio.CancelledError:
            # 同 anthropic 适配器：取消信号必须原样传出去（理由见那处的注释）。
            raise
        except Exception as exc:  # noqa: BLE001
            # 同 anthropic 适配器：这里是外部服务的边界，宽catch 是故意的——
            # 任何失败都翻译成 err 事件交给界面，会话继续（F11、AC11）。
            yield StreamEvent(err=exc)
        else:
            # 同 anthropic 适配器：done 一定排在 tool_calls 之后。
            yield StreamEvent(done=True)
