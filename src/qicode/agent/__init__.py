"""单轮闭环编排（F5、F6、AC8、AC9）。

**「一轮」是什么**：用户按一次 Enter，到界面重新能接受输入之间的整段过程。它可能
包含**两次**请求——第一次让模型决定要不要调工具，若调了就执行、把结果回灌，再发
第二次拿最终答复。第二次之后**不再继续**，哪怕模型又要求调工具：那是 AC9 的单轮
上限，也是「一次输入 = 一次工具批量」这条可预期性的来源。

**这一层同时是 `Conversation` 的唯一写者**（`docs/v2/plan.md` 技术决策「历史由谁写」）。
界面层因此从 v1 的「自己 `add_assistant`」退化成纯渲染器。理由是一轮里有 preamble /
工具结果 / 最终答复三条要按**各自的协议格式**入历史，散在渲染层必然写乱；一处写、
一处读，边界才干净。
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum

from qicode.conversation import Conversation
from qicode.llm import Provider, ToolCall, ToolDefinition, ToolResult
from qicode.tool import DEFAULT_TIMEOUT, Registry

#: `● name(args)` 那一行里最多留多少字符的参数预览。
#:
#: 只影响界面：真正执行时传给工具的是**完整**的 `call.input`。预览太长会把工具行
#: 撑成好几行，而那一行要传达的信息其实只有一个「模型在调什么」。
MAX_ARGS_PREVIEW = 80

#: 模型一个字都没说出来时给用户的提示。
#:
#: 这种情况**不进历史**。v1 踩过：Anthropic 对 content 为空的消息直接返回 400，
#: 存进去会让**下一轮**莫名其妙地失败，而用户完全看不出这跟上一轮有关。
#: （v1 的原始注释在 `docs/v1/` 的界面部分，规则本身没变，只是执行的人换成了这一层。）
EMPTY_REPLY = "模型返回了空回复"

#: 请求#2 **又在要工具**、自己一个字没说时给的提示（AC9 原话：「或单轮上限提示」）。
#:
#: 和 `EMPTY_REPLY` 必须分开，不能图省事共用一句。那句说的是「模型什么都没说」，
#: 而这里它明明说了——它想接着调工具，只是本阶段一轮只给一次。用错的那句会让用户
#: 以为模型抽风了，实际上是我们主动停的手，得让他知道「再发一条就能继续」。
TOOL_LIMIT = "模型还想继续调用工具，但一轮只执行一次。请再发一条消息让它接着做。"


class Phase(Enum):
    """一次工具调用的两个时点。"""

    START = "start"
    END = "end"


@dataclass
class ToolEvent:
    """一次工具调用的开始或结束（`docs/v2/plan.md`）。

    START 和 END 用**同一个**类型，靠 `phase` 区分：界面据此决定是「先把这一行画出来」
    还是「把结果补上去」。分成两种类型也可以，但那样界面要维护两套分支，而这两件事
    本来就是同一个东西的前后两帧。
    """

    name: str
    args: str = ""
    """参数预览（START 和 END 都带着，END 时界面要用它重画整行）。"""

    phase: Phase = Phase.START
    result: str = ""
    """`phase=END`：工具返回的完整内容。界面自己决定截几行显示。"""

    is_error: bool = False
    """`phase=END`：这次工具执行是不是失败了（`docs/v2/spec.md` F9）。"""


@dataclass
class Event:
    """单轮闭环对外事件流的一个元素。

    界面按**非 None 的字段**分派，四种形态互斥：
    `text` 非空 → 正文增量；`tool` 非 None → 工具时点；`done` → 本轮结束；
    `err` 非 None → 本轮失败（会话不中断）。
    """

    text: str = ""
    tool: ToolEvent | None = None
    done: bool = False
    err: Exception | None = None


@dataclass
class _Turn:
    """一次请求收完之后，我们关心的那点东西。

    用它而不是让 `_request` 返回一个元组：`_request` 是 async generator，
    它得一边 `yield` 事件一边攒东西，而 generator **没法又 yield 又 return 结果**。
    于是改成「传一个容器进来填」。
    """

    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    err: Exception | None = None


def _readable(raw: str) -> str:
    """把参数里 `\\uXXXX` 形式的转义还原成真字符，**纯为了好看**。

    **这一层是防御性的，我们自己那两个适配器现在都不会喂进转义。** 实测过：Anthropic
    那条路原来会（`json.dumps` 漏了 `ensure_ascii=False`），源头已在
    `anthropic_provider._tool_calls_of` 修掉；DeepSeek 的 OpenAI 兼容端点直接发 UTF-8，
    不转义。留在这里是因为 `base_url` 是**用户填的**——各种第三方网关（Go/Java 写的
    代理、老的 OpenAI 兼容实现）出于「输出只准落在 ASCII 里」的习惯确实会转义，
    撞上了就跟这次一样：功能没坏，屏幕上是一条读不出来的工具行。

    成本很低：只有真的出现 `\\u` 才去解析，绝大多数调用是原样返回——既不篡改模型
    原本的排版（有的发 `{"a":1}`、有的发 `{"a": 1}`，那是它的自由），也免得一次几 MB
    的 `write_file` 为了显示 80 个字符把整段 JSON 解析一遍。

    解析失败（模型偶尔真的会发非法 JSON）或结果不是对象时同样原样返回。这一层是给人
    看的，不该在画之前先把自己搞崩——「参数不是合法 JSON」那条结构化错误由工具侧报，
    不归这里管。
    """
    if "\\u" not in raw:
        return raw
    try:
        parsed = json.loads(raw)
    except ValueError:
        # JSONDecodeError 是 ValueError 的子类，这一句就够。
        return raw
    if not isinstance(parsed, dict):
        return raw
    # `ensure_ascii=False` 只让非 ASCII 出字。换行仍是 `\n`、引号仍是 `\"`，
    # 所以结果**仍然是一行**——这个函数的前提就是「一行」。
    return json.dumps(parsed, ensure_ascii=False)


def preview_args(raw: str) -> str:
    """把工具参数压成一行能看的预览。

    **公开**（不是 `_preview`）是因为它有两个调用方：这里的 `run`，以及退出回放
    （`qicode.tui.view.transcript`）。回放拿到的 `ToolCall.input` 是**完整**的原始
    参数（历史里存的就是全文，截断只发生在界面上），不共用这一份规则的话，
    回放时一次 `write_file` 会把整个文件内容印到终端上，跟对话区里看到的完全两样。

    先还原转义再截断（`_readable`）：截断是**按显示出来的字符**算的，反过来的话一条
    中文路径会被算成六倍长度，还没上屏就被砍掉了。
    """
    text = _readable(raw)
    if len(text) <= MAX_ARGS_PREVIEW:
        return text
    # 截断要**说出来**。不标省略号的话，界面上的 `{"path": "src/qic` 看着就像一个
    # 完整的（而且写错了的）参数，反而让人以为模型发的东西不对。
    return text[:MAX_ARGS_PREVIEW] + "…"


class Agent:
    """持有 provider 与注册中心，执行单轮闭环。"""

    def __init__(self, provider: Provider, registry: Registry) -> None:
        self._provider = provider
        self._registry = registry

    async def run(self, conv: Conversation) -> AsyncIterator[Event]:
        """跑完一轮，把经过翻译成事件流。

        事件的到达顺序**就是**渲染顺序：正文增量 → （工具 START → 工具 END）× N →
        最终正文增量 → done。界面因此不用自己判断「现在该画哪一块」。

        调用方 `cancel()` 掉跑这个生成器的 task 即可打断（用户按 Esc / Ctrl+C）：
        `async for` 会把 `CancelledError` 原样抛上来，适配器那边的 `async with`
        顺势关掉 HTTP 流。工具执行本身受 `DEFAULT_TIMEOUT` 约束（N1）。
        """
        # 工具定义只在这里取一次。`supports_tools` 为假（Anthropic 开了 thinking）
        # 时就给空列表——界面那边看的是**同一个**属性，所以「工具暂不可用」的提示
        # 不需要这里再传一个标志过去。
        defs: list[ToolDefinition] = (
            self._registry.definitions() if self._provider.supports_tools else []
        )

        # ── 请求#1：这一轮要不要先调工具 ──
        first = _Turn()
        async for event in self._request(conv, defs, first):
            yield event
        if first.err is not None:
            yield Event(err=first.err)
            return

        if not first.calls:
            # 纯文本回合，与 v1 行为等价：答复直接入历史，这一轮就完了。
            if not first.text.strip():
                yield Event(err=RuntimeError(EMPTY_REPLY))
                return
            conv.add_assistant(first.text)
            yield Event(done=True)
            return

        # ── 有工具调用 ──
        # 先把「模型说了什么 + 它要调什么」整体作为一条 assistant 回合记下来。
        # 顺序很要紧：这条必须排在工具结果**之前**，协议靠 id 配对，反过来就配不上了。
        conv.add_assistant_with_tool_calls(first.text, first.calls)

        results: list[ToolResult] = []
        for call in first.calls:
            preview = preview_args(call.input)
            yield Event(tool=ToolEvent(name=call.name, args=preview, phase=Phase.START))

            # 传 `call.input` 原文而不是 preview：预览是给眼睛看的，执行要完整的。
            # `Registry.execute` 把所有失败（未知工具 / 超时 / 工具自己抛异常）
            # 都收成结构化 `Result`，所以这里不会抛——这正是 N4 要的形状。
            result = await self._registry.execute(
                call.name, call.input, DEFAULT_TIMEOUT
            )

            yield Event(
                tool=ToolEvent(
                    name=call.name,
                    args=preview,
                    phase=Phase.END,
                    result=result.content,
                    is_error=result.is_error,
                )
            )
            results.append(
                ToolResult(
                    tool_call_id=call.id,
                    content=result.content,
                    is_error=result.is_error,
                )
            )

        # 一批结果作为**一条** tool 回合入历史（与 assistant 那条的 tool_calls 一一对应）。
        conv.add_tool_results(results)

        # ── 请求#2：拿着结果要最终答复 ──
        final = _Turn()
        async for event in self._request(conv, defs, final):
            yield event
        if final.err is not None:
            yield Event(err=final.err)
            return

        if not final.text.strip():
            # 一个字都没说出来。空回复不入历史（Anthropic 对空 content 直接 400）。
            #
            # 两种成因要分开报：它可能是**又想要工具**——那我们停手的原因就说得明白，
            # 用户再发一条即可（AC9 的「单轮上限提示」）；也可能真的什么都没给。
            # 共用一句会把前一种说成「模型抽风了」，而实际上是我们主动停的。
            limit = bool(final.calls)
            yield Event(err=RuntimeError(TOOL_LIMIT if limit else EMPTY_REPLY))
            return

        conv.add_assistant(final.text)
        yield Event(done=True)

    async def _request(
        self, conv: Conversation, defs: list[ToolDefinition], turn: _Turn
    ) -> AsyncIterator[Event]:
        """发一次请求：正文增量实时转成事件吐出去，收到的东西填进 `turn`。

        两次请求的收流逻辑**完全一样**——差别只在调用方怎么处理 `turn.calls`：
        请求#1 拿去执行，请求#2 只看一眼、不执行（AC9）。所以这里不设开关，
        该不该执行是 `run` 的决定。

        `conv.messages()` 每次都是**新的一份副本**（见 `Conversation.messages`），
        所以「取历史 → 发请求 → 往历史里追加」这个来回不会撞上「边迭代边修改」。
        这一点在 v2 比 v1 更要紧：一轮里要取两次。
        """
        async for event in self._provider.stream(conv.messages(), defs):
            if event.err is not None:
                turn.err = event.err
                return
            if event.text:
                turn.text += event.text
                # **立刻**转出去，不等整条流收完：界面的「逐字蹦出来」全靠这一下。
                yield Event(text=event.text)
            turn.calls += event.tool_calls
        # 到这里流正常结束了。适配器保证正常结束必给 done，这里不再另判——
        # 真没给的话，`turn.text` 是多少就是多少，界面照样能收尾。
