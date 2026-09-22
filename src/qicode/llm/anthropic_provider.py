"""Anthropic 协议适配器。

封装 `anthropic.AsyncAnthropic`，把它的事件流翻译成统一的 `StreamEvent`。
"""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from qicode.config import ProviderConfig
from qicode.llm import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    Message,
    StreamEvent,
    ToolCall,
    ToolDefinition,
    tool_input,
)
from qicode.prompt import system_prompt

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


def _to_anthropic_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    """把注册中心的工具定义转成 Anthropic 的 `tools` 参数。

    两处与 OpenAI 侧不同，都是协议规定：
    - 参数字段叫 `input_schema`，不是 `parameters`；
    - **不需要**外面再包一层 `{"type": "function", ...}`。
    """
    return [
        {
            "name": d.name,
            "description": d.description,
            "input_schema": d.input_schema,
        }
        for d in tools
    ]


def _to_anthropic_messages(msgs: list[Message]) -> list[dict[str, Any]]:
    """把统一历史转成 Anthropic 的 `messages` 参数。

    三种回合的形状各不相同，都不是随手写的：

    - **纯文本回合**：`content` 是字符串，与 v1 完全一致。
    - **assistant 调工具的回合**：`content` 必须是**数组**，正文那一段是
      `{"type": "text"}`，后面跟上若干 `{"type": "tool_use"}`。只调工具、不说话时
      正文是空串——那种情况下**不能**放一个空的 text 块，服务端会以
      `text content blocks must be non-empty` 直接 400，所以空正文就整个不放。
      正文为空时 content 数组里只剩 tool_use 块，这是合法的。
    - **工具结果回合**：Anthropic **没有** `tool` 这个角色。`tool_result` 块必须放进
      一条 **user** 消息的 content 数组里。这是协议规定，不是笔误——若照 OpenAI 的
      习惯写成 `{"role": "tool"}`，服务端不认识这个角色，请求当场失败。
    """
    out: list[dict[str, Any]] = []
    for m in msgs:
        if m.role == ROLE_TOOL:
            out.append(
                {
                    # 见上面第三条：这里必须是 user。
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": r.tool_call_id,
                            # 一个 id 一个块；多个工具的结果并列在同一个数组里
                            # （与 OpenAI 那边「一个结果一条消息」正好相反）。
                            "content": r.content,
                            "is_error": r.is_error,
                        }
                        for r in m.tool_results
                    ],
                }
            )
            continue

        if m.role == ROLE_ASSISTANT and m.tool_calls:
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            blocks += [
                {
                    "type": "tool_use",
                    "id": c.id,
                    "name": c.name,
                    # 用 `tool_input` 而不是裸 `json.loads`：这里要的是**对象**，
                    # 而模型偶尔会发出非法 JSON。裸解析一旦抛出去，崩的是整轮请求，
                    # 界面上只会剩一句看不懂的异常（见 `tool_input` 的说明）。
                    "input": tool_input(c),
                }
                for c in m.tool_calls
            ]
            out.append({"role": ROLE_ASSISTANT, "content": blocks})
            continue

        out.append({"role": m.role, "content": m.content})
    return out


def _tool_calls_of(final: Any) -> list[ToolCall]:
    """从流结束后的完整消息里取出本轮的工具调用。

    **为什么不自己拼 `input_json_delta` 的碎片**：SDK 内部的累加器已经把参数 JSON
    拼好了，`get_final_message()` 拿到的就是成品。自己再拼一遍，等于把 SDK 已经做对
    的事重做一次，还平白多出一处可能拼错的地方。

    `block.input` 是 SDK 解析好的**对象**，而 `ToolCall.input` 要的是原始字符串
    （注册中心吃字符串，工具内部本来也要自己解析一遍才能报「参数不是合法 JSON」），
    所以这里 dumps 回去——**必须带 `ensure_ascii=False`**（理由见下面那行注释）。

    `stop_reason` 是服务端给出的、最明确的「我这轮是想调工具」信号。用它做闸门，
    顺带挡掉一种更麻烦的情况：`max_tokens` 截断时也可能留下一个 input 不完整的
    tool_use 块，那种调用拿去执行只会得到一句莫名其妙的参数错误。
    """
    if final.stop_reason != "tool_use":
        return []
    return [
        # `input` 是 SDK 解析好的 dict，dumps 回字符串。
        #
        # `ensure_ascii=False` 不是可选项。它的默认值 `True` 意思是「输出只准落在
        # ASCII 里」，于是每个非 ASCII 字符都被翻成 `\uXXXX`：一条中文路径
        # `/Users/me/琪琪作业/快排.txt` 会变成 `/Users/me/琪琪作业/...`。
        # `json.loads` 解析结果一模一样（所以功能没坏），坏的是**给人看的那一行**——
        # 它会在工具行里原样上屏，而同一块的结果行回显的是真字符，两种写法打架。
        # JSON 本来就是 UTF-8 编码的，这层转义是给「只能跑 ASCII 的老通道」准备的保险，
        # 我们没有这个约束。
        ToolCall(id=b.id, name=b.name, input=json.dumps(b.input, ensure_ascii=False))
        for b in final.content
        if b.type == "tool_use"
    ]


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

    @property
    def supports_tools(self) -> bool:
        """开了 thinking 就不能带工具（理由见 `qicode.llm.Provider.supports_tools`）。"""
        return not self._thinking

    async def stream(
        self, msgs: list[Message], tools: list[ToolDefinition]
    ) -> AsyncIterator[StreamEvent]:
        params: dict[str, Any] = {
            "model": self._model,
            "max_tokens": MAX_TOKENS,
            # system 是 Anthropic 协议的顶层参数，不混在 messages 里。
            # 现拼而不是用常量：里面要带上本次的接入点和模型名，模型才知道
            # 自己是谁（理由见 `qicode.prompt.system_prompt`）。
            "system": system_prompt(self._name, self._model),
            "messages": _to_anthropic_messages(msgs),
        }
        # 空列表就不发这个参数。上层已经按 `supports_tools` 决定过传不传了，
        # 这里只做「空就不发」这一件事——部分网关对 `tools: []` 会报错。
        if tools:
            params["tools"] = _to_anthropic_tools(tools)
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
                    # input_json_delta（工具参数的碎片）也是——碎片拼装交给 SDK 的累加器，
                    # 读完了直接取成品。签名、用量、起止标记同样都不是正文。

                # 必须在 async with 里面取：`get_final_message()` 要读的就是这条流，
                # 出了上下文它已经被关掉了。
                calls = _tool_calls_of(await stream.get_final_message())
                if calls:
                    # 攒齐了**一次性**上抛，不学文本那样一片一片来：参数 JSON 被协议
                    # 切成碎片、多工具时还会按 index 交错到达，中途任何一片都组不成一个
                    # 能用的调用，上层拿到半截也无事可做。
                    yield StreamEvent(tool_calls=calls)
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
            # 注意顺序：tool_calls 在上面 yield 过之后才轮到 done。上层因此可以
            # 「见 done 就收工」，工具调用一定在它之前到齐。
            yield StreamEvent(done=True)
