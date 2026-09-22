"""协议无关的模型接入层。

上层（`conversation` / `agent` / `tui`）只认这里的几个类型加一个工厂，完全不知道 HTTP
长什么样。要接一种新协议，就是写一个适配器模块 + 在 `new_provider` 里加一行分派，
上面的层零改动。
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from qicode.config import ProviderConfig

# 消息角色。提成常量而不是散着写字面量：v1 只有两个角色时怎么写都行，v2 加到三个之后，
# 「某个分支漏了 tool」就成了一个会**静默**出错的问题（见 `docs/v2/plan.md` 里
# openai 适配器那个兜底分支的教训）。常量至少能被 grep 到。
#
# 常量本身标成 `Literal["..."]` 而不是让它退化成 `str`：`Message.role` 要的就是字面量
# 类型，标了这里，`Message(role=ROLE_USER)` 才不用额外加注解或 cast。
Role = Literal["user", "assistant", "tool"]
ROLE_USER: Role = "user"
ROLE_ASSISTANT: Role = "assistant"
ROLE_TOOL: Role = "tool"


@dataclass
class ToolCall:
    """协议无关地承载模型发起的一次工具调用。

    只在这一轮的工具调用**参数全部拼接完成**之后才有值——流式过程中那些 JSON 碎片由
    各适配器自己攒，攒齐了才构造这个对象交给上层（`docs/v2/spec.md` F4）。
    """

    id: str
    """provider 侧的调用 id；回灌执行结果时靠它配对。"""

    name: str
    """工具名，注册中心按名查找。"""

    input: str
    """JSON 参数字符串，**保持原始文本，不在这里解析**。

    不用 dict 是因为两头要的都是字符串：注册中心的 `execute(name, args: str)` 吃字符串，
    工具内部本来也要自己 `json.loads` 一遍才能报出「参数不是合法 JSON」这类错。
    中间转成 dict 再序列化回去，纯属白跑一趟，还多了一个「在这里就抛异常」的机会。
    """


def tool_input(call: ToolCall) -> dict[str, Any]:
    """把 `ToolCall.input` 解析成对象；解析不出来就给个空字典。

    **这个函数存在的唯一理由是「不许抛」。** Anthropic 的 `tool_use` 块要的 `input` 是
    一个对象，适配器回灌时必须 `json.loads` 一次——而模型偶尔真的会发出非法 JSON
    （截断、多一个逗号、混进注释）。那一下要是抛出去，崩的就不只是这一条工具调用，
    而是整轮请求，界面上会变成一句看不懂的异常。

    解析失败时工具侧本来就已经返回了「参数不是合法 JSON」的结构化错误
    （`docs/v2/spec.md` F9），回灌时给个空对象正好对得上：模型看到的仍然是那条错误说明，
    只是 assistant 回合里的 input 是空的——它据此重试即可，比整个会话挂掉强得多。
    """
    try:
        parsed = json.loads(call.input)
    except (ValueError, TypeError):
        # ValueError 覆盖 JSONDecodeError（后者是它的子类）；TypeError 是防 input 被
        # 塞了非字符串。两种情况都不该发生，但这是个「不许抛」的函数，兜住。
        return {}
    # JSON 合法但不是对象（比如 `[1,2]` 或 `null`）同样没用——工具要的是键值对。
    return parsed if isinstance(parsed, dict) else {}


@dataclass
class ToolResult:
    """协议无关地承载一次工具执行结果。"""

    tool_call_id: str
    """对应 `ToolCall.id`。"""

    content: str
    """执行产出：成功时的内容，失败时的结构化错误说明。"""

    is_error: bool = False
    """是否为错误结果（`docs/v2/spec.md` F9）。"""


@dataclass
class ToolDefinition:
    """注册中心导出的、协议无关的工具定义（F1/F3）。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    """完整 JSON Schema：`{"type": "object", "properties": {...}, "required": [...]}`。

    存整份 schema 而不是拆成 properties / required 两个字段：OpenAI 的
    `function.parameters` 直接吃整份，拆了反而要再拼回去；Anthropic 那边要取两个键，
    那点活儿放在适配器里，只有一处。
    """


@dataclass
class Message:
    """一条对话消息。

    system 提示词**不由这里携带**，各适配器在发请求时自己注入（协议不同，注入的位置也
    不同——Anthropic 是顶层 `system` 参数，OpenAI 兼容侧是 messages 里的第一条）。

    三个角色各自的字段用法（`docs/v2/plan.md`「工具结果在 Message 的形态」）：

    - `user`      —— 只有 `content`
    - `assistant` —— `content`（正文，可能为空）+ 可选的 `tool_calls`
    - `tool`      —— 只有 `tool_results`，`content` 为空

    没有做成「content 是若干 content block 的联合类型」：两个 SDK 的工具语义本来就是
    「靠 id 关联的 tool_use / tool_result 列表」，本阶段的工具结果又全是文本，上联合类型
    是多余抽象。协议之间的差异（Anthropic 的工具结果要进一条 user 消息、OpenAI 有独立的
    tool 角色）由适配器吸收掉。
    """

    role: Role
    # 默认空串有两个用处：`tool` 回合根本没有正文；`assistant` 回合也可能只调工具不说话。
    content: str = ""
    #: 仅 assistant 回合。默认空列表，让 v1 那些 `Message(role=..., content=...)` 的构造
    #: 原样还能用——这是能平滑扩上来的前提。
    tool_calls: list[ToolCall] = field(default_factory=list)
    #: 仅 tool 回合：对应上一批 tool_calls 的执行结果，靠 id 配对。
    tool_results: list[ToolResult] = field(default_factory=list)


@dataclass
class StreamEvent:
    """流式过程中吐给上层的一个事件。

    四种形态，上层按顺序判断即可：

    - `text` 非空       → 一段正文增量，追加显示
    - `tool_calls` 非空 → 本轮模型请求执行这些工具（在 `done` 之前一次性发出）
    - `done=True`       → 本轮正常结束
    - `err` 非 None     → 出错，本轮到此为止

    思考增量**不走这里**：适配器识别到就直接丢弃（F5），别让它混进正文。

    `tool_calls` 是「攒齐了一次性给」，而不是像文本那样一片一片来。原因是参数 JSON 被
    协议切成碎片、还可能按 index 交错到达（一次要调多个工具时几条搅在一起），中途任何
    一片都组不成一个能用的调用，上层拿到半截也无事可做。适配器攒齐再上抛，边界最干净。
    """

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
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

    @property
    def supports_tools(self) -> bool:
        """这个接入点**此刻**能不能接受工具定义。

        问的不是「协议支不支持工具」——两条协议都支持。问的是这条链路**当前的配置**
        允不允许：Anthropic 一旦打开 thinking，带 `tool_use` 的 assistant 回合在续答
        回灌时必须附上原来的 thinking 块（含签名），而本阶段按 spec 丢弃 thinking 增量、
        不留签名，那样的请求必被服务端 400。二者现阶段不可兼得。

        做成**显式声明**、由上层据此决定传不传 `tools`，而不是让适配器偷偷把 `tools`
        丢掉：后者会让配了 `thinking: true` 的用户发现工具静默失灵，且无处可查
        （`docs/v2/plan.md`「关于 thinking 与工具的冲突」）。
        """
        ...

    def stream(
        self, msgs: list[Message], tools: list[ToolDefinition]
    ) -> AsyncIterator[StreamEvent]:
        """发起一轮流式对话。

        `msgs` 是完整的对话历史（含本轮用户输入）。`tools` 是允许本轮使用的工具定义，
        空列表表示不带工具——传什么由上层按 `supports_tools` 决定，适配器只管照发，
        不再自己判断该不该带。

        实现应当是 async generator：调用方 `cancel()` 掉跑它的 task 时，`async for`
        会自然抛 `CancelledError`，SDK 的流由 `async with` 上下文自动清理。
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
