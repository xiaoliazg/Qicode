# 多协议 LLM 终端对话客户端 Plan

## 技术栈
- 语言：Python 3.12+
- TUI：[Textual](https://textual.textualize.io/)（async-first 的 TUI 框架）+ Rich（Markdown/语法高亮渲染）
  + Textual CSS（样式/布局）。实际用到的内置部件：`VerticalScroll`（对话区容器）、
  `Static`（成品块与横幅的基类）、`TextArea`（输入框，多行）、`OptionList`（provider 选择）。
  **不用 `RichLog`**：它的内容不可回改，没法承载「正在流式的那一块」，理由见「技术决策」表。
- 配置：YAML 解析（`pyyaml`，import 名 `yaml`）
- LLM 通信：官方 Python SDK —— `anthropic`（`AsyncAnthropic`）、`openai`（`AsyncOpenAI`），均原生支持
  async 流式（SDK 内部已处理 SSE）

实施时的实际版本（记一笔，省得下次猜）：Textual 8.2.8 / Rich 15.0.0 / anthropic 1.7.0 /
openai 3.16.2 / pyyaml 6.0.3；开发链 ruff 0.16.8、mypy 2.3.1、pytest 9.1.1。

## 架构概览（分层）
1. 入口层 `qicode.cli` —— 加载配置、把配置错误变成一行可读信息、启动 Textual App；
   退出后把会话重放到主屏幕。
2. 配置层 `qicode.config` —— 读取并校验 `.qicode/config.yaml`，给出 providers 列表。
3. LLM 协议层 `qicode.llm` —— 定义协议无关的 `Provider` Protocol 与统一消息/流式事件类型；
   anthropic、openai 两个适配器各自封装官方 SDK、统一吐出文本增量（思考增量内部丢弃）。
4. 会话层 `qicode.conversation` —— 进程内维护多轮历史，提供完整上下文。
5. 提示词/资源 `qicode.prompt` —— system prompt 拼装、启动横幅（吉祥物像素画 + 蹦跳节奏）。
   纯函数、零依赖。
6. 脱敏 `qicode.redact` —— 把要显示出去的文本里可能夹带的密钥抹掉（N5）。
7. 终端层 `qicode.tui` —— Textual App，含状态机（选择/空闲/流式）、输入框、对话区、
   loading 计时、provider 选择列表；以 async task 消费 `Provider.stream(...)` 的事件生成器。

## 数据流（一轮对话）
用户输入 → TUI 提交 → conversation 追加 user 消息 → 对话区追加用户块 → 挂一块空的
回复块（流式与定型都刷它，位置自始至终不变）→ 调 `Provider.stream(msgs)` →
得到 `AsyncIterator[StreamEvent]` → TUI async task 逐个 `async for` 读文本增量并实时
追加（loading 计时同步进行）→ 收到结束事件 → 用 Rich Markdown 渲染整段、刷进**同一块**
回复块 → conversation 追加 assistant 消息 → 回到空闲。

> 备注：Python 的 Textual + asyncio 是 async-first 体系，直接 `async for event in
> provider.stream(...)` 即可驱动 UI；没有 goroutine / channel / `tea.Cmd` 的胶水层。

## 核心数据结构与接口

```python
# ───────── config 层 ─────────
from dataclasses import dataclass, field
from typing import Literal

@dataclass
class ProviderConfig:
    name: str                          # 状态栏左侧显示
    protocol: Literal["anthropic", "openai"]
    api_key: str
    model: str                         # 状态栏右侧显示
    base_url: str | None = None        # None 则用 SDK 默认端点
    thinking: bool = False             # 仅 anthropic 生效

@dataclass
class Config:
    providers: list[ProviderConfig] = field(default_factory=list)

def load(path: str) -> Config: ...     # 加载 + 校验

# ───────── llm 层（协议无关）─────────
from typing import Protocol, AsyncIterator

@dataclass
class Message:
    role: Literal["user", "assistant"]
    content: str

@dataclass
class StreamEvent:
    text: str = ""                     # 文本增量
    done: bool = False                 # 本轮正常结束
    err: Exception | None = None       # 出错（与 done 互斥）

class Provider(Protocol):
    @property
    def name(self) -> str: ...         # -> 状态栏左
    @property
    def model(self) -> str: ...        # -> 状态栏右
    # 发起一轮流式对话；内部注入内置 system prompt 与 thinking 配置；
    # 思考增量内部丢弃；以 async generator 吐出 StreamEvent；
    # 调用方 cancel() 该 task 即终止。
    def stream(self, msgs: list[Message]) -> AsyncIterator[StreamEvent]: ...

def new_provider(cfg: ProviderConfig) -> Provider: ...   # 按 protocol 构造适配器

> **v2 在上面这几个类型上做了扩展**（这里是 v1 的原始设计，改动见 [v2/plan.md](../v2/plan.md)）：
>
> - `Message.role` 多了 `"tool"`，并新增 `tool_calls` / `tool_results` 两个字段；
> - `StreamEvent` 多了 `tool_calls`，适配器在流结束时把拼好的调用吐出来；
> - `Provider` 多了 `supports_tools` 属性，`stream()` 多了 `tools` 参数
>   （传空列表就退回 v1 的行为）。
>
> 三项全是**追加**、都有默认值，v1 的调用方一行都不用改。

# ───────── conversation 层 ─────────
class Conversation:
    def __init__(self) -> None:
        self._messages: list[Message] = []
    def add_user(self, text: str) -> None: ...
    def add_assistant(self, text: str) -> None: ...
    def messages(self) -> list[Message]: ...    # 返回副本

# ───────── prompt 层（纯函数，零依赖）─────────
MASCOT_ART: str                        # 吉祥物字符画（`H`/`F`/`E`/`B`/`.` 逐格标记）
MASCOT_COLORS: dict[str, str]          # 每个字母 -> 颜色；`_render_pixel_art` 翻成 markup
MASCOT_BANNER: str                     # 已是可嵌入 Rich 的 markup（带背景色的空格）

def system_prompt(provider_name: str, model: str) -> str: ...
def render_banner(version: str, cwd: str, art_offset: int = 0) -> str: ...
def bounce_offset(frame: int) -> int   # 第 frame 帧图案抬起几行

BOUNCE_HEADROOM = 1                    # 图案上方留的起跳空间（行）
BOUNCE_FRAMES = (1, 1, 0, 0)           # 蹦一下的逐帧高度
BOUNCE_PERIOD_FRAMES = 40              # 一个周期 40 帧 × 0.1s = 每 4 秒蹦一下
FRAME_INTERVAL = 0.1                   # 动画帧间隔（秒）

# ───────── redact 层 ─────────
MASK = "***"
MIN_SECRET_LENGTH = 8                  # 短于这个长度的「密钥」不抹（多半是占位符）
def redact(text: str, secrets: Iterable[str]) -> str: ...

# ───────── tui 层 ─────────
from enum import Enum

class SessionState(Enum):
    SELECTING = "selecting"            # 多 provider 时的选择界面
    IDLE = "idle"                      # 等待用户输入
    STREAMING = "streaming"            # 等待/接收模型流（loading + 计时）

class QicodeApp(App):
    # 关键 reactive / 成员
    state: SessionState
    providers: list[ProviderConfig]
    provider: Provider | None
    conv: Conversation
    cur_reply: str                     # 本轮 assistant 增量缓冲
    turn_start: float                  # time.monotonic() 计时起点
    _streaming: ReplyBlock | None      # 本轮回复块（对话区里正在被刷的那个子节点）
    _stream_task: asyncio.Task | None  # 当前流式消费 task
    _timer: Timer | None               # Textual 内置定时器，用于秒数计时刷新
    # 对话区是一棵**控件树**（`VerticalScroll`），成品块 mount 进去就再也不动；
    # 正在流式的那一块也是树里的最后一个子节点，位置从生到死不变。

    def submit(self, text: str) -> None: ...          # 同步：只创建 task，不 await
    async def _consume_stream(self) -> None: ...      # 转调 tui.stream.consume
    def _append(self, block: RenderableType) -> None: ...
    def _mount_in_log(self, widget: Widget) -> None: ...
    def _follow_tail(self) -> None: ...               # 溢出后才挂锚定（见下）
    def _start_reply_block(self) -> None: ...

# ───────── tui.stream（不 import textual，可脱离界面单测）─────────
TICK_INTERVAL = 0.1
async def consume(provider, msgs, *, on_text, on_done, on_error) -> None: ...
    # 三个回调保证恰有一个被调用（取消除外）；异常不会逃出去。

# ───────── tui.view ─────────
MARKER = "●"
class PromptArea(TextArea):            # Enter 提交、Alt+Enter / Shift+Enter / Ctrl+J 换行
class ReplyBlock(Horizontal):          # 左列 `●` + 右列正文，两列对齐
class MascotBanner(Static):            # 会蹦的启动横幅
def user_block(text) / error_block(msg) / streaming_body(reply, elapsed)
def marked_markdown(text)              # 只给退出回放用
def status_bar(provider) -> RenderableType
def transcript(messages) -> Group      # 退出后重放
```

## 模块设计

### 模块 `qicode.config`
职责：读取并校验 `.qicode/config.yaml`，产出 providers 列表。**这一层只做「读文件 →
翻译 → 校验」，不碰网络**，上层拿到手里的数据一定是合法的。
对外接口：`load(path) -> Config`；`Config.providers`；`ProviderConfig`；`ConfigError`。
三步走，每步的失败都带上足以定位的上下文（哪个文件、第几项、哪个字段）：
1. 读文件 —— 不存在 / 读不动（权限、编码）分开报，不然用户会去反复确认路径，
   而真正的问题是权限。
2. 解析 YAML —— `_describe_yaml_error` 把 pyyaml 那段带源码片段和 `^` 的
   traceback 样文本压成一句「第几行第几列：什么毛病」，位置信息才不会被淹掉。
3. 映射 dataclass —— 顶层必须是键值对；`providers` 必须是非空列表；每项
   `name` / `protocol` / `api_key` / `model` 非空且是字符串（`api_key: 12345`
   会被 YAML 解析成 int，不在这里拦就一路带到 SDK 里去了）；
   `protocol` 查表收窄成 `ProtocolName`。
   可选字段 `base_url`（不写 = 用 SDK 默认端点）、`thinking`（**只认 bool**，
   写成 `"true"` / `1` 一律报错——放宽到 int 会让 `thinking: 1` 被当成合法的 True）。
任何失败一律抛 `ConfigError`：调用方只需认识这一种异常，message 里已经写明是哪种。
依赖：`pyyaml`、标准库 `dataclasses` / `pathlib` / `typing`。

### 模块 `qicode.llm`
职责：定义协议无关的 `Provider` Protocol 与统一消息/事件类型；按 protocol 构造适配器。
对外接口：`Provider`（typing.Protocol）、`Message`、`StreamEvent`、`new_provider(cfg) -> Provider`。
适配器**在函数体内 import**（`new_provider` 里按 protocol 分派）：没配 anthropic 的人
不必为了跑 openai 而装 `anthropic`，缺哪个 SDK 只在使用它的那条路上报错。
子单元：
- anthropic 适配器（`anthropic_provider.py`）：封装 `anthropic.AsyncAnthropic`。
  把 `list[Message]` 转为 SDK 的 messages 入参，注入
  `system=system_prompt(self._name, self._model)`（**现拼，不是常量**——见
  「模块 `qicode.prompt`」）、按 `cfg.thinking` 设
  `thinking={"type": "adaptive", "display": "summarized"}`；
  （**不用固定预算的 `{"type": "enabled", "budget_tokens": N}`**：那种写法在当前 Claude
  模型 Fable 5/5.1、Opus 5/4.8/4.7、Sonnet 5 上已被移除，传了直接返回 400。
  `display` 也必须显式写——它缺省是 `omitted`，那样服务端不发思考内容，就没有增量
  可「识别但不渲染」，F5 无从谈起。）
  另带 `max_tokens`（够长，不至于把正常回答截断）。
  使用 `async with client.messages.stream(**params) as stream: async for event in stream:`
  迭代，取 `event.type == "content_block_delta"` 且 `event.delta.type == "text_delta"`
  的 `event.delta.text` → `StreamEvent(text=...)`；其余事件（thinking delta、签名、
  用量、起止标记）一律丢弃；正常结束 yield `StreamEvent(done=True)`；
  异常路径 yield `StreamEvent(err=exc)`。`cfg.base_url` 非空时传 `base_url=...` 构造 client。
- openai 适配器（`openai_provider.py`）：封装 `openai.AsyncOpenAI`。把 `list[Message]`
  转为 `messages=[...]`，首条插入
  `{"role": "system", "content": system_prompt(self._name, self._model)}`。
  消息**按 role 分支、两次字面量构造**（`_to_sdk_message`），不能图省事写
  `{"role": msg.role, ...}`：SDK 的消息类型是若干 TypedDict 组成的联合，每个成员各自
  要求 `role` 是它自己的那个字面量常量，用变量填就一个成员也对不上，mypy 连后面迭代
  流的那行也会跟着报错。
  `async with stream: async for chunk in stream:` 迭代，`chunk.choices` 为空时跳过
  （有些兼容实现会发只带用量统计的收尾块，直接取 `[0]` 会凭空把一轮对话判成失败），
  取 `chunk.choices[0].delta.content` 非空时 yield `StreamEvent(text=...)`；
  正常结束 yield `StreamEvent(done=True)`；异常 yield `StreamEvent(err=exc)`。
  `cfg.base_url` 非空时传 `base_url=...`；`thinking` 字段忽略（OpenAI 协议没有这个字段，
  各家兼容网关自行发挥，形状不统一，v1 不猜）。
  那层 `async with stream` 不是装饰：`AsyncStream.close()` 只在**流被读完**时才自动调用，
  取消或中途报错跳出时没人关它，HTTP 连接会一直挂着到 GC。
  共同点：两适配器都把 `stream(...)` 实现为 `async def stream(...) -> AsyncIterator[StreamEvent]`
  的 async generator；`asyncio.CancelledError` **必须原样抛出**（吞掉取消语义就断了，
  SDK 的流也不会被清理）；其余异常宽 catch 是**故意的**——这里是外部服务的边界，
  网络断了、密钥不对、被限流、响应格式变了什么都可能来，一律翻成 `StreamEvent(err=...)`
  交给界面显示，让会话能继续（F11、AC11），而不是把整个 App 打崩。
  依赖：`anthropic`、`openai`、本项目 `config`、`llm`、`prompt`。

### 模块 `qicode.conversation`
职责：进程内维护单会话多轮历史（user/assistant 交替）。
对外接口：`add_user`、`add_assistant`、`messages()`。
依赖：`llm`（`Message` 类型）。

### 模块 `qicode.prompt`
职责：提供 system prompt 拼装与启动横幅。**纯函数、零依赖**——不碰网络、不碰界面、
连 rich 之外的东西都不 import（只用 `rich.markup.escape`）。
对外接口：
- `system_prompt(provider_name, model) -> str` —— **按当前接入点现拼**，不是常量。
  为什么要把这两个值写进去：它们只存在于本地配置里，模型自己看不到；不告诉它，
  用户问「你是什么模型」时它只能答「我不掌握这个信息」（实测原话），或者凭训练数据
  编一个（F4、AC4）。措辞上有意分开两件事——「你是谁、跑在什么模型上」我们知道，
  要求它照实说；「这个模型是哪家公司训练的」我们不知道，就明确说不知道。一句笼统的
  「不要编造」会让它对**两者**都用同一句「我不知道」搪塞过去，那正是要修的毛病。
- `MASCOT_ART` / `MASCOT_COLORS` / `MASCOT_BANNER` —— 吉祥物像素画。
  `_render_pixel_art` 把字符画翻成「**带背景色的空格**」拼出的 markup。
  为什么不用实心块字符 `█` 直接拼：`█` 的显示宽度在不同终端、不同字体下会在 1 格和
  2 格之间摇摆，一旦判成 2 格整幅图就斜掉了；**空格的宽度恒为 1 列**，拿背景色染上
  就是一个宽度绝对可靠的像素。（`view.SPINNER_FRAMES` 选盲文点阵是同一个取舍。）
  `.` 与认不出的字符一律留白，图案里可以用任意符号做占位。
- `bounce_offset(frame) -> int` + 四个常量 —— 蹦跳节奏。一个周期 40 帧 × 0.1 秒 = 每 4 秒
  蹦一下，一次蹦跳 `(1, 1, 0, 0)` 四帧约 0.4 秒；绝大多数帧返回 0，蹦是**偶发**的，
  比一直来回晃更像「活着」，也不抢注意力（F7、AC14）。
- `render_banner(version, cwd, art_offset=0) -> str` —— 唯一带动态输入的函数。
  左边图案、右边一列文字，文字跟**框**垂直居中。两条约束写在 docstring 里：
  ① **任何 `art_offset` 下返回的行数都一样**——横幅是对话区的第一个子节点，高一行
  矮一行下面全跟着回流（`app._follow_tail` 刚修掉的那个毛病），所以图案外套了一个
  固定高度的框（`图案行数 + BOUNCE_HEADROOM`），图案在框里挪、框不动；
  ② 文字落点按**框**高算、与 `art_offset` 无关，图案蹦的时候文字纹丝不动。
  另外两处细节：`cwd` 必须 `escape()`（用户机器上的字符串，可能含 `[`，不转义
  轻则被当成样式吃掉几个字符，重则抛 `MarkupError` 让启动当场崩掉，N6、AC15）；
  图案的**显示宽度**要从原始 `MASCOT_ART` 上量，`MASCOT_BANNER` 是 markup、
  字符串长度跟屏幕列数根本不是一回事（一行 12 格，markup 长达 72 字符），
  拿它去补空格会把右侧文字推出屏幕。底部留一行空白，别跟第一条消息贴在一起。
依赖：`rich.markup`。

### 模块 `qicode.redact`
职责：把要显示出去的文本里可能夹带的密钥抹掉（N5、AC11）。
对外接口：`redact(text, secrets) -> str`；常量 `MASK`、`MIN_SECRET_LENGTH`。
**为什么需要这一层**：适配器把上游的异常原样翻成 `err` 事件交给界面，而 `str(exc)`
是**别人写的字符串**（SDK、网关、反向代理），它完全可能把请求里的凭据抄进错误信息。
实测撞到过一次：拿错 key 打 DeepSeek，回来的错误里带着密钥后四位。那次是服务端自己
掩了码，但这不是我们能担保的事；而这句话的落点是用户**屏幕上的一块可见内容**，
截屏、录屏、共享屏幕都带得走。
**反过来也要小心：抹过头同样是 bug。** 一个 `api_key` 完全可以合法地是个短占位符
（本地 Ollama 常写 `ollama`），无差别替换会把「cannot reach ollama server」这种正常
排障信息毁掉。所以短于 `MIN_SECRET_LENGTH`（8）的串放过——真实密钥（`sk-` 那一类）
都在 20 字符以上，8 这个门槛全盖住，同时放过一眼是占位符的短串。空串由同一道关挡掉
（`str.replace("", ...)` 会在每两个字符之间插一刀，把整段文本搅碎）。
依赖：无（只用 `collections.abc`）。

### 模块 `qicode.tui`
职责：Textual App，承载选择/对话/流式/错误的全部交互与渲染。
对外接口：`QicodeApp(providers: list[ProviderConfig])`；`App.run()`；`app.conv`（退出回放要用）。
拆四个文件，按「界面怎么摆 / 渲染怎么画 / 流怎么驱动 / 选项怎么映射」切：
`app.py`、`view.py`、`stream.py`、`select.py`。这样渲染规则和流式驱动都能脱离整个
App 单独测（`stream.py` 甚至**不 import textual**）。

**`app.py` —— 装配、状态机、消息处理**
- `compose`：对话区 `VerticalScroll(id="log")`、选择列表 `OptionList(id="select")`、
  输入行（`❯` + `PromptArea`）、状态栏 `Static`。选择列表**先建好靠显隐切换**，
  不动态 mount/remove——`compose` 只在启动时跑一次，动态增删反而更绕。
- 启动（`on_mount`）：先挂启动横幅（`MascotBanner(__version__, os.getcwd())`）；
  单份配置 → `new_provider(providers[0])`、进 `IDLE`；多份 → `SELECTING`（F2、AC1、AC2）。
  选择列表要**显式把 `highlighted` 置 0 并 `focus()`**：Textual 8.x 里它的初值是
  `None`，而 `action_cursor_down` 在无高亮时是「移到第一个可选条目」——用户第一次
  按 ↓ 会觉得没反应，要按第二下才真的往下走。进 `IDLE` 时也必须**显式给输入框焦点**，
  否则默认焦点落在对话区上，一进来敲字敲不进去。
- `state` 用 reactive，`watch_state` 里统一做显隐与状态栏同步；`state` 是 `init=False`
  且单 provider 时值没变（本来就是 IDLE）、watcher 不触发，所以 `on_mount` 末尾
  还要**显式同步一次** `_sync_chrome()`。
- 消息：`OptionList.OptionSelected` → `pick()` 出配置、`new_provider()`、进 `IDLE`；
  `PromptArea.Submitted` → `submit()`。
- `submit(text)`（F9）：**同步**函数，只创建 task 不 await。非 `IDLE` 直接返回且
  **不清输入框**（用户手快在等待时又敲了一行按了 Enter，内容得给他留着）；
  `/exit` → 退出；空输入原样忽略（发出去只会从对端换回一个 400）。正常路径：
  `conv.add_user` → 追加用户块 → 清输入框 → `cur_reply = ""`、`turn_start = monotonic()`
  → 切 `STREAMING` → **先 `_start_reply_block()` + `_refresh_streaming()` 立刻画一帧**，
  再起 `_timer = set_interval(TICK_INTERVAL, _tick)`。那一帧不是多余的：`set_interval`
  的首次触发要等满 0.1 秒，光靠它的话按下 Enter 之后最多 0.1 秒**屏幕上什么都没变**，
  首字来得慢时这段空白会被读成「卡住了」。
- 对话区是一棵**控件树**，不是日志缓冲：`_append(block)` 把定型内容包成
  `Static(block, expand=True)` mount 进去（`expand=True` 是必须的，不然 Static 按内容的
  **自然宽度**渲染，长中文不会折行、直挺挺伸出屏幕）；助手回复走 `_start_reply_block`，
  mount 一块**空的 `ReplyBlock`** 作为对话区的**最后一个子节点**，流式与定型都刷它。
  这是「不跳版」的关键：以前流式文字画在 `RichLog` 下面另一个独立的 `#streaming` 上，
  回复生成时贴着输入框，一定型写进 `RichLog` **整段跳到上面去**（历史不满一屏时
  `RichLog` 从头顶开始排）。现在控件自始至终没挪过窝。
- `_follow_tail` / `_engage_tail_anchor`：内容**真的溢出视口之后**才 `anchor()`。
  不能一上来就锚定——Textual 的锚定是**无条件贴底**的（合成器每次布局算
  `new_scroll_y = 内容底部 - 容器高` 后直接写进去，绕过 `validate_scroll_y` 的 clamp），
  内容比视口矮时这个值是**负数**，整块内容被往下推：启动时 banner 悬在屏幕下半截，
  更要命的是最后一块只要长高一行，上面所有内容都被顶上去一行——流式正文和定型后的
  markdown 折行高度经常不一样，于是就能看见「跳」。`max_scroll_y > 0` 之后贴底本来
  就是想要的（来新内容自动跟到底，用户往上滚时 `_anchor_released` 自动让位，
  滚回底部又恢复）。判断时机有三处：`_follow_tail` 里立刻判一次、`call_after_refresh`
  再补一次（整段回复一口气到达时中间没有别的帧，只有这一下能接住）、`_end_turn`
  再判一次（定型后折行高度可能就在这一刻才第一次顶出视口）。
- `_consume_stream`：把 `provider`、`conv.messages()` 和三个回调交给 `stream.consume`。
- `_finish_with_assistant`（F8、F12、F6）：算总耗时 → 有内容则 `show_reply()` 定型
  + `conv.add_assistant`；**空回复走错误路径、不进历史**——Anthropic 对 content 为空的
  消息直接返回 400，存进去会让**下一轮**莫名其妙地失败，而用户完全看不出这跟上一轮有关。
- `_finish_with_error`（F11、AC11）：`show_error(_safe_message(err))`，不退出。
  `_safe_message` 在这里（不是更晚）过 `redact(str(err), [cfg.api_key for cfg in self.providers])`：
  这句文字会被画进对话区，是肉眼可见的一屏内容。抹的是**所有** provider 的 key，
  不止当前这个——出错时用户多半正要切到另一家去试。
- `_end_turn`：停表、放掉引用、回 IDLE，**不清空回复块**（它已被换成定型内容，
  从此是历史的一部分）。
- 退出（F10、N7）：`Ctrl+C` 走 `BINDINGS` 里的 `Binding("ctrl+c", "quit", priority=True)`。
  **必须自己抢且必须 priority**：Textual 8.x 默认把 ctrl+c 绑成 App 的 `help_quit`、
  Screen 的 `copy_text`、TextArea 的 `copy`——输入框一聚焦它就被「复制」吃掉，AC10
  直接失效。代价是输入框里不能按 Ctrl+C 复制，以 spec 为准。`/exit` 与 Ctrl+C 都走
  `_quit()`：先 `_cancel_stream()` 取消进行中的 task（`async for` 抛 `CancelledError`
  走正常收尾、关掉 HTTP 流），再 `exit()` 让 Textual 还原 raw mode。

**`view.py` —— 纯函数 + 两个 widget，不持有状态**
- 一条贯穿全篇的约定：**所有函数返回 `Text` / `Group` / `Markdown`，不返回裸字符串**。
  模型回复里出现 `[` 太常见了，而任何交给 `markup=True` 的 widget 的字符串都会被当成
  Rich 标记解析——轻则吃字符，重则 `MarkupError` 打崩界面。`Text` 是已解析好的，
  再交给 widget 不会被二次解析。
- `PromptArea(TextArea)`：Textual 8.x 的 `TextArea` 是**纯编辑器**，`_on_key` 里写死了
  `insert_values = {"enter": "\n"}` 并对 enter `stop()` + `prevent_default()`——键盘事件
  当场被吃掉，App 层挂 `("enter", "submit")` 这类 binding **根本收不到**。所以提交语义
  只能在这一层接管：`enter` 发 `Submitted` 消息、不插换行；`NEWLINE_KEYS`
  （`alt+enter` / `shift+enter` / `ctrl+j`）插换行（F9、AC9）。三个键是必要的——
  `alt+enter` 在多数终端被发成 `ESC CR`，Textual 的 `_xterm_parser` 只在键名长度为 1 时
  才补 `alt+` 前缀，`\r` 的键名是五字符的 `enter`，于是 alt 被丢掉、退化成普通 Enter，
  按下去消息直接就发出去了；`shift+enter` 同此，靠扩展键盘协议才有独立键名；
  `ctrl+j` 是 LF（0x0A），raw mode 下原样送出、哪个终端都一样，是那个保底可用的。
- `ReplyBlock(Horizontal)`：左列 `●`、右列正文，两列对齐（F8、AC8）。做成两列是因为
  前两条路都实测撞过墙——Rich 的 `Table.grid` 里**单元格中的 `Markdown` 根本不折行**，
  整段被压成一行加省略号；把 `● ` 拼进 markdown 源码则会被 Rich 的折行
  （`rich/_wrap.py` 的 `divide_line` 按空白分词）搞砸**纯中文长段**：不含空格的一整段
  是一个「词」，词比整行宽时它先换行再硬折，于是已经占着第一行的 `● ` 被晾在那儿。
  两列之后宽度由 Textual 布局算准，圆点和正文互不干扰；左列 `width: auto`，终端把它
  判成 1 列还是 2 列都不影响布局。顺带还保证了流式期间与定型之后圆点是同一个控件、
  位置从没变过。三个方法：`show_streaming` / `show_reply` / `show_error`（错误时圆点
  也转红——错误块是整体一个视觉单元）。
- `MascotBanner(Static)`：`on_mount` 里 `set_interval(FRAME_INTERVAL, _tick)`，
  `_tick` 先进 `_in_view()` 判断、再推进帧号、**图案没变就不 `update()`**（一个周期
  40 帧里只有 4 帧不一样）。可见性用 `region` 与 `container_viewport` 比——
  两者**都是屏幕坐标**（实测：容器下移 4 行，横幅 `region.y` 从 0 变 4；容器再滚动
  30 行，它从 4 变 −26），同一条坐标系里直接比，不用自己减 `scroll_y`；还没上屏时
  两者都是空区域，判成「看不见」正是想要的。
- `streaming_body(reply, elapsed)`：两种形态（没增量 → `⠋ Imagining… (Ns)`；有增量 →
  正文 + 一行次要计时）。转轮帧由**已用秒数**推出，不是每次 +1 的计数器——刷新频率
  以后变了转速也不会乱。`SPINNER_FRAMES` 用盲文点阵，宽度稳定 1 列（同像素画那个取舍）。
- `user_block` / `error_block` / `status_bar`（`Table.grid(expand=True)` 两端对齐，
  比手工算空格可靠）/ `marked_markdown`（**只给退出回放用**：纯 Rich 环境摆不出两列，
  只能拼，遇到块级语法行首时退化成「圆点独占一行」）/ `transcript`（把会话历史拼成
  可直接 print 的块；耗时不重放，事后回看没意义）。
- 依赖：`textual`、`rich`、本项目 `llm`、`prompt`。

**`stream.py` —— 流式消费，不 import textual**
`consume(provider, msgs, *, on_text, on_done, on_error)`：三个回调**保证恰有一个**被调用
（取消除外），调用方因此不必自己兜底状态；异常不会从它逃出去（F11 要求失败不中断会话）。
`CancelledError` 原样抛出。末尾兜一道 `on_error(RuntimeError(...))`——正常结束必有
`done=True` 是两个适配器代码里的承诺，迭代完了却既没 done 也没 err 时兜住，
免得界面永远卡在「流式中」、用户连输入框都拿不回来。

**`select.py`**：`build_options(providers)` → `OptionList` 的选项（名称与模型），
`pick(providers, option_id)` → 选中的 `ProviderConfig`。

依赖（整个包）：`textual`、`rich`、本项目 `llm`、`conversation`、`config`、`prompt`、`redact`。

### 模块 `qicode.cli`（入口）
职责：装配与启动。就三件事——找配置、把配置错误变成人能看懂的话、启动界面；
业务逻辑一概不在这一层。
流程：`load(".qicode/config.yaml")`（相对**当前工作目录**找，不进 home、不看环境变量）
→ `QicodeApp(cfg.providers).run()` → `_replay_transcript(app)`。
**横幅不在这里 print**，而是由界面在 `on_mount` 里挂进对话区——否则它会留在 Textual
接管屏幕**之前**的滚动缓冲里，两种输出混在一起。
失败处理：`ConfigError` 打印一行可读信息到 stderr 并 `SystemExit(1)`（N4、AC1）——
配置错误是**用户能自己修好**的，只给一行、不带 traceback。
`_replay_transcript`：`run()` **之后**把这次会话重放到主屏幕（F10、AC10）。Textual 跑在
备用屏幕上，`run()` 一返回整屏内容连同滚动历史一起没了，用户按完 `/exit` 什么都看不到。
重放的是**对话本身**（用户说的、模型答的），不是把界面内容复制出来——那样会带上只在
交互时有意义的边框和状态栏；耗时不重放。一句没聊就退出（比如只是误开了想看看）时
直接返回，不留一片空白。
依赖：`rich`、本项目 `config`、`tui`。

## 模块交互

### 调用链（启动）
```
main() → load(".qicode/config.yaml")
       → 若 ConfigError：stderr 打印一行可读错误、SystemExit(1)
       → QicodeApp(cfg.providers) → app.run()
         → on_mount：挂 MascotBanner（对话区的第一个子节点，自己按帧重绘）
         → len(providers) == 1：new_provider(cfg[0]) 构造 provider，进 IDLE
         → len(providers)  > 1：进 SELECTING，build_options() 填列表、focus()
       → run() 返回后 _replay_transcript(app)：把对话重放到主屏幕
```

### 时序（多 provider 选择）
```
SELECTING:
  OptionList 显示各 provider 的 name + model，光标预置在第 0 条
  用户方向键移动、Enter 选定
  → pick() 取出该条配置 → new_provider(cfg)
  → 状态栏更新为 provider.name / provider.model
  → 进 IDLE，输入框拿焦点
```

### 时序（一轮对话，核心）
```
IDLE:
  用户在 PromptArea 输入，Enter 提交（alt+enter / shift+enter / ctrl+j 换行）
  → 发 PromptArea.Submitted(value)
  → submit(text)：
      conv.add_user(text)
      _append(user_block(text))                  # mount 一块定型内容
      清空输入框
      turn_start = time.monotonic()；cur_reply = ""
      切 STREAMING
      _start_reply_block()                       # mount 一块**空的** ReplyBlock
      _refresh_streaming()                       # 立刻画一帧，不等定时器
      _stream_task = asyncio.create_task(_consume_stream())
      _timer = self.set_interval(TICK_INTERVAL, _tick)

STREAMING:
  _consume_stream → stream.consume(provider, conv.messages(), 三个回调)
    async for event in provider.stream(msgs):
      - event.text → on_text   → cur_reply += text；刷**同一块**回复块；_follow_tail()
      - event.done → on_done   → show_reply(Markdown(全文), 总耗时)；conv.add_assistant
      - event.err  → on_error  → show_error(redact(str(err), 所有 api_key))；不入历史
  两个回调都收在 _end_turn()：停表、放引用、回 IDLE、再判一次 _follow_tail
  期间输入框不接受提交，但界面照常响应（N1：可滚动回看已完成内容）
```

### 时序（退出）
```
任意状态：输入 "/exit"（IDLE 识别）或 Ctrl+C（priority binding，压过 TextArea 的 copy）
  → _cancel_stream()：task.cancel() 终止进行中的流（CancelledError 原样传播，
    SDK 的 async with 关掉 HTTP 流）
  → App.exit() → Textual 退出备用屏幕、还原 raw mode（N7）
  → run() 返回 → _replay_transcript()：Console().print(transcript(conv.messages()))
    落进终端回滚缓冲，之后能用终端原生方式翻看（F10）
```

### 数据流图
```
config.yaml ──load──> list[ProviderConfig] ──new_provider──> Provider
                                                              │
用户输入 ──> conversation(+user) ──messages()──────────────────┤
                                                              ▼
                                                    Provider.stream(msgs)
                                                              │
                                                    async generator
                                                              ▼
                                       StreamEvent (text / done / err)
                                                              │
                                             stream.consume ──┴──> 三个回调
                                                              │
                        ┌─────────────────────────────────────┼──────────────────────┐
                        ▼                                     ▼                      ▼
                  text  → 刷回复块                   done  → Markdown 定型      err → redact
                  （流式正文 + 计时）                + conversation(+assistant)  → 刷回复块
                        └─────────────────────────────────────┴──────────────────────┘
                                                              │
                                                    对话区（VerticalScroll）
                                                    启动横幅 / 用户块 / 回复块
```

## 文件组织
```
Qicode/
├── pyproject.toml                  — PEP 621 项目元数据、依赖、脚本入口
├── README.md
├── CLAUDE.md                       — 项目协作约定
├── .qicode/
│   ├── config.yaml                 — 运行配置（providers 列表），含真实密钥，已 gitignore
│   └── config.yaml.example         — 提交进仓库的样例
├── docs/
│   ├── README.md                   — 文档索引与各阶段状态
│   └── v1/                         — spec / plan / task / checklist
├── src/
│   └── qicode/
│       ├── __init__.py             — 版本号 `__version__`
│       ├── __main__.py             — 允许 `python -m qicode`
│       ├── cli.py                  — 入口：加载配置、启动 TUI、退出后重放会话
│       ├── config.py               — Config / ProviderConfig / ConfigError、load 与校验
│       ├── prompt.py               — system_prompt、吉祥物像素画、banner 拼装与蹦跳节奏
│       ├── redact.py               — 密钥脱敏（显示前抹掉上游错误里夹带的凭据）
│       ├── conversation.py         — 单会话多轮历史
│       ├── llm/
│       │   ├── __init__.py         — Provider Protocol、Message、StreamEvent、new_provider
│       │   ├── anthropic_provider.py  — anthropic 适配器（封装 AsyncAnthropic）
│       │   └── openai_provider.py     — openai 兼容适配器（封装 AsyncOpenAI）
│       └── tui/
│           ├── __init__.py
│           ├── app.py              — QicodeApp：装配、状态机、消息处理、滚动跟随
│           ├── view.py             — 渲染拼装与自定义控件（PromptArea / ReplyBlock /
│           │                         MascotBanner）、状态栏、退出回放
│           ├── stream.py           — 流式消费（不 import textual，可脱离界面单测）
│           └── select.py           — provider 选择（OptionList 的选项构造与回查）
└── tests/
    ├── conftest.py                 — 共用夹具：按脚本吐事件的假 provider、配置工厂
    ├── test_cli.py                 — 入口与退出回放
    ├── test_config.py              — 配置校验的各类失败路径
    ├── test_conversation.py        — 历史追加与副本
    ├── test_llm_providers.py       — 两适配器的事件翻译（用假 SDK 客户端）
    ├── test_prompt.py              — system prompt、banner 版式、蹦跳的纯函数穷举
    ├── test_redact.py              — 脱敏的边界（短串放过、空串、多次出现）
    ├── test_tui_app.py             — 真界面（`run_test()`）：状态机、不跳版、蹦跳暂停
    ├── test_tui_select.py          — 选项构造与回查
    └── test_tui_stream.py          — consume 的三条回调路径
```
说明：
- 依赖版本预期：`textual`、`rich`、`anthropic`、`openai`、`pyyaml`。在 `pyproject.toml` 中
  以 `dependencies = [...]` 声明。**不引入锁文件**：本项目用 `uv pip install` 安装，
  它不读 `uv.lock`，留一份不会被读取的锁文件只会悄悄过期。
- `tui/` 拆 4 个文件按职责切分：`app` 摆界面与转状态、`view` 画、`stream` 驱动流、
  `select` 做映射。`stream.py` 刻意**不 import textual**——流式是本项目的核心路径，
  切出来它就能用一个假 provider 直接跑单测。
- `.qicode/config.yaml` 含真实密钥，已在 `.gitignore` 忽略；仓库里只留 `config.yaml.example`。
- `pyproject.toml` 里通过 `[project.scripts] qicode = "qicode.cli:main"` 暴露 CLI 入口；
  装好后既可 `qicode` 也可 `python -m qicode`。

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 语言 | Python 3.12+ | 项目既定（qicode python 线）；3.12 的 typing/`asyncio.TaskGroup` 等更舒服 |
| TUI 框架 | Textual | async-first，原生跑在 asyncio 上；CSS 样式、widget 丰富；与流式 SDK 天然契合 |
| markdown 渲染 | Rich 的 `rich.markdown.Markdown` | Textual 内部即用 Rich；代码块语法高亮、列表、强调齐全；宽度自适应（N6） |
| LLM 通信 | 官方 Python SDK（`anthropic` / `openai`） | 用户选定；SDK 内置 SSE 解析与 async 流，省去手写；`AsyncAnthropic` / `AsyncOpenAI` 即可 |
| 协议抽象 | 统一 `Provider` Protocol + 两适配器 | 满足 F3/N3；上层不感知协议；适配器在 `new_provider` 内按需 import，缺哪个 SDK 只在使用它的那条路上报错 |
| 流式接入 TUI | `async for event in provider.stream(...)` 直跑在 Textual 的事件循环里 | Python async-first，无需 channel/Cmd 胶水；界面不阻塞（N1） |
| 流式渲染策略 | 流式纯文本 + done 后 `rich.markdown.Markdown` 定型 | markdown 需完整块；增量渲染会抖动（F8） |
| 对话区模型 | `VerticalScroll` + 控件树（`Static` / `ReplyBlock`），**不用 `RichLog`** | RichLog 的内容不可回改，承载不了「正在流式的那一块」；旧写法把流式文字画在 RichLog 下面另一个独立的 `#streaming` 上，回复生成时贴着输入框、一定型整段跳到上面去。改成对话区里最后一块回复块之后，流式与定型刷的是同一个控件，位置从生到死不变（F8） |
| 回复行首的 `●` | 做成两列（`ReplyBlock(Horizontal)`），不拼进 markdown 源码、不用 `Table.grid` | `Table.grid` 的单元格里 `Markdown` **根本不折行**（压成一行加省略号）；拼进源码则被 Rich 的按空白折行搞砸**纯中文长段**（整段算一个词，先换行再硬折，`● ` 被晾在第一行）。两列由 Textual 布局算准宽度，两边都不犯（F8、AC8） |
| 滚动跟随 | 等 `max_scroll_y > 0` 才 `anchor()` | Textual 的锚定是**无条件贴底**的：合成器每次布局算 `内容底部 − 容器高` 后直接写进 `scroll_y`，绕过 `validate_scroll_y` 的 clamp——不满一屏时这个值是负的，整块内容被往下推；且末尾每长高一行，上面全部内容被顶上去一行（流式正文与定型 markdown 折行高度不同，于是看得见「跳」） |
| 吉祥物动画 | 固定高度的**框**内移位（`图案行数 + BOUNCE_HEADROOM`），由 `Static` 的 `set_interval` 换帧 | 横幅是对话区第一个子节点，高度一变下面全回流。框不动、图案在框里往上挪一格再落回，返回行数因而恒定；右侧文字按**框**高居中，与动画无关。滚出视野（`region` vs `container_viewport`，同为屏幕坐标）就不刷 |
| 像素画用「带背景色的空格」 | 不用实心块 `█` | `█` 的显示宽度在不同终端/字体下在 1 格和 2 格之间摇摆，判成 2 格整幅图就斜掉；**空格恒为 1 列**。转轮选盲文点阵是同一个取舍 |
| thinking | 仅 anthropic 生效（`{"type": "adaptive", "display": "summarized"}`）；openai 忽略 | 固定预算的 `{"type":"enabled","budget_tokens":N}` 在当前 Claude 模型上已被移除、传了直接 400；`display` 缺省是 `omitted`，不显式写就收不到思考增量，F5 的「识别但不渲染」无从谈起。OpenAI 协议没有这个字段、各家网关形状不统一，v1 不猜 |
| 计时 | `turn_start = time.monotonic()` + `set_interval(0.1, ...)` 计算 elapsed | 自请求即计时，Textual 内置 timer 驱动（F12）。转轮帧由**已用秒数**推出而非计数器，刷新频率变了转速也不乱 |
| provider 选择 | 单份直进 / 多份 `OptionList` 选择 | 满足 F2 |
| 历史 | 进程内 `list[Message]`，单会话 | 满足 F6；不持久化 |
| system prompt | 按接入点现拼 `system_prompt(provider_name, model)`，由适配器注入 | 满足 F4/AC4；接入点名与模型名只在本地配置里，模型自己看不到——不告诉它就只能答「我不掌握这个信息」或编一个。conversation 层保持纯 user/assistant |
| 退出后的可读性 | `run()` 之后把对话重放到主屏幕 | Textual 跑在备用屏幕上，没有回滚缓冲，退出瞬间整屏内容连历史一起消失（F10、AC10）。重放的是对话本身，不带边框与状态栏 |
| 密钥脱敏 | 显示前过 `qicode.redact`；短于 8 字符的串放过 | `str(exc)` 是别人写的字符串，可能把凭据抄进错误信息，而它的落点是用户屏幕上的一屏可见内容（N5、AC11）。无差别替换会毁掉 `ollama` 这类占位符带来的排障信息 |
| 配置 | `.qicode/config.yaml` + `pyyaml`；密钥入 `.gitignore` | 用户既定路径；N5 密钥安全 |
| 错误处理 | 运行时错误经 `StreamEvent.err` 显示，不退出 | 满足 F11 |
| 类型/质量 | `typing.Protocol` + `dataclass`；`ruff format` + `ruff check` + `mypy` | 简洁，无运行时依赖（vs pydantic）；ruff 一站式格式化/lint |

### 明确不做的取舍
- **不用 `RichLog`**：理由见上表「对话区模型」。
- **不用锁定文件**：本项目用 `uv pip install` 装依赖，它不读 `uv.lock`。
- **不用 `Table.grid` 排回复**：理由见上表「回复行首的 `●`」。
- **不把 `thinking` 的固定预算写法留着**：见上表「thinking」。
