# Qicode V1（终端对话基座）Plan

> 对应已批准的 spec.md。本文档语言相关（Python）。

## 前置文档调研结论（Context7 查证）

1. **思考内容的传输方式是「协议相关」的，不能写死**：OpenAI 兼容侧，服务端把思考放进流式 delta 的 `reasoning` 字段（服务端以 reasoning parser 之类机制解析产出），开关常以 `chat_template_kwargs.enable_thinking` 这类请求级字段表达；Anthropic 原生侧，思考是独立的 `thinking` content block 流式推出，开关由请求参数 `thinking` 表达。**结论：开关的具体字段由各适配器自己决定，配置文件只负责「开关 + 可选参数覆盖」**（D13）。
2. **两个官方 SDK 都能用**：OpenAI 兼容侧用 `openai` 官方 SDK（协议外字段收进 `model_extra`，`getattr(delta, "reasoning", None)` 可取；发送侧用 `extra_body` 透传）；Anthropic 原生侧用 `anthropic` 官方 SDK 的 `client.messages.stream(...)` 上下文管理器。两者都接受自定义 `base_url`，因此能指向任何兼容实现——OpenAI 兼容侧可指 Ollama / OpenAI 官方，Anthropic 侧可指 Anthropic 官方 Claude / DeepSeek 的 Anthropic 端点。
   ⚠️ **Anthropic 流的事件形状（扒 SDK 源码确认，`lib/streaming/_messages.py` 的 `__stream__` / `build_events`）**：迭代器对同一个内容增量会 fire **两个**事件——先原样吐原生 `content_block_delta`，再吐一个 SDK 合成的语义事件。原生那路的判别字段是**子类型**：正文 `event.delta.type == "text_delta"`（文本在 `event.delta.text`）、思考 `event.delta.type == "thinking_delta"`（文本在 `event.delta.thinking`），另有 `"signature_delta"`。合成那路的类型名不带 `_delta` 后缀：正文 `event.type == "text"` + `event.text`，思考 `event.type == "thinking"` + `event.thinking`，另有 `event.type == "signature"`。
   **两条路任选其一、绝不能都处理**，否则正文与思考都会输出两遍。本项目**取原生 `content_block_delta` 那一路**：它是 SDK 官方文档（`streaming.md`）给出的写法，属线级稳定接口；合成事件是较新的便利层。`thinking_delta` / `text_delta` 是 **delta 的子类型名**，不是顶层事件类型，别写混。
3. **Esc 打断路径**：生成期间终端切 cbreak（termios），后台线程监听键盘，Esc 置位 `threading.Event`，流迭代在 chunk 间隙检测并 break，连接关闭后服务端即中止本次生成。
4. **prompt_toolkit REPL 惯例**：输入态 Ctrl+C 抛 `KeyboardInterrupt`（取消当前行），Ctrl+D 抛 `EOFError`（退出）。采用「连按两次 Ctrl+C 才退出」的 REPL 惯例。
5. **rich Live**：`screen=False` 内联模式、`get_renderable` 回调全量重绘，均为稳定 API；`refresh_per_second` 只是后台刷新线程的节流上限，`Live.refresh()` 本身是立即执行、不受它节流的——「每个增量重绘一次」正是靠这个（N1 的逐字要求）。
6. **Anthropic 请求参数的三条硬约束（对着装好的 SDK 1.7.0 实测 + 官方文档核对，三条都会直接决定功能能不能用）**：
   - **`thinking` 用 `{"type": "adaptive"}`，不能用固定预算**。`{"type": "enabled", "budget_tokens": N}` 这套旧写法在当前 Claude 模型（Fable 5/5.1、Opus 5/4.8/4.7、Sonnet 5）上已被移除，**传了直接返回 400**；只有 Haiku 4.5、Sonnet 4.5 一类旧模型还需要它。旧模型的用户走 `thinking_params` 自行覆盖。
   - **不写 `display` 就看不到思考内容**。当前模型上 `display` 缺省为 `"omitted"`——思考照常发生、照常计费，但流里推出来的 `thinking` 文本是空字符串。AC13 要的「界面上看到思考过程」必须显式写 `display: "summarized"`。
   - **`max_tokens` 是本协议必填**，没有服务端默认值；且 `temperature` 在 SDK 1.7.0 的 `messages.stream()` 签名里已不存在（官方在开启思考时不接受该参数），所以「不要传 temperature」这条不是靠自觉，而是压根没这个入参。
   - 另：本协议的 system 提示走**顶层 `system` 参数**，不是 messages 里的一条消息——与 OpenAI 兼容侧结构性不同，改代码时别按一边的经验改另一边。

## 架构概览

```
┌─────────────────────────────────────────────┐
│  cli（启动编排）                             │
│  解析命令行 → 加载配置 → 选 provider → 进 REPL│
└──────────────┬──────────────────────────────┘
               ▼
┌──────────────────────────┐   ┌──────────────────────────┐
│  tui（交互层）            │◄──┤  session（对话管理）       │
│  输入框/流式渲染/Esc监听   │   │  历史消息/清空/换后端      │
│  /model /clear /exit     │   └──────────────────────────┘
└──────────────┬───────────┘
               ▼
┌──────────────────────────┐
│  provider（模型接入层）    │
│  统一接口 + 两个协议适配器 │
│  openai_compat / anthropic│
│  （官方 SDK + 线程）       │
└──────────────────────────┘
   config（配置）被 cli / provider 使用
```

- **cli**：程序的「总调度」。启动时做三件事——定位并加载 YAML 配置、确定用哪个 provider（`-p` 指定，否则取配置里的第一个）、把控制权交给 TUI。之后它只在退出时收尾。**启动不弹选择菜单**（T17）：换模型在对话里走 `/model`，不拦在门口问。
- **tui**：用户唯一直接接触的层。负责**全部用户输入**（对话输入框与历史、`/model` 选择面板，统一用 prompt_toolkit）、命令分发（`/clear` `/model` `/exit`）、流式渲染（rich Live 逐 token 刷新，思考块灰斜体 + 正文 Markdown）、Esc 打断监听。tui 不直接碰 HTTP，只跟 provider 的统一接口对话。
- **session**：一条极简的对话状态机。存消息列表（role + content）、记录当前 provider、处理「切换后端但保留历史」和「清空历史」。思考文本只用于显示，不进消息列表（满足 F3 的回传规则）。
- **provider**：模型接入层，是整个架构里唯一知道「HTTP 长什么样」的地方。定义统一接口 `ChatProvider`（发一轮对话，返回一个增量流），V1 交付两个实现，正对应原始需求点名的两条链路：`OpenAICompatProvider`（对接 Ollama、OpenAI 官方等 OpenAI 兼容服务）与 `AnthropicProvider`（对接 Anthropic 官方 Claude、DeepSeek 等原生协议服务）。继续加协议 = 新增一个适配器文件 + 工厂加一行，上面三层零改动（G5）。
- **config**：数据加载层。定位配置文件（默认路径 + `-c` 覆盖）、YAML 解析、字段校验、`${ENV_VAR}` 展开、给出「模板引导 / 指向具体行的报错」。产出纯数据（`ProviderConfig`），不含任何行为。

spec 覆盖映射：F1→cli+config、F2/F5→tui+provider、F3→session、F4→session+cli、G5→provider、N2/N3→config+provider+cli 的错误处理约定。

## 核心数据结构与接口

### 异常体系

```python
class ConfigError(Exception):
    """配置加载/校验失败。message 必须指向具体行/字段，供 cli 友好展示（AC3）。"""
    def __init__(self, message: str, line: int | None = None): ...

class ProviderError(Exception):
    """与模型服务通信失败（连接拒绝/超时/HTTP 错误/流中断）。
    message 是人类可读的「原因+建议」，绝不包含 api_key 原文（N2/N3）。"""
```

### ProviderConfig（config 模块产出，纯数据）

```python
@dataclass(frozen=True)
class ProviderConfig:
    name: str        # 供应商标识名，如 "my-qwen"
    protocol: str    # 协议类型："openai_compatible" | "anthropic"
    model: str       # 模型名，如 "qwen2.5vl:7b"（服务端侧注册/约定的模型 ID）
    base_url: str    # 如 "http://127.0.0.1:11434/v1" 或 "https://api.deepseek.com/anthropic"
    api_key: str     # 已完成 ${ENV_VAR} 展开后的值
    thinking: bool = False            # 是否启用扩展思考，缺省 False
    thinking_params: dict | None = None  # 思考开关的协议相关请求参数，缺省由适配器给默认值
```

`thinking_params` 的存在理由：**「怎么表达开启思考」是协议相关的**，写死在适配器里就意味着换服务端要改代码。有了它，同一份代码既能对接 `chat_template_kwargs` 式的请求级扩展字段，也能对接 Anthropic 的 `thinking` 参数，用户在配置里改一行即可（D13）。`frozen=True` 保证不可变——切换 provider 是「换引用」而不是「改字段」；`thinking_params` 用 dict 而非可变默认值，避免冻结下仍可被外部改写。

### ChatMessage（对话历史最小单元）

```python
@dataclass
class ChatMessage:
    role: str      # "user" | "assistant"（system 由 provider 层内部注入）
    content: str   # 纯文本；思考内容永远不存进来（F3）
```

刻意扁平：OpenAI 兼容协议的 `messages` 就是这个形状，`to_api_messages()` 一行转换。思考文本只存在于 tui 显示缓冲，随请求结束丢弃——这就是「思考过程不参与回传」的实现方式。

### StreamDelta（provider 层向上层吐的增量事件）

```python
@dataclass
class StreamDelta:
    kind: str       # "thinking" | "content" | "done"
    text: str = ""  # kind 为 thinking/content 时的增量文本
    error: ProviderError | None = None  # kind 为 done 且出错时携带
```

**整个架构最核心的接口约定**：provider 不管底层协议怎么变，吐给上层的永远是这三种事件。TUI 拿到 `thinking` 刷灰色斜体区，拿到 `content` 刷 Markdown 区，拿到 `done` 收摊。未来加新协议适配器，只要流能翻译成这三种事件，上层零改动（G5 的兑现点）。

### ChatProvider（协议无关的统一接口）

```python
class ChatProvider(ABC):
    """按协议划分的模型接入接口。对话层只依赖这个，不依赖任何具体实现。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """对应 ProviderConfig.name，供 /model 面板显示。"""

    @abstractmethod
    def stream_chat(self, messages: list[ChatMessage], *,
                    cancel_event: threading.Event) -> Iterator[StreamDelta]:
        """发一轮对话，流式产出增量事件。
        - messages：完整历史（不含 system），按序发送
        - cancel_event：tui 的 Esc 监听线程置位后，本方法应在
          下一个增量到达前尽快停止产流并正常结束（kind=done，
          error=None）——「用户主动打断」不算错误
        """
```

单参数、纯同步迭代器，故意不引入 async：prompt_toolkit 的 REPL 是同步世界，为流式单独开 asyncio 循环反而要处理线程/事件循环边界。SDK 同步 `create(stream=True)` 逐 chunk 迭代即可，「首 token 即打印」由 SSE 本身保证。

### OpenAICompatProvider（OpenAI 兼容协议，对接 Ollama 等）

```python
class OpenAICompatProvider(ChatProvider):
    def __init__(self, config: ProviderConfig):
        # 内部持有：
        #   self._config = config
        #   self._client = OpenAI(base_url=config.base_url,
        #                          api_key=config.api_key,
        #                          max_retries=0)   # 关 SDK 重试，失败即时上报

    def stream_chat(self, messages, *, cancel_event) -> Iterator[StreamDelta]:
        """实现要点：
        1. 请求体 = [system 默认提示] + messages
        2. 思考参数的组装规则（两条链路一致，2026-09-22 按真机发现修正）：
             显式写了 config.thinking_params → 无条件用它（不看 thinking 开关）
             只写了 thinking: true          → 用本适配器默认值
                                              {"chat_template_kwargs": {"enable_thinking": True}}
             两样都没有                     → 不发 extra_body，由服务端默认值决定
           「显式写了就无条件生效」是修出来的：开关为假时原本什么都不发，服务端便
           用它自己的默认值，而部分后端默认开启思考，结果用户写的「明确关闭」参数
           被静默丢弃——配置里写了却不起作用，见 D13（`chat_template_kwargs` 式
           扩展字段，换服务端改配置即可，不动代码）
        3. 迭代 chunk：getattr(delta, "reasoning", None) → StreamDelta("thinking")
                       delta.content → StreamDelta("content")
        4. 每个 chunk 处理前检查 cancel_event：置位则 break，
           正常收尾（Esc 打断的落点）
        5. 连接类异常统一翻译成 ProviderError（带「检查服务是否启动/地址
           是否正确」建议），保证 api_key 不出现在 message 里
        """
```

`max_retries=0` 有意为之：交互场景「卡 10 秒重试两次再报错」比「立刻告诉我服务没起」体验差（N2 要求快速、可读的失败）。

### AnthropicProvider（Anthropic 原生协议，对接 Anthropic 官方 Claude / DeepSeek 等）

```python
class AnthropicProvider(ChatProvider):
    def __init__(self, config: ProviderConfig):
        #   self._config = config
        #   self._client = Anthropic(base_url=config.base_url,
        #                            api_key=config.api_key,
        #                            max_retries=0)

    def stream_chat(self, messages, *, cancel_event) -> Iterator[StreamDelta]:
        """实现要点（与 OpenAICompatProvider 同一份契约，上层无感）：
        1. 请求 = model + max_tokens + 顶层 system 提示 + 历史 messages。
           system 走**顶层参数**而非 messages 里的一条（本协议的结构差异）。
           max_tokens 取模块常量 DEFAULT_MAX_TOKENS = 16384（本协议必填、无服务端
           默认值）。取 16384 而非更小的值：开启思考后 thinking 与正文共享这个
           上限，偏小容易让长回答在句子中间被截断（stop_reason=max_tokens）
        2. 思考参数组装规则同 OpenAI 侧：显式写了 config.thinking_params 就无条件
           用它（这样 {type: disabled} 才能真正关闭思考）；否则 thinking: true 时用
           默认 {"type": "adaptive", "display": "summarized"}；两样都没有则不传该
           参数、由服务端默认值决定（Opus 5 / Fable 5 默认是开的）
           （默认值的理由见前置调研第 6 条：固定预算写法已 400，display 缺省为空串）
        3. 用 with self._client.messages.stream(...) as stream: 逐事件迭代，
           **只处理原生 content_block_delta 的增量**（见前置调研第 2 条的 ⚠️）：
             event.type == "content_block_delta" 且
               event.delta.type == "text_delta"     → StreamDelta("content", event.delta.text)
               event.delta.type == "thinking_delta" → StreamDelta("thinking", event.delta.thinking)
             其余一律跳过——尤其 SDK 合成事件是同一份增量的另一份投递，
             处理它会把正文/思考输出两遍
        4. 每个事件处理前检查 cancel_event：置位则退出 with 块，
           连接关闭即中止服务端生成（Esc 打断的落点，契约同左）
        5. 异常翻译同左（anthropic SDK 的 APIConnectionError / APITimeoutError /
           APIStatusError 与 openai 同名同形，翻译策略一致）。注意本协议两阶段
           抛的异常类型相同，翻译函数不必像 OpenAI 侧那样区分 in_stream
        """
```

**工厂的位置**：`create_provider` 放在 `provider/__init__.py`，不放任何一个适配器模块里。它服务的是「所有协议」，落进某个适配器文件会让另一个适配器反向 import 它，「协议平等」就成了摆设。实现是一张 `PROTOCOLS: dict[protocol, 适配器类]` 表 + 未命中抛 `ProviderError`——新增协议就是加一行（D10）。

**两个适配器共享的约定**（写进 `base.py` 的接口文档，避免各写各的）：
- 「用户按 Esc」= 正常 `done`、`error=None`；只有真实通信故障才带 `error`。
- 出错不抛异常，一律以 `StreamDelta("done", error=ProviderError(...))` 收尾——因为错误往往发生在流已经开始之后，此时正文可能已有内容，抛异常会把「已打印的部分」一起丢掉（这条契约的消费方是 repl 的错误出口，见「错误出口」一节）。
- 错误 message 一律经 api_key 子串替换兜底（N3）。

### Session（对话状态）

```python
class Session:
    def __init__(self, providers: list[ProviderConfig]):
        self._providers = providers
        self._current: ProviderConfig      # 当前选中的配置
        self._current_provider: ChatProvider  # 对应的适配器实例
        self._messages: list[ChatMessage]  # 空列表起步

    # -- 历史管理（F3）--
    def add_user(self, text: str) -> None: ...
    def add_assistant(self, text: str) -> None: ...
    def clear_history(self) -> None: ...
    def messages(self) -> list[ChatMessage]: ...  # 返回副本，防外部改

    # -- 后端切换（F4）--
    def switch(self, name: str) -> None:
        """按 name 换 _current 并重建适配器实例；_messages 原样保留。
        找不到 name 时抛 KeyError（tui 负责翻译成人话）。"""

    # -- 发起对话 --
    def current_name(self) -> str: ...
    def all_names(self) -> list[str]: ...
    def chat_stream(self, cancel_event: threading.Event) -> Iterator[StreamDelta]:
        """取 self._current_provider.stream_chat(self._messages())，
        纯转发——session 自己不解析、不渲染任何内容。"""
```

切后端时**重建适配器实例**：不同 provider 的 base_url/api_key 不同，OpenAI client 构造时就把这些定死了，重建是最干净的隔离。

### ConfigLoader（config 模块）

```python
class ConfigLoader:
    DEFAULT_PATH = Path.home() / ".qicode" / "config.yaml"
    TEMPLATE = """..."""   # 展示给用户的引导模板（AC2）

    @classmethod
    def load(cls, path: str | None = None) -> list[ProviderConfig]:
        """定位（path 优先，否则 DEFAULT_PATH）→ 读取 → 解析 → 校验 → 展开。
        任何一步失败抛 ConfigError（带行号/字段名）；
        文件不存在抛 ConfigError（message 携带 TEMPLATE 全文，AC2）。"""

    @staticmethod
    def _expand_api_key(raw: str) -> str:
        """识别 ${VAR} 形式：整串是 ${VAR} 则取 os.environ，
        未设置 → ConfigError("环境变量 VAR 未设置")；
        非 ${} 形式原样返回（明文，N3）。"""
```

### tui 层三个关键对象

```python
class StreamInterrupter:
    """Esc 监听器（生成期间独占键盘）。
    进入时 termios 切 cbreak 模式、关闭回显；
    后台线程逐字符读取，读到 \\x1b（Esc）置位 cancel_event；
    退出时恢复原 termios。macOS/Linux 通用（N4）。
    以 context manager 使用：with StreamInterrupter(ev): ..."""

class StreamRenderer:
    """流式渲染器：持有两块缓冲 _thinking_buf / _content_buf。
    每收到一个 StreamDelta 追加到对应缓冲，然后 live.update()
    重绘「思考块（dim italic 样式）+ 正文（Markdown）」。
    结束时关闭 Live，返回 StreamResult(text=最终正文, error=途中错误)：
    正文交由调用方入库（被 Esc 截断的部分原样保留，AC8），
    错误交由调用方走统一错误出口。"""

@dataclass
class StreamResult:
    """渲染结果：正文 + 途中是否出错。
    为什么错误跟着正文一起返回：provider 契约是「出错不抛异常、
    改用 done 事件携带 error」，错误只能随事件流往上走，
    而渲染器是这条流的第一手消费者，必须把它交给 repl。
    曾把 error 丢掉，结果服务端出错时用户只看到一条空回复、
    连报错都没有（见「错误出口」）。"""
```

class Repl:
    """主循环：prompt_toolkit PromptSession（InMemoryHistory 自带
    上下键历史，AC6）→ 命令分发（/clear /model /exit，未知命令
    提示，AC17）→ 普通消息走 session.chat_stream + StreamInterrupter
    + StreamRenderer 三件套。
    输入态 Ctrl+C：prompt_toolkit 抛 KeyboardInterrupt，第一次清行继续，
    2 秒内第二次退出；Ctrl+D：EOFError 退出（AC16 口径）。"""
```

## 模块设计

### 模块一：config（配置）

**职责：** 把磁盘上的 YAML 变成干净的 `ProviderConfig` 列表；对外只暴露 `ConfigLoader.load()` 和 `ConfigError`。
**对外接口：** `ConfigLoader.load(path: str | None) -> list[ProviderConfig]`、`ConfigError`（含 `line` 属性）。
**依赖：** 无内部模块依赖；外部依赖 `pyyaml`。
**要点：**
- 校验规则：顶层必须是 `providers:` 列表；每项 `name/protocol/model/base_url/api_key` 必填非空，`thinking` 可选布尔，`thinking_params` 可选字典（键值原样透传给适配器）；`protocol` 仅接受 `openai_compatible` 与 `anthropic`（其他值报「暂不支持」并列出支持项）；`name` 不可重复；`base_url` 必须 `http(s)://` 开头。
- `${ENV_VAR}` 展开失败（变量未设置）抛 `ConfigError`，message 写明变量名（AC4）。
- 所有报错携带 YAML 行号：PyYAML 的 `yaml.YAMLError` 自带 `problem_mark.line`，包一层（AC3）。
- 文件不存在时 message 直接带 `TEMPLATE` 全文（AC2 的「模板引导」）。

### 模块二：provider（模型接入）

**职责：** 屏蔽协议细节，向上只产出 `StreamDelta` 事件流。
**对外接口：** `ChatProvider`（ABC）、`create_provider(config) -> ChatProvider`（工厂：按 `protocol` 分发，V1 两条分支，未来加分支只改这里）、`OpenAICompatProvider`、`AnthropicProvider`、`StreamDelta`、`ProviderError`。
**依赖：** config 的 `ProviderConfig`；外部依赖 `openai` 与 `anthropic` 两个官方 SDK（均用同步客户端）。
**要点：**
- 默认 system 提示常量放在本模块（「你是 Qicode，一个运行在终端里的 AI 助手」），对上层不可见——V1 不开放 system 配置，在此收口。两个适配器共用同一个常量，保证换后端时「助手人设」不变。
- 异常翻译：两个 SDK 的异常类同名同形（`APIConnectionError` / `APITimeoutError` / `APIStatusError`），策略一致——连接失败 → 「无法连接 base_url，请确认模型服务已启动、地址是否正确」；超时 → 「请求超时」；HTTP 状态错误 → 带状态码说明；流迭代中途断连 → 「流中断」。message 一律不含 api_key（兜底做一遍 key 子串替换，N3）。注意 `APITimeoutError` 是 `APIConnectionError` 的子类，判断顺序必须先超时后连接。
- 取消语义：每处理一个增量前 `cancel_event.is_set()`，置位则中止迭代并以正常 `done` 收尾（不产 error）——打断是用户意图，不是故障。
- 思考开关的落地：`thinking=False` 不发任何相关参数；`thinking=True` 时取 `config.thinking_params`，为空则用该适配器的默认值（D13）。

### 模块三：session（对话管理）

**职责：** 保管对话历史和当前后端，是「F3 记忆」与「F4 切换」的唯一实现处。
**对外接口：** `Session` 类全部方法。
**依赖：** provider 模块（`create_provider`、`ChatProvider`、`StreamDelta`）。
**要点：**
- `switch()` 内部：`create_provider(新config)` 重建适配器；`_messages` 不动（F4）。
- `chat_stream()` 纯转发迭代器，session 不缓存响应——响应文本由 tui 渲染完成后调 `add_assistant()` 入库，保证「入库的文本 == 用户看到的文本」（被 Esc 截断时也一样，AC8 后半句靠这个）。
- `messages()` 返回浅拷贝：防止 provider 层在请求组装期改写历史。

### 模块四：tui（交互层）

**职责：** 一切用户可见的东西——输入、渲染、命令、打断。
**对外接口：** `run_repl(session: Session, configs: list[ProviderConfig]) -> None`。
**依赖：** session；外部依赖 `prompt_toolkit`、`rich`、`termios/tty`（标准库）。
**要点：**
- **主循环骨架**：
  ```
  while True:
      输入 = prompt("› ")          # KeyboardInterrupt: 第一次→清行继续，
                                   # 记时间戳，2 秒内第二次→退出
      EOFError                      # → 退出
      以 "/" 开头 → 命令分发，否则 → 对话流程
  ```
- **渲染策略**（N1「逐字 + 不闪烁」的实现）：`rich.live.Live(screen=False, refresh_per_second=10)` 内联模式，`get_renderable` 回调实时拼「思考块（`Style(dim=True, italic=True)`）+ 空行 + 正文（`rich.markdown.Markdown`）」。**每个 delta 追加进缓冲后立刻 `live.refresh()` 强制重绘一次**——rich 的 `refresh()` 是立即执行的，不受 `refresh_per_second` 节流，所以字符是一个一个长出来的；`refresh_per_second=10` 只作兜底（万一某段时间没有增量、比如等首 token 或服务端卡住，界面状态仍然是活的）。Live 原地重绘当前区域，不重排历史消息；流结束退出 Live，最终文本落进终端滚动区。
  ⚠️ **逐字刷新的代价**：每个 token 都要把半截 Markdown 重解析一遍并整块重绘。V1 的消息长度下开销可忽略；若将来回复极长或某些终端出现可见闪烁，退路是把「每增量强制刷新」改成节流到 20~30fps，观感上仍是逐字，代价是极端情况下不再是「一个 token 一帧」。
- **Markdown 流式渲染取舍**：生成中是「半截 Markdown」，`rich.markdown.Markdown` 对残缺语法宽容（未闭合代码块持续以代码块样式渲染，闭合后定格）；逐帧重解析整个缓冲，V1 消息长度下性能无压力，保证生成中与生成后观感一致。
- **StreamInterrupter 细节**：保存原 `termios.tcgetattr` → `cbreak` + 关 `ECHO` → 后台线程用 **`select` 带 0.1s 超时**轮询 stdin，每轮先看「停止信号」再看有没有按键，读到 `\x1b` 置位 cancel_event 后退出 → 退出上下文恢复 termios。
  ⚠️ **这里必须用带超时的 select，不能直接阻塞 `os.read`**：阻塞读的线程在本轮生成正常结束后仍卡在 read 上，而且**不会**因为 termios 被恢复就抛错返回；它作为 daemon 线程继续活着，跟 prompt_toolkit 抢同一个 stdin，把用户之后的每一次按键都吃掉——现象是「从第二轮起输入框像能打字、程序却收不到，回车只换来一个空输入」。所以退出时先置停止信号，线程最长一个轮询间隔后收手，再恢复 termios。（已补回归测试，见 checklist）
- **命令分发**：`/clear` → `session.clear_history()` + 一行确认；`/model` → 不带参数弹选择面板、带参数直接切（**原 `/provider` 已并入它**，T17）；`/exit` → 返回；未知命令 → 「未知命令：xxx（支持 /clear /model /exit）」。
- **错误出口统一**：全部集中在 `Repl._report_error()`（红色面板打印 message），**两条通路都归它**——渲染过程中抛出的 `ProviderError`，以及流结束时 `done` 事件携带的 `error`。后者容易被漏掉（provider 契约是「出错不抛异常」，所以 `except ProviderError` 抓不到它），一旦漏掉就是「服务端报错、用户却只看到一条空回复」的静默失败。提示后继续下一轮输入（N2「会话不崩溃」）。
- **对话流程**（主循环的心脏，含错误处理）：
  ```
  session.add_user(text)
  ev = threading.Event()
  with StreamInterrupter(ev):
      result = StreamRenderer(console).run(session.chat_stream(ev))   # 可能抛 ProviderError
  if result.error:  _report_error(result.error)      # 事件通路：流中途失败
  if result.text:   session.add_assistant(result.text)  # 空文本不入库，避免污染上下文
  ```
  注意「空文本不入库」：回复为空通常意味着出错或模型没产出内容，把一条空的 assistant 消息塞进历史会让后续请求带上无意义的空回合。


### 模块四附：界面呈现增强（T16，补记）

**为什么补这一节**：原始需求写的是「仿 Claude Code 的交互式 TUI」，但当时的 F2 只写清了**功能**（能输入、能流式、能打断），**没有任何一条约束外观**——于是交付出去的是一个朴素的 `› ` 行式 REPL：没有 banner、没有状态栏、等首 token 时一片空白。功能不缺，观感差得远，而且真实发生过「用户分不清自己还在选 provider 还是已经进了对话」的混淆。这一节把「外观」补成明确需求。

**要做的四件事，按实施顺序：**

**① 启动 banner**（进入对话前打印一次）

- 内容：程序名 + 版本 + 本次选中的 `provider` / `model` + 当前工作目录
- 目的有两个：一眼看清「我在哪、接了谁、什么模型」；以及**明确标注「已进入对话」**，消掉上面那个混淆
- 终端宽度不足时降级：只印一行纯文本，不印图形字符（宽字符与 CJK 混排容易算错列宽）

**② 首 token 前的生成指示**（体验提升最大的一条）

- 现状：按下回车到首个增量到达之间，Live 区域**是一片空白**——用户无法区分「在算」和「卡住了」
- 改为：进入 Live 立刻显示 `<model> 正在生成… (N s)` 并让 rich 的 spinner 动起来。动画与秒数**靠 `refresh_per_second` 的兜底心跳驱动**，不依赖增量（这正是当初保留兜底帧率的用处）；**首个增量一到就切换**成现有的「思考块 + 正文」渲染，切换要无缝、不闪
- 秒数单调递增——它是「卡住 vs 在算」唯一可区分的信号
- 回复结束后把这一轮的耗时留成一行（参考实现里那句 `✻ Befuddled for 6.4s`，即「想了 6.4 秒」）。这条几乎不花成本，却是「像不像」的重要一块：它把「模型在这轮上花了多久」变成可回看的信息。措辞用中文，与界面其余文案一致

**③ 底部状态栏**（输入态）

- 用 prompt_toolkit 的 `bottom_toolbar`，显示 `<provider> · <model>`
- ⚠️ **状态栏只在输入态可见**，原因要说准（**此处曾写过一条错误结论，已更正**）：生成期间输入框不处于活动状态，prompt_toolkit 的工具栏随之下线。这**不是**因为「钉住底栏必须上全屏模式、会牺牲终端回滚」——**那条结论是错的**。`bottom_toolbar` 写的是正常屏幕，内容照常滚进终端回滚缓冲，「输入区钉在底部」与「往上翻能看历史」可以同时成立，Claude Code 就是这么做的（已实测验证：见 checklist 的 AC22 条目）。
  真正的原因是本项目在「输入态」与「生成态」之间是**两套渲染机制交替**——输入态归 prompt_toolkit，生成态归 rich Live；工具栏只活在输入态，生成态它不存在。要让底栏在生成期间也常驻，需要把生成也纳入同一套渲染机制，那是另一件事（见下表 c 层）。
- 代价要说清楚：从「生成中」回到「输入态」时底栏会出现一次，这是刻意的取舍而非缺陷
- **把「底部栏」拆成三层，避免混为一谈**（2026-09-22 澄清）：
  | 层 | 形态 | 可行性 |
  |----|------|--------|
  | a 输入态底栏 | `provider · model`，在 `›` 下方 | **可做**，即本条 |
  | b 生成期底部信息 | `正在生成… (N s)` + 模型名，跟在流式正文之后 | **可做**，即第 ② 条 |
  | c 生成期「输入框 + 底栏」同时常驻 | 正文还在滚动时，底部固定住输入框与状态栏 | **需付代价**，见下 |
  c 不可免费获得，原因**不是**「技术上做不到」（钉住底部几行不需要接管屏幕，`bottom_toolbar` 已经做到了 a 层），而是本项目在生成期间**根本不在 prompt_toolkit 的输入态**——那段时间属 rich Live，工具栏不存在。
  另外 **c 的前提是「生成期间还能输入」**——参考实现（Claude Code 等）支持排队：生成中打的字先记下、生成完再发。本项目 V1 不支持生成中打字（`Esc` 是**打断**语义，不是排队；生成期间 stdin 归 StreamInterrupter 管）。所以即便强行做出 c，得到的也是一个「看得见却打不进去」的输入框，反而更让人困惑。
  结论：**做 a + b 已能覆盖参考观感**；c 若确实要做，等于新增「生成中输入排队」这项能力，属于独立立项（会动 spec 的 F2 与打断语义）。

**④ 输入框边框**（最后做，工作量最大）

- 目标观感：`❯ 输入消息…` 外带一圈框线（prompt_toolkit 的多行 prompt + `Frame`；`❯` 作为提示符）
- ⚠️ 这一条动的是**输入层**，与 `StreamInterrupter` 的 termios 切换、Ctrl+C 处理在同一片区域。做完必须重跑 AC8（Esc 打断）与 AC16（三条退出路径）——T6 的踩坑说明里那个「监听线程抢 stdin」的问题就在这一片，不能想当然。

**明确不做**：对方演示界面里的 `Connected to 1 MCP server(s), 2 tools registered`——spec「不做 agent 能力：无 tool use」已排除，MCP 属于 V2。**不印这个数字**，因为 V1 印出来只能是假的。

**顺序与理由**：① → ② → ③ → ④。前两个是纯增益、只碰启动打印与渲染层，风险最低；③ 需要先试出 prompt_toolkit `bottom_toolbar` 在「Live 与 prompt 交替」场景下的真实行为；④ 放最后，因为它牵动输入层，需要连带回归 T6/T8 那两处曾经出过缺陷的地方。

**验收**：新增 AC21–AC24（见 spec.md）；**既有 39 项必须全量重跑**，其中最需要盯的是 AC5（spinner 切换不能破坏逐字观感）、AC8（Esc）、AC16（退出路径）、N1（不闪烁、不重排历史）。

### 模块五：cli（启动编排）

**职责：** 进程入口到主循环之间的一切：参数、配置、选 provider、异常兜底。
**对外接口：** `main(argv: list[str] | None = None) -> int`（包成 `qicode` 命令入口）。
**依赖：** config + tui + session。
**要点：**
- argparse 参数：`-c/--config PATH`（覆盖默认配置路径）、`-p/--provider NAME`（可选，直接指定用哪个——不指定就用配置里的第一个）、`--debug`、`--version`。
- 启动序列：`load 配置`（失败 → 打印 ConfigError → 退出码 1）→ 定 provider（`-p` 指定则校验，不存在提示后退出码 1；否则取**配置里的第一个**，不再弹菜单，T17）→ 构造 `Session` 并 `switch` 到它 → **擦屏**（`_clear_screen`）→ 打横幅（`render_banner`）→ `run_repl(...)`。
- **擦屏紧挨着横幅、且在它之前**，两件事是一套的（AC21）：只打横幅不擦屏的话，启动那行的 shell 提示符会留在横幅上方，照 Qicode 那种 `(Qicode) wanzg …/Qicode main ❯ qicode` 的长提示符，一眼看上去像「还在命令行里、没进去」。擦两样：可见区（`ESC[2J`）+ 回滚缓冲（`ESC[3J`），少了后者往上翻一格又能看见它。非终端（重定向、单测的 StringIO）跳过，不写控制序列。
- 全局兜底：外层 catch 意外异常，打印「未预期的错误」+ 提示 `--debug` 看堆栈（N2），未预期错误退出码非 0。
- 退出码定义：正常 0，配置失败 1，未预期错误 2（AC16「退出码干净」的明确口径）。

## 模块交互

**数据流（下行）与事件流（上行）：**

```
用户键盘 ──输入──▶ tui(Repl)
                     │ add_user / chat_stream(ev)
                     ▼
                  session ──转发──▶ provider ──HTTP+SSE──▶ 模型服务
                     ▲                     │      (Ollama / DeepSeek 等)
                     │   add_assistant(最终文本)
                     │                     ▼ StreamDelta 事件流
                  tui(StreamRenderer) ──逐帧重绘──▶ 终端
                        StreamInterrupter(ev) ◀──Esc 置位── 用户键盘
                        （provider 在 chunk 间隙检测到 ev → 正常收尾）
```

**典型时序（一条消息的一生）：**
1. Repl 拿到输入 → `session.add_user`
2. Repl 建 `Event` → 进入 `StreamInterrupter`（终端进 cbreak）
3. `session.chat_stream(ev)` → `provider.stream_chat` 发请求，SDK 逐 chunk 迭代
4. 每个 chunk → `StreamDelta` → Renderer 追加缓冲 → **立即 `live.refresh()` 重绘**（逐字，不按帧率节流）
5. 流自然结束 / Esc 置位被检测到 / 出错 → 最后一个 `StreamDelta(kind="done")`
6. Renderer 关 Live，返回最终文本 → `session.add_assistant`（文本入库）
7. Repl 回到输入态（termios 恢复），循环继续

**provider 切换时序（F4）：** `/model new-name` → `session.switch` → 重建适配器 → 下一条消息即走新链路；`_messages` 未动，历史原样随行。

## 文件组织

```
Qicode/
├── spec.md                        — 需求规格
├── plan.md                        — 技术方案（本文件）
├── task.md / checklist.md         — 后续阶段产物
├── pyproject.toml                 — 项目元信息 + 依赖 + [project.scripts] qicode 入口
├── config.example.yaml            — 示例配置
├── README.md                      — 安装/配置/使用说明（含各协议的配置示例与 thinking 前提）
└── qicode/
    ├── __init__.py                — 版本号
    ├── __main__.py                — python -m qicode 入口（调 cli.main）
    ├── cli.py                     — argparse、启动编排、退出码（启动不问，直接用第一个或 -p）
    ├── config.py                  — ProviderConfig、ConfigError、ConfigLoader
    ├── provider/
    │   ├── __init__.py            — 导出 ChatProvider/create_provider/StreamDelta/ProviderError
    │   ├── base.py                — ChatProvider ABC、StreamDelta、ProviderError
    │   ├── openai_compat.py       — OpenAICompatProvider（对接 Ollama 等）
    │   └── anthropic_compat.py    — AnthropicProvider（对接 Anthropic 原生协议）
    ├── session.py                 — ChatMessage、Session
    └── tui/
        ├── __init__.py            — 导出 run_repl
        ├── repl.py                — Repl 主循环、命令分发、错误出口
        ├── interrupt.py           — StreamInterrupter（termios + 监听线程）
        └── renderer.py            — StreamRenderer（Live + 思考块 + Markdown）
```

包名 `qicode`。测试文件（`tests/`）在 task.md 阶段列出。

## 技术决策

| # | 决策点 | 选择 | 理由 |
|---|--------|------|------|
| D1 | 模型通信方式 | 两条链路各用官方 SDK：`openai`（OpenAI 兼容）、`anthropic`（Anthropic 原生），都不裸写 httpx/SSE 解析 | SSE 解析、HTTP 错误分类、base_url 处理现成；两个 SDK 的异常类同名同形，异常翻译可共用一套策略。OpenAI 兼容侧 `reasoning` 走 `model_extra` 机制（`getattr(delta, "reasoning", None)`）取得；Anthropic 侧思考是独立 content block，由 SDK 合成的 `thinking` 事件给出（事件形状见前置调研第 2 条） |
| D2 | 同步 vs 异步 | 全同步（SDK 同步客户端 + 线程做 Esc 监听） | prompt_toolkit REPL 是同步世界；异步要处理事件循环/线程边界，复杂度不值。流式体验由 SSE 本身保证 |
| D3 | thinking 开关语义 | `thinking` = 「主动请求开启」（不等于关闭，后端默认可能是开的）；`thinking_params` = 「怎么表达」，**显式写了就无条件发出去**；只有它未配置时才由 `thinking` 决定（true → 适配器默认值，false → 不发） | 「怎么表达思考」是协议相关、甚至同一协议下各家实现也不同的（OpenAI 兼容侧用 `chat_template_kwargs`，Anthropic 用 `thinking`），写死在代码里意味着换服务端要改代码。而「显式写了就无条件生效」是 2026-09-22 真机验证时补的修正：原实现只在开关为真时才看 `thinking_params`，导致用户写的「明确关闭」参数被静默丢弃，见 D13/AC14 |
| D4 | thinking 内容来源 | 按协议各取各的：OpenAI 兼容侧读流式 delta 的 `reasoning` 字段（服务端解析产出）；Anthropic 侧读 SDK 合成的 `thinking` 事件 | 思考与正文在协议层就是分开推送的，客户端零解析成本。⚠️ 两条链路都要求后端与模型确实支持思考输出，否则该字段/事件根本不出现（AC13 的前提，写进 README 与配置模板注释） |
| D5 | Esc 打断 | 生成期 termios cbreak + 监听线程（`select` 带超时）+ `threading.Event`，流迭代在增量间隙检测并中止 | prompt_toolkit 非输入态收不到按键，须直接读终端 fd；cbreak 在 macOS/Linux 通用（N4）。中止后关闭连接，服务端即停止生成。**监听线程必须在退出时可靠终止**，否则会持续抢占 stdin（见模块四该条目的踩坑说明） |
| D6 | SDK 重试 | `max_retries=0` | 交互场景失败要「快而可读」，静默重试体验差（N2） |
| D7 | 流式渲染 | `rich.live.Live(screen=False, refresh_per_second=10)` + `get_renderable` 全量重绘，并且**每收到一个增量就 `live.refresh()` 强制重绘** | 内联不占全屏，历史留滚动区（F2）；真正驱动刷新的是「每个增量」而不是帧率——rich 的 `refresh()` 立即执行、不受 `refresh_per_second` 节流，这才叫逐字（N1），帧率只作兜底；全量重解析 Markdown 在 V1 长度下无性能问题 |
| D8 | 输入历史 | prompt_toolkit `PromptSession` 默认 `InMemoryHistory` | 零配置自带上下键翻阅（AC6）；会话内即可（不做跨会话持久化） |
| D9 | 配置解析 | `pyyaml` + 手工字段校验（不引入 pydantic） | 字段结构小（6 个基本字段 + 2 个可选），手工校验直白且报错可带行号；pydantic 属额外重量 |
| D10 | Provider 扩展机制 | `protocol` 字段 + `create_provider()` 工厂分发 | 新协议 = 新适配器文件 + 工厂一行，上层零改动（G5）；比注册表/插件化简单，符合 V1 体量 |
| D11 | 思考内容不入库 | 思考文本只存 tui 显示缓冲，`ChatMessage` 只有 role/content | OpenAI 兼容回传不认 `reasoning` 角色，入库可能污染请求；F3 要求「不参与回传」，不存即最强保证 |
| D12 | 依赖清单 | `openai`、`anthropic`、`pyyaml`、`prompt_toolkit`、`rich`（httpx 由两个 SDK 自带） | 最小依赖集，全部成熟主流库。两个协议 SDK 均属核心功能所需，不做可选依赖——省得用户为了换后端还要改安装方式 |
| D13 | 思考参数可配置 | 可选字段 `thinking_params`，键值原样透传给适配器；**显式配置即生效，与 `thinking` 开关无关** | 保证「不配置也能用」（大多数用户只关心开关），同时保证「配置了一定算数」——用户不该遇到「我明明写了却没起作用、还没人告诉我」。后者是 2026-09-22 真机验证补的修正（T14） |
| D14 | Anthropic 流式实现 | 用 SDK 的 `client.messages.stream(...)` 上下文管理器逐事件迭代（而非 `create(stream=True)` 手动拼事件），且只处理原生 `content_block_delta` 里的 `text_delta` / `thinking_delta` | 上下文管理器保证异常路径下连接被关闭（Esc 打断或异常时不会让服务端继续空跑）。选原生那一路而非 SDK 合成事件，是因为它是官方文档给出的写法、属线级稳定接口；迭代器对同一增量会同时投递两路（见前置调研第 2 条），**两种都处理会输出两遍** |
| D15 | 界面呈现 | 补「启动 banner + 首 token 前生成指示 + 输入态底部状态栏 + 输入框边框」四件；**不追求生成期常驻底栏** | 原始需求是「仿 Claude Code 的 TUI」，而 F2 当初只约束了功能、没约束外观，交付成了朴素行式 REPL（T16 的由来）。常驻底栏需要全屏模式，会牺牲终端回滚历史，与 F2「历史可向上翻阅」冲突——所以拿生成指示承担生成期的信息，底栏只在输入态出现 |
| D16 | 模型选择时机 | **进去之后再选**（`/model` 交互面板），启动直接进对话、默认用配置里第一个 provider | 拦在门口问多一步，而且菜单内容会一直留在对话记录最上面；进去后随时能换才顺手（Claude Code 同此）。**两种用法都留在 `/model` 上**：不带参数弹面板浏览着选，带参数（`/model <name>`）直接按名切——供脚本化与习惯打命令时用 |
| D15 | 错误通路 | 出错不抛异常，一律以 `StreamDelta("done", error=ProviderError)` 收尾；错误由渲染层随 `StreamResult` 交给 repl 的统一出口；正文为空则不入历史 | 错误常发生在流已开始之后（此时正文可能已有内容），抛异常会把「已打印的部分」一起丢掉，与 AC8「所见即所得」冲突。代价是错误有两条通路（异常 + 事件），必须在 repl 显式都接上——漏接会变成静默空回复，已补回归测试 |

**Python 版本**：≥ 3.10（`X | None` 语法、`dataclasses`；各依赖均兼容），写入 `pyproject.toml` 的 `requires-python` 与 README（N4）。

**服务端实测记录**

当前后端：**本地 Ollama**（`http://127.0.0.1:11434/v1`，模型 `qwen2.5vl:7b`），2026-09-22 实测：
- `/v1/models` 与 `/v1/chat/completions` 均正常，OpenAI 兼容协议可用；`base_url: http://127.0.0.1:11434/v1` 被 `OpenAI` SDK 直接接受，无需特殊处理。
- ⚠️ `qwen2.5vl:7b` 是视觉模型、**不带思考能力**，因此本机上 AC13/AC14 无法复验——这是环境限制，不是缺陷（spec 的 AC13 已注明前提）。
- 该模型首次调用有数秒加载耗时，之后很快；E2E 的等待循环必须按「提示符回到输入态」判断，不能按固定秒数猜。

历史记录（**该 vLLM 端点已于 2026-09-22 废弃、不再使用**，结论仅作知识留存）——对 `http://10.21.1.45:22845/v1`（模型 ID `agent-brain`，即 Qwen3.8-27B-FP8）实测：
- `chat_template_kwargs: {"enable_thinking": true}` → 流中先推 `delta.reasoning` 块（思考），再推 `delta.content` 块（正文），`finish_reason: stop` 收尾。D4 的「思考与正文在协议层分流」假设成立，无需客户端解析思考文本。
- `chat_template_kwargs: {"enable_thinking": false}` → 流中 `reasoning` 字段出现 0 次，纯 content。AC14 的「无思考块」由协议层保证。
- ⚠️ 实测发现（**与具体后端无关的通用边界情况，代码中保留此处理**）：思考结束后的**首个 content chunk 带前导空行（`\n\n`）**。StreamRenderer 追加正文前须 `lstrip("\n")` 处理首个 content 增量，避免回复开头出现空隙。

**遗留风险提示**（非 V1 阻塞项）：
- **原始需求点名的两条链路，真机只跑通了 OpenAI 兼容这一条**：本地 Ollama 可用；Anthropic 侧（官方 Claude / DeepSeek）居居手上当前没有 API key，因此**只由单测覆盖**（mock 事件流、请求体与错误翻译），**官方 Claude 未实测**。spec 的 AC18 已如实写明，拿到 key 后补做真机端到端。
- **thinking 链路（AC13/AC14）在本机没有可复验的后端**：Ollama 的 `qwen2.5vl:7b` 不支持思考，原先验证过的 vLLM 端点已废弃，且无 Anthropic key。代码与配置化参数都已就位，等接入支持思考的模型后重新验收。
- 服务不可达（连接拒绝）时的错误提示是 AC12 的一部分，由 `ProviderError` 的「检查服务是否启动/地址」建议覆盖。
