"""OpenAI 兼容协议适配器。

封装 `openai.AsyncOpenAI`。凡是以 OpenAI 的 `/chat/completions` 为接口的服务都归这条
链路：OpenAI 官方、本地 Ollama、各类兼容网关。差别只在 `base_url`。
"""

import asyncio
import re
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

#: 各家网关说「我不支持工具」的措辞。
#:
#: **为什么要用正则，而不是三个词各 `in` 一遍。** 要认的形状是「否定词 + support +
#: tool 出现在**同一句话**里」，不是这三个词在整段报文中各出现一次。全文匹配会把
#: 「碰巧都提到过」的报文也算进来：`max_tokens is not supported; invalid tool arguments`
#: 三个词一个不缺，可它跟「不支持工具」毫无关系。
#:
#: 两个模式是**语序相反**的两句话，两种写法都实测撞过：
#:
#: - `qwen2.5vl:7b does not support tools`（ollama 的原话）—— 否定词在前，模式 1
#: - `tool calling is not supported by this model` —— 工具在前，模式 2
#:
#: 两句之间那个 `[^.]{0,30}?` 是「同一句、且挨得够近」：`.` 是句号，跨句就不算；
#: 30 个字符足够装下 `does not support tool_choice and tools` 这类夹带，又不至于
#: 让两个不相干的从句碰在一起。紧挨着的 `\s+` 也帮了忙——上面那个 `max_tokens`
#: 的例子正是被它挡下的（`supported;` 后面是分号，不是空白，模式当场断掉）。
#:
#: 两点都要说清楚，免得读者以为它比实际更准：
#:
#: - 它认的是**形状**，不是意思。`the parameter is not supported and the tool argument
#:   is wrong` 会被误判成 True（`tests/test_llm_providers.py` 里有用例钉着）。
#: - 但误判的代价**有上限**：这条规则只决定发不发第二次请求，不决定要不要把工具
#:   永久判死——那个门槛在「重试成功」（见 `stream`）。所以最坏情况是多花一次往返，
#:   接着用户看到的是重试那次的真实报错。
_NO_TOOLS_PATTERNS = (
    r"(?:not|n't|un|no)\s*support\w*\s+[^.]{0,30}?\btools?\b",
    r"\btools?\b[^.]{0,30}?(?:not|n't|un)\s*support",
)


def _rejects_tools(exc: Exception) -> bool:
    """这条 400 是不是在说「这个模型不支持工具」。

    限定在 `BadRequestError`（HTTP 400）而不是纯按文本匹配：「不支持工具」是**请求
    格式**问题，协议上就该是 400。别的状态码夹着这句话（比如网关 500 里带一句），
    那是网关自己有毛病——那时候把工具摘掉等于把毛病盖住，宁可让用户看见那个 500。

    这是个**判断形状**的启发式，不是解析，召回和精度都不完美（取舍见
    `_NO_TOOLS_PATTERNS`）：认不出的写法会退化成「不重试」，也就是改之前的老样子，
    用户至少还能看见原始报错。
    """
    if not isinstance(exc, openai.BadRequestError):
        return False
    text = str(exc).lower()
    return any(re.search(pattern, text) for pattern in _NO_TOOLS_PATTERNS)


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


def _system_message(
    name: str, model: str, *, tools: bool
) -> ChatCompletionMessageParam:
    """拼 system prompt 那一格。

    **返回类型标注是必须的，不是装饰。** 不写的话 mypy 会把这个 dict 字面量推成
    `dict[str, str]`，而 `messages` 的类型是 `list[ChatCompletionMessageParam]`——
    元素是 TypedDict 的**联合**，`list` 对它是不变的（invariant），
    `dict[str, str]` 一个成员也对不上，`messages[0] = ...` 当场报 call-overload。
    写上返回类型，字面量就直接拿去和联合里的成员逐个比，一路通过。

    （这跟 `_to_sdk_messages` 里那段注释是同一个坑的两个面：**role 必须是字面量**，
    否则 TypedDict 联合认不出来。）
    """
    return {"role": "system", "content": system_prompt(name, model, tools=tools)}


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
        #: 这个接入点有没有**用行动**证明过「不接受工具定义」。
        #:
        #: 记在 provider 实例上，因为它天然是**接入点的属性**：「qwen2.5vl:7b 这个模型
        #: 不支持工具」跟界面无关、跟某一轮对话也无关，换个模型就未必成立，但同一场
        #: 会话里不会变。放这儿还有一处白捡的好处——`qicode.agent.Agent.run` 每轮开头
        #: 就是按 `self._provider.supports_tools` 决定发不发工具的，所以撞过一次之后，
        #: **下一轮自动就不发了**，agent 一行都不用改。
        self._tools_rejected = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    @property
    def supports_tools(self) -> bool:
        """这个接入点此刻能不能接受工具定义。

        这条协议的答案是**撞出来的**，不是提前知道的。配置里没有任何字段说「这个模型
        支不支持 tools」，OpenAI 也没有查询接口，唯一的办法是带着 `tools` 发一次、
        看对方怎么答；`_rejects_tools` 认出的那个 400 就是「不支持」，处理在 `stream`。

        这跟 Anthropic 那条链路**信息来源不同**：那边一旦配了 `thinking: true`，
        发请求**之前**就知道工具用不了，所以能提前声明。这里只能撞了才知道。但对上层
        而言两者是一回事——照旧只看这个属性就行，**不需要知道它是提前说好的还是撞出来的**。
        `Provider` 协议那句「由上层据此决定传不传 `tools`」在两条链路上都还成立，
        变的只是这份声明什么时候拿得到。

        撞到之后**一直是 False**，不会再翻回 True：模型能力不会在一场会话中间变。
        """
        return not self._tools_rejected

    async def stream(
        self, msgs: list[Message], tools: list[ToolDefinition]
    ) -> AsyncIterator[StreamEvent]:
        # 这次请求到底带不带 `tools`。**必须在拼 messages 之前算出来**，因为下面那句
        # system prompt 得如实描述这次到底有没有工具（见 `qicode.prompt.system_prompt`
        # 的 `tools` 参数）——说一套做一套时，模型对不上的方式是**编**。
        #
        # - 已经撞过「不支持」的接入点直接不带（`_tools_rejected`，见 `supports_tools`）。
        #   agent 每轮开头也判了同一个属性，但**同一轮里**它是在上面那个 400 之前判的，
        #   所以这里必须再判一次，否则降级那一轮之后还会白撞一次。
        # - 没有工具时传 `omit`（SDK 的「这个参数根本不要发」哨兵），而不是空列表：
        #   部分兼容网关对 `tools: []` 直接报错。
        dropped_tools = self._tools_rejected
        tools_param = (
            openai.omit if dropped_tools else (_to_openai_tools(tools) or openai.omit)
        )

        # 这条协议没有顶层 system 参数，system prompt 是 messages 的第一条。
        # 现拼而不是用常量：里面要带上本次的接入点和模型名，模型才知道自己是谁
        # （理由见 `qicode.prompt.system_prompt`）。
        messages: list[ChatCompletionMessageParam] = [
            _system_message(self._name, self._model, tools=not dropped_tools)
        ]
        for m in msgs:
            # 一条 Message 可能展开成多条（工具结果回合就是），所以是 +=。
            messages += _to_sdk_messages(m)

        # 工具调用的分片。key 是服务端给的 index，不是到达顺序——一次要调多个工具时，
        # 它们的片段会**交错**到达（t0 的片、t1 的片、t0 的片……）。按 index 归拢
        # 是唯一能拼对的办法，按到达顺序拼只会把两个工具的参数搅在一起。
        buf: dict[int, dict[str, str]] = {}

        try:
            # 最多试两次：第一次带工具，撞上「不支持」就摘掉工具再来一次。
            #
            # 之所以敢重发，是因为这个 400 在 `create()` **当场**就抛出来了，一个字的
            # 正文都还没 yield 出去——重发不会出现「同一段话说了两遍」。要是哪天有网关
            # 先吐一段正文再报这个 400，那这个重试就不再安全，届时要加判断。
            for attempt in (1, 2):
                try:
                    stream = await self._client.chat.completions.create(
                        model=self._model,
                        messages=messages,
                        # 这个字面量 `True` 不只是「开流式」：SDK 的 `create` 是一串 overload，
                        # 只有把 stream 写成字面量才能选中返回 `AsyncStream` 的那一个。
                        # 换成变量或者 `**kwargs` 展开，mypy 会退回第一个 overload，
                        # 类型变成 `ChatCompletion`，下面 `async with stream:` 当场报错。
                        stream=True,
                        tools=tools_param,
                    )
                except openai.BadRequestError as exc:
                    # 唯一会重试的情形：**第一次** + 带着工具 + 对方明说「不支持工具」。
                    # 三个条件缺一不可——第二次还失败（或者本来就是别的 400），说明问题
                    # 不在工具上，那时候再摘工具只是把真正的毛病藏起来。
                    if attempt == 1 and not dropped_tools and _rejects_tools(exc):
                        dropped_tools = True
                        tools_param = openai.omit
                        # system prompt 是函数开头拼好的，那时还不知道会降级，**必须换掉**：
                        # 不换的话第二次请求就成了「说明里写着可以用工具、参数里却没有工具」，
                        # 模型只会在正文里用自然语言假装调工具——那正是这次要修的毛病。
                        # 这里重建而不是改一改原文：措辞的差异归 `qicode.prompt` 管，
                        # 两边各写一份迟早会不一样。
                        messages[0] = _system_message(
                            self._name, self._model, tools=False
                        )
                        continue
                    # 不是那种 400：原样抛给外层，由它翻成 err 事件。
                    raise
                # 拿到流了，跳出重试。写成 `break` 而不是 `else: break`——`continue`
                # 和 `raise` 两条路都已经离开了这一轮，能走到这儿就是成功。
                #
                # 降级这件事**到这里才记下来**（`_tools_rejected`），不是一撞上 400 就记。
                # 因为「对方说工具不支持」和「毛病真的出在工具上」是两回事：重试**失败**
                # 的时候我们并没有拿到证据（那次失败可能是密钥不对、模型名写错、网络抖了
                # 一下），这时候把工具永久判死，会让一个只是碰巧撞上一次别的问题的接入点
                # 从此再也用不上工具。重试**成功**才是铁证——同一条请求、只少了 `tools`，
                # 它通了。
                if dropped_tools:
                    self._tools_rejected = True
                break

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
            #
            # 降级重试**也失败**时，接到的是**第二次**那个异常，这正是想要的：摘掉工具
            # 之后仍然失败，说明毛病跟工具有关无关（key 不对、模型名写错、网络不通……），
            # 那条错误才是用户此刻真要去解决的。第一次那句「不支持工具」我们已经照办了，
            # 再报它只会把人引到一个改不出结果的方向上。
            yield StreamEvent(err=exc)
        else:
            # 同 anthropic 适配器：done 一定排在 tool_calls 之后。
            yield StreamEvent(done=True)
