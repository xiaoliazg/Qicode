# v2 工具系统 Plan

> 基于已批准的 [spec.md](spec.md)。本文档与语言相关（Python 3.12+）。
> SDK 调用方式已对着 `anthropic`（`AsyncAnthropic`，支持 tool_use streaming）与
> `openai`（`AsyncOpenAI`，chat.completions tool_calls streaming）的文档与源码核对。
> **真机联调只有 OpenAI 兼容端点**（与 v1 相同，手上没有 anthropic 密钥）——
> 所以 anthropic 侧的改动一律以单测兜底，端到端那条留待有密钥时补。

## 架构概览

在 v1 的「provider → conversation → tui」三件套之上，新增两个包并扩展若干处：

- **`qicode.tool`（新建）**：统一工具抽象 `Tool`、执行结果 `Result`、注册中心 `Registry`、
  6 个核心工具。零外部依赖，不感知 LLM 协议。
- **`qicode.agent`（新建）**：承载「单轮闭环」编排——请求#1（带工具）→ 收集工具调用 →
  注册中心执行 → 结果回灌进 `Conversation` → 请求#2（续答）→ 最终文本 → 停。
  对外吐出一条 `Event` async generator 供 TUI 渲染。只依赖 `llm`、`tool`、`conversation`，
  不 import anthropic/openai，保持协议无关。
- **`qicode.llm`（扩展）**：`Message`/`StreamEvent` 增加工具字段；新增协议无关类型
  `ToolCall`/`ToolResult`/`ToolDefinition` 与 `ROLE_TOOL` 常量；`Provider.stream` 增加
  `tools` 参数；两个适配器注入工具定义、解析流式工具调用、回灌工具结果。
- **`qicode.conversation`（扩展）**：新增「assistant 工具调用回合」与「工具结果回合」
  的追加方法。
- **`qicode.prompt`（扩展）**：`system_prompt(provider_name, model)` 增补 Agent 角色与
  工具使用约定。
- **`qicode.tui`（扩展）**：`QicodeApp.submit` 改走 `Agent.run`；事件消费 task 处理工具
  事件；新增 `ToolBlock` 渲染工具行与结果摘要。**历史改由 agent 写，TUI 退化为纯渲染器。**
- **`cli.py`（扩展）**：构造 `tool.new_default_registry()` 并注入 `QicodeApp`。

依赖方向（无环）：`tool → llm`；`conversation → llm`；`agent → {llm, tool, conversation}`；
`tui → {agent, tool, conversation, llm, prompt}`；`llm → {config, prompt}`。

## 核心数据结构

### llm 包（`__init__.py` 扩展）

```python
import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol

# 消息角色——新增 ROLE_TOOL。user / assistant 也一并提成常量，别再散落字面量。
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"  # 携带工具执行结果的回合

@dataclass
class ToolCall:
    """协议无关地承载模型发起的一次工具调用（流式拼接完成后）。"""
    id: str            # provider 侧调用 id；回灌结果时配对
    name: str          # 工具名（注册中心按名查找）
    input: str         # 拼接完成的 JSON 参数字符串（raw JSON）

@dataclass
class ToolResult:
    """协议无关地承载一次工具执行结果。"""
    tool_call_id: str  # 对应 ToolCall.id
    content: str       # 执行产出（成功内容或结构化错误文本）
    is_error: bool = False  # 是否为错误结果（F9）

@dataclass
class ToolDefinition:
    """注册中心导出的协议无关工具定义。"""
    name: str
    description: str
    input_schema: dict[str, Any]  # 完整 JSON Schema：type/properties/required

# Message 扩展：assistant 回合可带 tool_calls；ROLE_TOOL 回合带 tool_results。
@dataclass
class Message:
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)     # 仅 assistant
    tool_results: list[ToolResult] = field(default_factory=list)  # 仅 ROLE_TOOL

# StreamEvent 扩展：在 text/done/err 之外，本轮结束时一次性上抛 tool_calls。
@dataclass
class StreamEvent:
    text: str = ""                       # 文本增量
    tool_calls: list[ToolCall] = field(default_factory=list)  # 非空：本轮模型请求执行这些工具（done 之前发出）
    done: bool = False
    err: Exception | None = None
```

`Provider.stream` 签名变更（`tools` 是**必填**位置参数，只有 agent 一个调用方，
不设默认值——省得日后有人漏传还以为工具没生效）：

```python
class Provider(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def model(self) -> str: ...
    @property
    def supports_tools(self) -> bool:
        """本 provider 当前配置下能不能用工具。

        给的是「能不能」，不是「想不想」——agent 据此决定要不要把工具定义塞进请求。
        Anthropic 侧开了 thinking 就返回 False（见「关于 thinking 与工具的冲突」）。
        """
        ...
    def stream(
        self,
        msgs: list[Message],
        tools: list[ToolDefinition],
    ) -> AsyncIterator[StreamEvent]: ...
```

`tools` 为空表示本次请求不带工具。续答请求（请求#2）仍传入 `tools`（与真实协议一致），
但编排层忽略其再次返回的工具调用（单轮）。

`supports_tools` 而不是让适配器自己偷偷把 `tools` 丢掉：那样用户配了 `thinking: true`
却悄无声息地用不了工具，是最难查的一种。声明出来，上层才能给一行提示。

`ToolCall.input` 是**原始 JSON 字符串**而不是 `dict`：注册中心与各工具的 `execute(args: str)`
本来就吃字符串，少一次「序列化→反序列化」的往返；模型偶尔会发出非法 JSON，字符串形态
能原样交给工具去报错，而不是在拼接阶段就炸掉。

但回灌路径（Anthropic 的 `tool_use` 块要的是对象）必须做一次 `json.loads`，
那一步是**可能抛的**。所以 `llm` 层配一个解析兜底：

```python
def tool_input(call: ToolCall) -> dict[str, Any]:
    """把 `ToolCall.input` 解析成对象；非法 JSON 返回 `{}`（N4：这里绝不抛）。

    解析失败时工具侧本来就会返回「参数不是合法 JSON」的结构化错误，
    回灌时给个空对象即可对上，犯不上让适配器崩在序列化上。
    """
```

### tool 包（新建）

```python
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

@dataclass
class Result:
    """工具执行结果——永远以值类型返回，从不抛 Python 异常给上层。"""
    content: str            # 回灌给模型的文本（已截断/带行号等）
    is_error: bool = False  # True 表示结构化错误，content 即错误描述

@runtime_checkable
class Tool(Protocol):
    """统一工具抽象（F1）。"""
    def name(self) -> str: ...                    # 模型看到的工具名，如 "read_file"
    def description(self) -> str: ...             # 给模型的用途说明
    def parameters(self) -> dict[str, Any]: ...   # 手写 JSON Schema
    async def execute(self, args: str) -> Result: ...
    # 注：args 是 raw JSON 字符串；超时由外部 asyncio.wait_for 控制

class Registry:
    """集中登记、按名查找、导出定义、按名执行。"""
    def __init__(self) -> None:
        self._order: list[str] = []      # 保持注册顺序，导出稳定
        self._tools: dict[str, Tool] = {}

    def register(self, t: Tool) -> None: ...       # 重名抛 ValueError
    def get(self, name: str) -> Tool | None: ...
    def definitions(self) -> list[ToolDefinition]: ...  # F3/AC1：按序导出
    async def execute(self, name: str, args: str, timeout: float) -> Result: ...
    # F5/F9：未知工具兜底为 is_error；超时由 asyncio.wait_for 抛 TimeoutError → 转 Result

def new_default_registry() -> Registry:
    """构造并注册 6 个工具。"""
    ...

DEFAULT_TIMEOUT: float = 30.0  # 单个工具执行的默认超时秒数（N1，不可配）
```

每个工具用 `@dataclass` + 手写 `from_json` 解析入参，或直接 `json.loads` 后用 `dict.get`；
解析失败转为 `Result(is_error=True, ...)`。

| 工具名 | 参数（JSON Schema） | 成功结果 | 错误结果 |
|--------|--------------------|---------|---------|
| `read_file` | `path`(必填) | 带行号文本（`f"{n:6d}\t{line}"` 风格，≤2000 行 / ≤256KB，超出截断标注 `[truncated]`） | 不存在/不可读/是目录 |
| `write_file` | `path`(必填)、`content`(必填) | `Path.parent.mkdir(parents=True, exist_ok=True)` 后覆盖写，返回路径与字节数 | 写入失败 |
| `edit_file` | `path`、`old_string`、`new_string`(均必填) | `content.count(old)==1` 时唯一替换并写回 | 0 处→「未找到匹配」；>1 处→「匹配到 N 处，old_string 不唯一，请提供更长上下文」 |
| `bash` | `command`(必填) | `asyncio.create_subprocess_shell(..., stdout=PIPE, stderr=PIPE)` 执行，返回 stdout/stderr/exit_code（合并视图截断 ~30000 字符） | 超时（is_error）；**非零退出也是 is_error**，content 里仍带完整 stdout/stderr/exit_code |
| `glob` | `pattern`(必填，如 `**/*.py`)、`path`(可选，默认 cwd) | `pathlib.Path(root).glob(recursive=True)` 匹配（≤100，排序） | 无匹配返回空说明（**非** is_error，见技术决策） |
| `grep` | `pattern`(必填，Python 正则)、`path`(可选)、`glob`(可选文件名过滤) | `re.compile` + 逐行扫，`file:line:content` 列表（≤100，超出标注） | 正则非法（is_error）；无命中返回空说明（**非** is_error） |

> `bash` 非零退出判为 `is_error`：spec F9 把「命令超时 / 非零退出」与「文件不存在」
> 并列成「工具执行失败」。模型自己挑的命令跑挂了，就该按失败回灌，
> 让它一眼看出「这条没成」而不是从一堆输出里自己琢磨。

### agent 包（新建）

```python
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator

class Phase(Enum):
    START = "start"  # 工具开始执行
    END = "end"      # 工具执行完毕

@dataclass
class ToolEvent:
    """一次工具调用的开始/结束（供 TUI 渲染工具行与结果摘要）。"""
    name: str
    args: str = ""            # 参数预览（用于 ● name(args)）
    phase: Phase = Phase.START
    result: str = ""          # phase=END：结果摘要
    is_error: bool = False    # phase=END：是否错误

@dataclass
class Event:
    """单轮闭环对外事件流元素，TUI 据非 None 字段分派渲染。"""
    text: str = ""                 # 文本增量（preamble 或最终答复）
    tool: ToolEvent | None = None  # 工具调用开始/结束
    done: bool = False             # 本轮结束
    err: Exception | None = None   # 出错（不中断会话）

class Agent:
    """持有 provider 与注册中心，执行单轮闭环；同时是**唯一往 Conversation 写的人**。"""
    def __init__(self, provider: Provider, registry: Registry) -> None:
        self._provider = provider
        self._registry = registry

    async def run(self, conv: Conversation) -> AsyncIterator[Event]:
        """执行单轮闭环，async generator 吐出事件流。调用方 cancel() 该 task 即终止。"""
        ...
```

## 模块设计

### `qicode.tool`

**职责：** 提供 6 个工具的统一抽象与执行；集中登记与导出；所有失败包成
`Result(is_error=True)` 而非抛异常（F1/F2/F9/N4）。

**对外接口：** `Tool`、`Result`、`Registry`、`new_default_registry`、`DEFAULT_TIMEOUT`。

**依赖：** 标准库（`pathlib`、`asyncio`、`re`、`json`）、`qicode.llm`（仅为
`definitions()` 返回 `list[ToolDefinition]`）。

**关键实现点：**

- Schema 手写为 `dict[str, Any]`：OpenAI 直接吃整对象；Anthropic 由 llm 适配器取
  `["properties"]`/`["required"]`（SDK 的 `input_schema` 不收 `"type": "object"` 之外的键）。
- `read_file` 带行号、行/字节上限、`[truncated]` 标注（N5/AC2）。
- `edit_file` 唯一匹配语义 + 含计数的可区分错误（AC4）。
- `bash` 用 `asyncio.create_subprocess_shell(cmd, stdout=PIPE, stderr=PIPE)`，外层
  `asyncio.wait_for(..., timeout)` 控制超时；超时则 `proc.kill()` 并返回结构化错误
  （AC5/N1）。asyncio 按 OS 自动选 `shell`，跨平台不用自己分支。
- `glob` / `grep` 遍历期间 `await asyncio.sleep(0)` 让出 event loop，别让一次大范围扫描
  把 TUI 卡住（N2）。
- 空 `args`（OpenAI 可能给空串而非 `{}`）归一为 `"{}"` 处理，避免误报参数错误。

### `qicode.agent`

**职责：** 单轮闭环编排（F5/F6），保证 AC9 单轮上限；把 provider 的 `StreamEvent` 与
工具执行翻译成统一 `Event` 异步流。**并且独占 `Conversation` 的写权限**——TUI 从 v1 的
「自己 add_assistant」退化成纯渲染器，历史怎么写由这一层说了算。

**对外接口：** `Agent`、`Event`、`ToolEvent`、`Phase`。

**依赖：** `qicode.llm`、`qicode.tool`、`qicode.conversation`、`asyncio`。

**run 算法：**

1. `defs = self._registry.definitions() if self._provider.supports_tools else []`。
2. **请求#1**：`async for se in self._provider.stream(conv.messages(), defs):` 转发 `text`
   增量给调用方、累积完整 preamble 文本、收集 `tool_calls`；出错则 `yield Event(err=...)` 后结束。
3. 若无 `tool_calls`：`conv.add_assistant(preamble)`，`yield Event(done=True)`，结束
   （纯文本回合，与 v1 行为等价）。
4. 有 `tool_calls`：`conv.add_assistant_with_tool_calls(preamble, calls)`。
5. 顺序执行每个 call：`yield Event(tool=ToolEvent(name, args, Phase.START))` →
   `r = await self._registry.execute(call.name, call.input, DEFAULT_TIMEOUT)` →
   `yield Event(tool=ToolEvent(name, phase=Phase.END, result=r.content, is_error=r.is_error))` →
   收集 `ToolResult(tool_call_id=call.id, content=r.content, is_error=r.is_error)`。
6. `conv.add_tool_results(results)`。
7. **请求#2**：`async for se in self._provider.stream(conv.messages(), defs):` 转发最终答复
   `text`、累积 final 文本；**忽略**其返回的任何 `tool_calls`（单轮，AC9）。
8. `conv.add_assistant(final)`，`yield Event(done=True)`。

- 调用方 `cancel()` 此 task（退出 / Ctrl+C）时 `async for` 自然抛 `CancelledError`，
  沿上游传播终止；工具执行经 `asyncio.wait_for` 受 `DEFAULT_TIMEOUT` 约束（N1）。
- `ToolEvent.args` 取 `input` 的简短预览（截到 80 字符），只用于 `● name(args)` 那一行。

### `qicode.llm`（扩展）

**职责：** 协议无关请求/响应抽象 + 两协议工具调用全流程（F3/F4/F6/F7）。

**`anthropic_provider.py` 关键改动：**

- 新增 `supports_tools` 属性：`return not self._cfg.thinking`（理由见文末）。
- 请求构造加 `params["tools"] = _to_anthropic_tools(tools)`：每项
  `{"name": d.name, "description": d.description, "input_schema": d.input_schema}`。
- 流循环沿用 v1 的 `async with self._client.messages.stream(**params) as stream:`；按
  `event.type` 分派：`content_block_delta` + `delta.type == "text_delta"` →
  `yield StreamEvent(text=delta.text)`；`thinking_delta` / `input_json_delta` 跳过
  （SDK 内部累加器会保留完整 input JSON，不必自己拼 PartialJSON）。
- 流结束后取 `final_message = await stream.get_final_message()`：若
  `final_message.stop_reason == "tool_use"`，遍历 `final_message.content`，对 `ToolUseBlock`
  收集 `ToolCall(id=block.id, name=block.name, input=json.dumps(block.input))`，
  `yield StreamEvent(tool_calls=calls)`。
- `_to_anthropic_messages` 扩展：assistant 回合若有 `tool_calls`，content 用
  `[{"type": "text", "text": preamble}, {"type": "tool_use", "id": ..., "name": ...,
  "input": tool_input(call)}]` 数组；`ROLE_TOOL` 回合把每个 `ToolResult` 用
  `{"type": "tool_result", "tool_use_id": id, "content": content, "is_error": is_error}`
  拼进**一条 user 消息**的 content 数组。

**`openai_provider.py` 关键改动：**

- 新增 `supports_tools` 属性，直接 `return True`（这套协议没有 thinking 的约束）。
- 请求构造加 `params["tools"]`：每项
  `{"type": "function", "function": {"name": d.name, "description": d.description,
  "parameters": d.input_schema}}`。
- 流循环沿用 v1 的 `async with` 包住 stream；按 index 维护
  `tool_calls_buf: dict[int, dict]`，把 `delta.tool_calls` 中每片
  `{index, id?, function.name?, function.arguments?}` 累加合并（id/name 取首次出现，
  arguments 拼接）；正文 `delta.content` 仍 yield 文本增量。
- 流结束后（`finish_reason == "tool_calls"` 或 buf 非空）按 index 排序组
  `ToolCall(id, name, input=arguments_buf)`（空 arguments 归一为 `"{}"`），
  `yield StreamEvent(tool_calls=calls)`。
- `_to_openai_messages` 扩展：assistant 回合若有 `tool_calls`，发
  `{"role": "assistant", "content": preamble or None, "tool_calls": [{"id": c.id,
  "type": "function", "function": {"name": c.name, "arguments": c.input or "{}"}}]}`；
  `ROLE_TOOL` 回合每个 `ToolResult` 发一条
  `{"role": "tool", "tool_call_id": r.tool_call_id, "content": r.content}`。

### `qicode.conversation`（扩展）

```python
def add_assistant_with_tool_calls(self, text: str, calls: list[ToolCall]) -> None:
    """assistant 工具调用回合。"""
    self._messages.append(Message(role=ROLE_ASSISTANT, content=text, tool_calls=list(calls)))

def add_tool_results(self, results: list[ToolResult]) -> None:
    """ROLE_TOOL 结果回合。"""
    self._messages.append(Message(role=ROLE_TOOL, tool_results=list(results)))
```

保留 `add_user`/`add_assistant`/`messages` 不变。`messages()` 返回副本那条约定
（v1 那段关于「边迭代边追加会静默死循环」的说明）在 v2 更重要了——现在
`conv.messages()` 会被**调两次**（请求#1、请求#2），中间还夹着工具执行。

### `qicode.tui`（扩展）

**职责：** 渲染 `agent.Event`（文本 / 工具行 / 结果摘要 / 错误 / 结束），保持非阻塞（N2）。
**只管画，不写历史。**

v1 的对话区模型（`VerticalScroll` + 一块块 `ReplyBlock`）在 v2 原样沿用，只多一种块：

- `view.py` 新增 `ToolBlock(Horizontal)`，与 `ReplyBlock` 同构——左列圆点、右列正文，
  只是正文是**两行**：

  ```python
  class ToolBlock(Horizontal):
      """对话区里的一条工具调用：左边一个圆点，右边「调用行 + 结果行」。"""

      def show_running(self, name: str, args: str) -> None:
          """执行中：只显示 ● name(args)（F8 的「工具行随流式实时出现」）。"""

      def show_result(self, name: str, args: str, summary: str, is_error: bool) -> None:
          """执行完：调用行下面挂一行缩进的 ⎿ 结果摘要（AC11）。"""
  ```

  配色沿用 v1 的一套：圆点 `bold cyan`（与助手回复的 `bold` 区分开），
  调用行 `name(args)` 加粗，结果摘要 `dim`、错误 `red`（F9 的「UI 可区分」）。
  结果摘要 UI 侧截断到 ~8 行。
- `streaming_body` 增加一种形态：正在跑工具时显示 `● name(args) Running…` +
  与 v1 同一个盲文转轮——**不新增第二套动画**，`SPINNER_FRAMES` 照用。
- `QicodeApp.__init__(self, providers: list[ProviderConfig], registry: Registry)`：
  存 `self._registry`。（v1 的 `__version__` 是 import 进来的，不用当参数传。）
- 新增成员 `self._cur_tool: ToolBlock | None`——本轮正在执行的那个工具块。
- `submit`：`conv.add_user(text)` 之后 `self._stream_task = asyncio.create_task(
  self._consume_agent_events())`，task 内构造 `Agent(self.provider, self._registry)`
  后 `async for ev in agent.run(self.conv):` 分派。
- `_consume_agent_events` 分派（**映射到 v1 那套「开块 / 刷块 / 定型」的动作上**）：
  - `ev.text` 非空：若当前没有回复块（刚跑完工具）先 `_start_reply_block()`；
    然后 `cur_reply += ev.text`，`_refresh_streaming()`。
  - `ev.tool` 且 `phase == START`：把当前回复块**定型**——`cur_reply` 非空就
    `show_reply(cur_reply, elapsed)`（这就是 preamble），为空则把这块空块从对话区
    摘掉，免得留一个孤零零的圆点；然后 `_start_tool_block()` 挂新 `ToolBlock` 并
    `show_running(name, args)`。
  - `ev.tool` 且 `phase == END`：`self._cur_tool.show_result(...)`；清 `self._cur_tool`。
    **这一块就此定型，不再改动**——下一条正文增量会另开新的回复块。
  - `ev.done`：定型最终答复，`_end_turn()`。
  - `ev.err`：`_finish_with_error(ev.err)`（v1 的 `_safe_message` 脱敏照走）。
- 顺序天然保序：全程单个 event loop，`VerticalScroll.mount()` 按事件到达顺序追加，
  没有需要加锁的地方。

## 模块交互

```
用户提交
  └─ QicodeApp.submit: conv.add_user(text); _start_reply_block(); asyncio.create_task(_consume_agent_events())
       └─ _consume_agent_events:
            └─ agent = Agent(provider, registry); async for ev in agent.run(conv):
                 ├─ 请求#1: async for se in provider.stream(conv.messages(), registry.definitions()):
                 │     └─ 适配器: 注入 tools → 流式拼接 → StreamEvent{text…} / StreamEvent{tool_calls}
                 │     → agent 转发 Event{text}（preamble），收集 calls
                 ├─ 无 calls → conv.add_assistant(preamble); yield Event(done=True)
                 └─ 有 calls:
                      ├─ conv.add_assistant_with_tool_calls(preamble, calls)
                      ├─ for call:
                      │    ├─ yield Event(tool=START)  ─→ TUI: 定型 preamble 块、挂 ToolBlock.show_running()
                      │    ├─ await registry.execute(name, args, DEFAULT_TIMEOUT)
                      │    └─ yield Event(tool=END)    ─→ TUI: ToolBlock.show_result()
                      ├─ conv.add_tool_results(results)
                      ├─ 请求#2: async for se in provider.stream(...) → yield Event(text)（最终答复）
                      │     （适配器把 conv 里的 tool_use/tool_result 回合映射为各自线格式）
                      └─ conv.add_assistant(final); yield Event(done=True)  ─→ TUI: 定型并 _end_turn()
```

并发：`conv` 只在单个 event loop 上被 agent 那条 task 写——`submit` 在 `create_task`
之前 `add_user`，之后 TUI 侧**只读**（渲染器不再调 `add_assistant`）。Textual 的渲染
回到主协程序列化执行，与 `conv` 互不干扰（N2）。

## 文件组织

```
qicode/
├── pyproject.toml                          — 不变（已含 anthropic/openai/textual/rich/pyyaml）
├── src/qicode/
│   ├── cli.py                              — 修改：new_default_registry() 注入 QicodeApp
│   ├── llm/
│   │   ├── __init__.py                     — 修改：ToolCall/ToolResult/ToolDefinition/ROLE_*/tool_input；扩展 Message/StreamEvent；Provider.stream 加 tools
│   │   ├── anthropic_provider.py           — 修改：注入 tools；stream 解析 tool_use；_to_anthropic_messages 支持 tool_use/tool_result
│   │   └── openai_provider.py              — 修改：注入 tools；按 index 拼 tool_calls；_to_openai_messages 支持 assistant.tool_calls/tool 消息
│   ├── tool/                               — 新建
│   │   ├── __init__.py                     — Tool Protocol、Result、Registry、new_default_registry、DEFAULT_TIMEOUT、_truncate
│   │   └── read_file.py / write_file.py / edit_file.py / bash.py / glob_tool.py / grep_tool.py
│   ├── agent/                              — 新建
│   │   └── __init__.py                     — Agent、Event、ToolEvent、Phase、run、_stream_once
│   ├── conversation.py                     — 修改：add_assistant_with_tool_calls、add_tool_results
│   ├── prompt.py                           — 修改：system_prompt 增 Agent 角色与工具约定
│   └── tui/
│       ├── app.py                          — 修改：__init__ 接 registry；_cur_tool；_consume_agent_events
│       ├── view.py                         — 修改：ToolBlock；streaming_body 增加工具执行形态
│       └── stream.py                       — **删除**（见下）
└── tests/
    ├── test_tool.py                        — 新建：注册中心 + 各工具单测
    ├── test_agent.py                       — 新建：单轮闭环（fake provider）：AC8 链路、AC9 单轮
    └── test_tui_stream.py                  — **删除**（`consume` 没了）
```

**关于删掉 `tui/stream.py`：** v1 的 `consume(provider, msgs, on_text, on_done, on_error)`
是「驱动一次 provider 流并把结果回调出去」。v2 里这件事由 `Agent._stream_once` 干，
而且它要的不是回调、是「累积 preamble + 收集 tool_calls」，签名对不上，
硬留着就是一份没人调的死代码。它唯一的另一个导出 `TICK_INTERVAL`（界面刷新节奏）
迁到 `app.py`——那是界面的东西，不是 llm 的。

`.qicode/config.yaml` 的**格式**与 v1 完全一致：v2 不新增任何配置项
（超时内置、工具集固定，见 spec「不做的事」）。

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 工具调用循环放哪 | 新建 `qicode.agent` 包，TUI 退化为渲染器 | 循环（请求#1→执行→请求#2）塞不进 v1 的单次 `_consume_stream` 协程；独立包可无 UI 单测（AC8/AC9），只依赖 llm+tool+conversation，不泄漏 SDK 类型。命名 `agent` 而非 `runner`：概念即 Agent，本阶段恰为单轮。 |
| 历史由谁写 | `Agent` 独占写权限，TUI 只读 | v1 里是 TUI 在 `_finish_with_assistant` 里 `add_assistant`；v2 一轮里有 preamble / tool_result / final 三条要按协议格式入历史，散在渲染层必然写乱。一处写、一处读，边界干净。 |
| 是否用 SDK 的高级 tool-runner | 不用，坚持手写 streaming + 手动单轮 | anthropic Python SDK 暂无自动 tool runner；openai 的 helper 会自动连环到完成，违反 F6/AC9。手写迭代更可控，也与 v1 的风格一脉相承。 |
| 工具定义传入哪一层 | `Provider.stream` 第二参数 `list[ToolDefinition]` | 两 SDK 都把 tools 放 per-request params；续答仍需带；保持 Provider 无状态。 |
| 工具参数 Schema 生成 | 每工具手写 `dict[str, Any]` | OpenAI `parameters` 与 Anthropic `input_schema` 都直接吃 JSON Schema dict；6 个固定工具手写最直白，描述对模型可读性最关键；不引入 `pydantic` 反射（schema 还要剥 `$defs`/`additionalProperties` 噪音）。 |
| `ToolCall.input` 的类型 | 原始 JSON **字符串** | 注册中心与 `execute(args: str)` 本来就吃字符串；模型偶发非法 JSON 时能原样交给工具去报错，而不是在拼接阶段就崩。 |
| 回灌路径的 JSON 解析 | `llm.tool_input(call)` 兜底，失败返回 `{}` | Anthropic 的 `tool_use.input` 要对象，那一步 `json.loads` 会抛；解析失败时工具侧本就返回了「参数非法」的结构化错误，给个空对象能对上，不必让适配器崩（N4）。 |
| 流式工具参数拼接 | Anthropic 用 `stream.get_final_message()` 拿汇总；OpenAI 按 `delta.tool_calls[i].function.arguments` 按 index 累加 | Anthropic SDK 自带累加器，避免手写 PartialJSON 的边界；OpenAI 必须按 index 拼（多工具时各条同时分片）。 |
| Glob/Grep 实现 | 纯标准库（`pathlib.glob`/`re` + `asyncio.sleep(0)` 让出） | 零额外依赖、跨平台；spec 要求保持简单、不引入配置。 |
| Bash 实现与超时 | `asyncio.create_subprocess_shell` + `asyncio.wait_for(..., DEFAULT_TIMEOUT)` | shell 自带管道/重定向；asyncio 原生超时 + `proc.kill()`；30s 内置不可配（spec：超时不配置化）。asyncio 按 OS 自动选 shell，不用自己分支。 |
| 非零退出算不算工具失败 | 算，`is_error=True`，但 content 里保留完整 stdout/stderr/exit_code | spec F9 把「命令超时 / 非零退出」与「文件不存在」并列成工具执行失败；模型自己挑的命令跑挂了就该按失败回灌，一眼看出「这条没成」。细节照样给全，不影响它判断。 |
| 搜索无命中算不算失败 | 不算，`is_error=False`，返回「无命中」说明 | 「没有匹配」本身就是一个有效答案，标成错误会让模型以为搜索坏了、去反复重试。这是对 spec F9 举例列表的一处**有意偏离**，F9 已相应改掉。 |
| 工具失败的表达 | `execute` 返回 `Result(content, is_error)`，从不抛异常给上层 | F9/N4：所有失败包成结构化结果回灌，程序不崩，上层无需区分 try/except 路径。 |
| 工具结果在 Message 的形态 | 平铺字段（assistant 加 `tool_calls`，`ROLE_TOOL` 加 `tool_results`） | 两 SDK 的工具语义本就是 id 关联的 tool_use/tool_result 列表；通用 content-block 联合属过度设计（本阶段结果均文本）。适配器吸收差异（Anthropic 结果进 user 消息、OpenAI 用 tool 角色）。 |
| 工具行的视觉 | 复用 v1 的 `●` 两列版式，圆点 `bold cyan`、调用行加粗、结果 `dim`/错误 `red` | v1 的 `ReplyBlock` 已经把「两列排版」的坑趟平了（见 `docs/v1/plan.md` 技术决策），`ToolBlock` 照着做即可，不另起一套。圆点换颜色是为了跟助手回复区分（F8 的「可区分」）。 |
| 工具执行中的指示 | 复用 v1 的 `SPINNER_FRAMES`，在 `ToolBlock` 上显示 `name(args) Running…` | N2 要求执行期间有进行中指示；v1 已经有转轮和 `TICK_INTERVAL` 刷新，再引一套动画只是多一处要维护。 |
| UI 截断 vs 回灌截断 | 两者分离：UI 摘要 ~8 行；回灌为工具级上限（read 2000 行 / bash 30000 字符等） | AC11/N5 要界面截断，但模型需要较完整内容；尾部统一加 `[truncated]` 标注。 |
| 续答请求是否带 tools | 带，但忽略其返回的工具调用 | 与真实协议一致（OpenAI assistant+tool 之后不带 tools 也行，但带上更稳）；F6/AC9 由 agent 不再触发执行来保证单轮。 |
| thinking 与工具组合 | **本阶段：`Provider.supports_tools` 在开了 thinking 时返回 False，agent 因而不发工具定义** | Anthropic 在 thinking 启用时要求回灌带 tool_use 的 assistant 回合附原 thinking 块（含 signature），而本阶段按 spec 丢弃 thinking 增量、不留签名，续答请求必被 400。二者现阶段不可兼得。做成显式声明 + 界面提示，而不是让适配器偷偷丢掉 `tools`。 |
| 空最终答复 | 续答为空时用单轮提示占位并推给 UI | 空 assistant 回合会破坏下一轮请求（Anthropic 要求非空内容 + 角色交替）；占位提示同时满足 AC9 的「单轮上限提示」。 |
| 空参数归一 | OpenAI 侧空 arguments 归一为 `"{}"` | 无参工具的 arguments 可能是空串，回灌时须是合法 JSON，否则严格兼容端点对 `"arguments": ""` 返回 400。 |
| grep 超长行 | 显式标注未完整搜索 | `for line in file` 遇超长行可能阻塞或读爆内存；用分块读 + 最大长度判定，超出标注「该行过长，未完整搜索」，避免假「无命中」误导模型。 |
| 对话区顺序 | 单 event loop 内 `VerticalScroll.mount()` 按事件顺序追加 | 全程一条 task 驱动、一次 append 一个块，Python 的 asyncio 单线程模型天然保序，不需要任何同步原语。 |
| 工具命名 | `read_file`/`write_file`/`edit_file`/`bash`/`glob`/`grep` | 符合 OpenAI 函数名规则（`a-zA-Z0-9_-`）与 Claude Code 习惯；TUI 工具行显示 `● name(关键参数)`。 |
| 异步测试怎么写 | 沿用 v1：同步 `def test_...` 里 `asyncio.run(...)` | v1 的 `tests/test_tui_stream.py`、`test_llm_providers.py` 全是这个写法，**不引入 `pytest-asyncio`**。为两个新测试文件引一个新依赖 + `asyncio_mode` 配置，不划算，也让两批测试风格分裂。 |

### 关于 thinking 与工具的冲突

Anthropic 的 extended thinking 一旦启用，带 `tool_use` 的 assistant 回合在回灌时
**必须**原样带上对应的 thinking 块（含 `signature`）。而 spec F4 明确说「思考增量沿用
v1 接收即丢弃」——签名也就一起丢了。续答时缺签名 → 400。

三条路：

| 方案 | 代价 |
|------|------|
| A. 配置开了 thinking 就不发工具定义（**本阶段取这条**） | 开 thinking 的用户在 v2 里用不上工具；v1 的行为一字不变 |
| B. 带工具的请求强制关 thinking | 用户配了 `thinking: true` 却被静默关掉，比 A 更难察觉 |
| C. 本阶段就保留 thinking 块与签名 | 改动面从「解析」扩到「存储 + 回灌 + 消息结构」，且只有真机密钥才能验 |

取 A，落成 `Provider.supports_tools`：Anthropic 侧开了 thinking 就是 `False`，
OpenAI 侧恒为 `True`。`QicodeApp.on_mount` 里发现选中的 provider 不支持工具时，
在对话区打一行说明（「当前配置开启了 thinking，本阶段工具暂不可用」）——
**明说**，而不是让用户对着一个永远不调用工具的模型发呆。

留待后续阶段连同 thinking 的完整支持一起做。
