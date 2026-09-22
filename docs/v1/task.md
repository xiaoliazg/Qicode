# 多协议 LLM 终端对话客户端 Tasks

> 包名：`qicode`（Python 3.12+）。源码位于 `src/qicode/`，内部模块以 `qicode.xxx` 导入。
>
> **T1–T14 是动工前的任务拆解，T15 起是实现过程中追加的。** 凡是当初的写法与最终实现
> 不一致的地方，本文已经就地改成**实际做出来的样子**，并写上为什么——留着过时的写法
> 只会让下一个人照着抄错。逐条对照的证据在 `docs/v1/checklist.md`。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `pyproject.toml` | PEP 621 项目元数据、依赖、脚本入口 |
| 新建 | `.qicode/config.yaml.example` | 配置模板 |
| 修改 | `.gitignore` | 忽略 `.qicode/config.yaml` |
| 新建 | `README.md` | 项目说明 |
| 新建 | `docs/README.md` | 文档索引与各阶段状态 |
| 新建 | `docs/v1/` | spec / plan / task / checklist |
| 新建 | `src/qicode/__init__.py` | 包标识、版本号 `__version__` |
| 新建 | `src/qicode/__main__.py` | `python -m qicode` 入口（转调 `cli.main`） |
| 新建 | `src/qicode/config.py` | `Config` / `ProviderConfig` / `ConfigError`、`load`、校验 |
| 新建 | `src/qicode/prompt.py` | `system_prompt`、`MASCOT_ART` / `MASCOT_BANNER`、`render_banner`、蹦跳节奏 |
| 新建 | `src/qicode/redact.py` | 密钥脱敏（T16） |
| 新建 | `src/qicode/conversation.py` | 单会话多轮历史 |
| 新建 | `src/qicode/llm/__init__.py` | `Provider` Protocol、`Message`、`StreamEvent`、`new_provider` 工厂 |
| 新建 | `src/qicode/llm/anthropic_provider.py` | anthropic 适配器 |
| 新建 | `src/qicode/llm/openai_provider.py` | openai 兼容适配器 |
| 新建 | `src/qicode/tui/__init__.py` | TUI 包标识 |
| 新建 | `src/qicode/tui/app.py` | `QicodeApp`、状态机、消息处理、滚动跟随 |
| 新建 | `src/qicode/tui/view.py` | 渲染拼装与自定义控件（`PromptArea` / `ReplyBlock` / `MascotBanner`）、状态栏、退出回放 |
| 新建 | `src/qicode/tui/stream.py` | `consume`（流式消费，不 import textual） |
| 新建 | `src/qicode/tui/select.py` | provider 选择（`OptionList` 选项构造与回查） |
| 新建 | `src/qicode/cli.py` | 入口装配、退出后重放会话 |
| 新建 | `tests/conftest.py` | 共用夹具：假 provider、配置工厂 |
| 新建 | `tests/test_config.py` | config 单测 |
| 新建 | `tests/test_conversation.py` | conversation 单测 |
| 新建 | `tests/test_cli.py` | 入口与退出回放 |
| 新建 | `tests/test_llm_providers.py` | 两适配器的事件翻译（T14） |
| 新建 | `tests/test_prompt.py` | prompt 层的纯函数穷举（T19） |
| 新建 | `tests/test_redact.py` | 脱敏边界（T16） |
| 新建 | `tests/test_tui_app.py` | 真界面测试（`run_test()`） |
| 新建 | `tests/test_tui_select.py` | 选项构造与回查 |
| 新建 | `tests/test_tui_stream.py` | `consume` 的三条回调路径 |

---

## T1: 初始化 Python 项目骨架与依赖
**文件：** `pyproject.toml`、`src/qicode/__init__.py`、`src/qicode/__main__.py`、`src/qicode/cli.py`（临时占位）
**依赖：** 无
**步骤：**
1. 用 `uv init` 或手写 `pyproject.toml`，关键字段：
   ```toml
   [project]
   name = "qicode"
   version = "0.1.0"
   requires-python = ">=3.12"
   dependencies = [
     "textual>=0.80",
     "rich>=13",
     "anthropic>=0.40",
     "openai>=1.50",
     "pyyaml>=6",
   ]

   [project.scripts]
   qicode = "qicode.cli:main"

   [build-system]
   requires = ["hatchling"]
   build-backend = "hatchling.build"

   [tool.hatch.build.targets.wheel]
   packages = ["src/qicode"]

   [dependency-groups]
   dev = ["pytest>=8", "ruff>=0.6", "mypy>=1.10"]
   ```
2. `src/qicode/__init__.py`：定义 `__version__ = "0.1.0"`。
3. `src/qicode/__main__.py`：`from .cli import main; main()`。
4. `src/qicode/cli.py` 写一个临时 `main()`，打印 `f"qicode {__version__}"` 并退出，确保可启动。
5. 安装依赖：在已激活的 conda 环境 `Qicode` 里跑 `uv pip install -e . --group dev`。
   **不要用 `uv sync`**——它会另建一个 `.venv`，跟本项目「用 conda 环境开发」的约定冲突。
   也可以 `uv pip install --python <conda 里的 python> -e . --group dev`，不必先激活。

**验证：** `python -m qicode` 能打印版本号；`qicode` 命令同样可用；`uv pip list` / `pip list` 能看到上述依赖。

## T2: config 模块
**文件：** `src/qicode/config.py`、`tests/test_config.py`
**依赖：** T1
**步骤：**
1. 定义 `@dataclass class ProviderConfig` 字段：`name`、`protocol`、`api_key`、`model`、`base_url: str | None = None`、`thinking: bool = False`；以及 `@dataclass class Config(providers: list[ProviderConfig])`。
2. 定义 `class ConfigError(Exception)`。
3. 实现 `load(path: str) -> Config`：用 `pathlib.Path(path).read_text()` + `yaml.safe_load` 解析；
   再调 `_from_dict(...)` 把 dict 映射到 dataclass（手动映射保留校验时机）。
4. 校验：`providers` 非空；逐项 `name` / `protocol` / `api_key` / `model` 非空；
   `protocol ∈ {"anthropic", "openai"}`。失败抛 `ConfigError`，message 形如
   `providers[1].api_key 不能为空`。
5. 文件不存在 → `ConfigError(f"配置文件不存在: {path}")`；YAML 解析失败 → 转换为 `ConfigError(...)`。
6. 写 `tests/test_config.py`：合法配置返回正确条数；缺字段 / 非法 protocol / 文件缺失分别抛 `ConfigError`。

**验证：** `pytest tests/test_config.py` 通过；`ruff check src/qicode/config.py` 无告警。

## T3: 配置模板与忽略
**文件：** `.qicode/config.yaml.example`、`.gitignore`
**依赖：** T2
**步骤：**
1. 写 `.qicode/config.yaml.example`：含 anthropic 条目（含 `thinking: true`）与一段注释掉的 openai 条目示例，字段与 `ProviderConfig` 对齐。
2. `.gitignore` 追加 `.qicode/config.yaml`。

**验证：** 复制 example 为 `.qicode/config.yaml` 后 `config.load(...)` 通过；`git status` 确认 `.qicode/config.yaml` 被忽略。

## T4: prompt 模块
**文件：** `src/qicode/prompt.py`
**依赖：** T1
**步骤：**
1. 定义 `MASCOT_ART`：字符画，每个格子一个字母（`H` 头发 / `F` 肤色 / `E` 眼睛 / `B` 腮红，
   `.` 留白），约 12 列 × 6 行；`MASCOT_COLORS` 给每个字母配一个颜色。
   `_render_pixel_art` 把字符画翻成**带背景色的空格**逐格拼出的 markup——
   空格宽度恒为 1 列，任何终端、任何字体都不会错位（实心块字符 `█` 的显示宽度
   在部分终端会在 1/2 格之间摇摆，判成 2 格整幅图就斜掉）。认不出的字符一律留白，
   图案里可以用任意符号做占位。结果存进 `MASCOT_BANNER`。
2. 实现 `def render_banner(version: str, cwd: str) -> str`：拼出「吉祥物 + Qicode vX +
   cwd + 就绪提示行」，右侧文字与图案垂直居中。
3. （T18 追加）`BANNER_GAP` 拉开图案与右列的间距；图案上下各留一行空白。
4. （T19 追加）加 `art_offset` 参数与蹦跳常量，图案外套固定高度的框。

**验证：** `python -c "from qicode.prompt import render_banner; print(render_banner('0.1.0', '/tmp'))"`
输出含三要素与提示行；T19 起由 `tests/test_prompt.py` 穷举覆盖。

> **实现时改了一处：** system prompt 最初设计成模块级常量 `SYSTEM_PROMPT`，
> 后来改成按接入点现拼的 `system_prompt(provider_name, model)` —— 见 T17。

## T5: llm 包骨架
**文件：** `src/qicode/llm/__init__.py`
**依赖：** T2
**步骤：**
1. 定义 `@dataclass class Message(role: Literal["user","assistant"], content: str)`、
   `@dataclass class StreamEvent(text: str = "", done: bool = False, err: Exception | None = None)`。
2. 定义 `class Provider(Protocol)`：`name` / `model`（property）；
   `def stream(self, msgs: list[Message]) -> AsyncIterator[StreamEvent]: ...`。
3. 实现 `def new_provider(cfg: ProviderConfig) -> Provider`：按 `cfg.protocol` 分派
   `AnthropicProvider` / `OpenAIProvider`；未知协议抛 `ValueError`。
   适配器用**函数内 import**（不写在文件顶部）：新增协议时不必动这个文件的头部，
   也不会因为某一个适配器出问题就整个 `qicode.llm` 都 import 不了。
   （适配器在 T7/T8 实现，那之前分派会 import 失败——T5 阶段只验骨架可 import。）

**验证：** `python -c "from qicode.llm import Provider, Message, StreamEvent, new_provider"` 不报错。

## T6: conversation 模块
**文件：** `src/qicode/conversation.py`、`tests/test_conversation.py`
**依赖：** T5
**步骤：**
1. 定义 `class Conversation`，内部 `self._messages: list[Message] = []`。
2. 实现 `add_user(text)`、`add_assistant(text)`、`messages() -> list[Message]`（返回 `list(self._messages)` 副本）。
3. 单测：连续 `add_user` / `add_assistant` 后 `messages()` 顺序与 role 正确。

**验证：** `pytest tests/test_conversation.py` 通过。

## T7: anthropic 适配器
**文件：** `src/qicode/llm/anthropic_provider.py`
**依赖：** T5、T4
**步骤：**
1. `class AnthropicProvider`：`__init__(self, cfg)` 中 `self._client = anthropic.AsyncAnthropic(api_key=cfg.api_key, base_url=cfg.base_url or None)`；保存 `cfg.model` / `cfg.name` / `cfg.thinking`。
2. `name` / `model` property 返回 `cfg.name` / `cfg.model`。
3. `async def stream(self, msgs) -> AsyncIterator[StreamEvent]`：
    - 把 `msgs` 转 `[{"role": m.role, "content": m.content} for m in msgs]`。
    - `params = {"model": self._model, "max_tokens": MAX_TOKENS, "system": system_prompt(self._name, self._model), "messages": [...]}`。
      `system` 是**现拼**而不是常量——里面要带上本次的接入点名与模型名（见 T17）。
    - 若 `self._thinking`，加 `thinking={"type": "adaptive", "display": "summarized"}`。
      **不要写固定预算的 `{"type": "enabled", "budget_tokens": N}`**——那在当前 Claude
      模型（Fable 5/5.1、Opus 5/4.8/4.7、Sonnet 5）上已被移除，传了直接 400。
      `display` 也必须显式写：缺省是 `omitted`，服务端就不发思考内容了。
    - `try: async with self._client.messages.stream(**params) as stream: async for event in stream:`
      根据 `event.type` 判断：`content_block_delta` 且 `event.delta.type == "text_delta"` →
      `yield StreamEvent(text=event.delta.text)`；`thinking_delta` 跳过；其他事件忽略。
    - `else` 分支正常结束 → `yield StreamEvent(done=True)`。
    - `except asyncio.CancelledError: raise`；其他 `except Exception as e: yield StreamEvent(err=e)`。

**验证：** `python -c "from qicode.llm.anthropic_provider import AnthropicProvider"` 不报错；联调留到 T14；可写小脚本用假 key 触发错误，确认拿到 `err` 事件。

## T8: openai 适配器
**文件：** `src/qicode/llm/openai_provider.py`
**依赖：** T5、T4
**步骤：**
1. `class OpenAIProvider`：`__init__` 中 `self._client = openai.AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url or None)`；保存 `cfg.model` / `cfg.name`（`thinking` 忽略）。
2. `name` / `model` property 同上。
3. `async def stream(self, msgs) -> AsyncIterator[StreamEvent]`：
    - 组装 `messages = [{"role": "system", "content": system_prompt(self._name, self._model)}] + [...]`。
      这条协议没有顶层 system 参数，system prompt 就是 messages 的第一条（见 T17）。
    - 转换历史消息**按 role 分支、两次字面量构造**（`_to_sdk_message`），不能图省事写
      `{"role": msg.role, ...}`：SDK 的消息类型是若干 TypedDict 组成的联合，每个成员各自
      要求 `role` 是它自己的那个字面量常量，用变量填就一个成员也对不上，mypy 连后面
      迭代流的那行也会跟着报错。
    - `try: stream = await self._client.chat.completions.create(model=self._model, messages=messages, stream=True)`。
    - `async with stream:` 再 `async for chunk in stream:`。
      **这层 `async with` 不是装饰**：`AsyncStream.close()` 只在**流被读完**时才自动调用，
      用户取消或中途报错跳出时流没读完、没人关它，HTTP 连接会一直挂着到 GC。
    - `chunk.choices` 为空时 `continue`（有些兼容实现会发只带用量统计的收尾块，
      直接取 `[0]` 会在这些后端上抛 `IndexError`，白白把一轮对话判成失败）；
      否则 `delta = chunk.choices[0].delta.content`，非空时 `yield StreamEvent(text=delta)`。
    - 结束后 `yield StreamEvent(done=True)`。
    - `except asyncio.CancelledError: raise`；其他 `except Exception as e: yield StreamEvent(err=e)`。

**验证：** import 不报错；同 T7 的错误路径手测。

## T9: TUI App 骨架
**文件：** `src/qicode/tui/app.py`、`src/qicode/tui/__init__.py`
**依赖：** T1、T2、T5、T6
**步骤：**
1. 定义 `class SessionState(Enum)`：`SELECTING` / `IDLE` / `STREAMING`。
2. 定义 `class QicodeApp(App)`：构造参数 `providers: list[ProviderConfig]`（空列表直接抛
   `ValueError`——公开类，手工构造也能走到这）；初始化 `state`、`provider: Provider | None`、
   `conv = Conversation()`、`cur_reply = ""`、`turn_start = 0.0`、`_streaming: ReplyBlock | None`、
   `_stream_task = None`、`_timer = None`。
3. `compose() -> ComposeResult`：yield `VerticalScroll`（id="log"，对话区）、
   `OptionList`（id="select"，先建好靠显隐切换，不动态 mount/remove）、
   `Horizontal`（id="input-row"：`Static("❯")` + `PromptArea`）、
   `Static`（id="statusbar"）。
   **不用 `RichLog`，也没有独立的 `#streaming` 动态区**——理由见 T15。
4. `on_mount(self)`：挂 `MascotBanner(__version__, os.getcwd())` 到对话区（T19）；
   若 `len(self.providers) == 1`：`self.provider = new_provider(self.providers[0])`、
   `self.state = IDLE`；否则切 `SELECTING`（T11 接入 `OptionList`，要把 `highlighted`
   显式置 0 并 `focus()`）。
   进 `IDLE` 时必须**显式给输入框焦点**，否则默认焦点落在对话区上，一进来敲字敲不进去。
   `state` 是 `init=False` 且单 provider 时值没变、watcher 不触发，末尾要显式同步一次界面。
5. `BINDINGS = [Binding("ctrl+c", "quit", "退出", priority=True, show=False)]`。
   **必须 priority**：Textual 8.x 默认把 ctrl+c 绑成 App 的 `help_quit`、Screen 的
   `copy_text`、TextArea 的 `copy`——输入框一聚焦它就被「复制」吃掉，AC10 直接失效。
   `action_quit`：若 `_stream_task` 存在则 `cancel()`，`self.exit()`。

**验证：** `python -m qicode`（搭配最小合法配置）能进入界面，看到 banner + 空对话区 + 输入框 + 状态栏；`ruff check src/qicode/tui/app.py` 无告警。

## T10: TUI 流式接入与计时
**文件：** `src/qicode/tui/stream.py`、`src/qicode/tui/app.py`
**依赖：** T9、T5
**步骤：**
1. 在 `app.py` 给 `QicodeApp` 添加 **同步**的 `def submit(self, text: str) -> None`：
    - 非 `IDLE` 直接返回，且**不清输入框**（用户手快在等待时又敲了一行按了 Enter，
      内容得给他留着）。
    - `text.strip() == "/exit"` → `self._quit()`；空输入原样忽略（发出去只会换回一个 400）。
    - 否则：`self.conv.add_user(text)`；`self._append(user_block(text))`；清空 `PromptArea`；
      `self.cur_reply = ""`；`self.turn_start = time.monotonic()`；`self.state = STREAMING`；
      **先 `self._start_reply_block()` + `self._refresh_streaming()` 立刻画一帧**，
      再 `self._stream_task = asyncio.create_task(self._consume_stream())`、
      `self._timer = self.set_interval(TICK_INTERVAL, self._tick)`。
      那一帧不是多余的：`set_interval` 首次触发要等满 0.1 秒，光靠它的话按下 Enter 之后
      最多 0.1 秒**屏幕上什么都没变**，首字来得慢时这段空白会被读成「卡住了」。
      `_append` 里**不 `await`** `mount()`——`Widget.mount()` 当场就把控件注册进父节点了，
      返回的 `AwaitMount` 只是用来等布局完成的。
2. 在 `stream.py` 实现 `async def consume(provider, msgs, *, on_text, on_done, on_error)`：
   把三个回调交给它，界面层不自己写 `async for`。
   ```python
   try:
       async for event in provider.stream(msgs):
           if event.err is not None:
               on_error(event.err); return
           if event.text:
               on_text(event.text)
           if event.done:
               on_done(); return
   except asyncio.CancelledError:
       raise
   except Exception as exc:
       on_error(exc); return
   on_error(RuntimeError("流式响应意外结束：没收到结束信号，也没收到错误"))
   ```
   `app._consume_stream` 只是 `await consume(self.provider, self.conv.messages(), ...)`。
   这一层**不 import textual**，可以脱离终端、脱离 App 直接单测。
3. `_tick`：仅 `STREAMING` 时 `_refresh_streaming()`——刷**当前那块回复块**的正文与计时。
4. `_finish_with_assistant`：
    - `block.show_reply(reply, elapsed)`（内部是 `Group(Markdown(reply), 耗时)`）刷新**同一块**；
    - `self.conv.add_assistant(reply)`；
    - **空回复走错误路径、不进历史**：Anthropic 对 content 为空的消息直接返回 400，
      存进去会让**下一轮**莫名其妙地失败，而用户完全看不出这跟上一轮有关。
    - `_end_turn()`：停表、放掉 `_streaming` 引用、回 IDLE（**不清空回复块**，
      它已经是历史的一部分）、再判一次 `_follow_tail()`。
5. `_finish_with_error`：`block.show_error(self._safe_message(err))`，同上回 IDLE，不退出。
   `_safe_message` 在这里过 `redact`（T16）。
6. 提交语义**不在 App 层挂 binding**：Textual 8.x 的 `TextArea._on_key` 把 enter 写死成
   插换行并且 `stop()` + `prevent_default()`，App 层挂 `("enter", "submit")` **根本收不到**。
   改在 `PromptArea`（T12）自己接管。

**验证：** 配真实 key 后跑通一轮：能看到 "Imagining… (Ns)" 计时；流式逐字；done 后看到 markdown 在同一块回复块里定型。

## T11: TUI provider 选择
**文件：** `src/qicode/tui/select.py`
**依赖：** T9、T2、T5
**步骤：**
1. `OptionList` 在 `compose` 里就 yield 出来（id="select"），靠**显隐**切换，不动态
   mount/remove——`compose` 只在启动时跑一次，动态增删反而更绕。
2. `build_options(providers)` 造出 `f"{p.name} ({p.model})"` 的选项；
   `pick(providers, option_id)` 按选项 id 回查 `ProviderConfig`。
3. 监听 `on_option_list_option_selected`：`self.provider = new_provider(pick(...))` →
   切 `IDLE` → 给输入框焦点。状态栏由 `watch_state` → `_sync_chrome()` 统一刷新。
4. `state == SELECTING` 时把 `#log` / `#input-row` / `#statusbar` 全部 `display = False`，
   只留列表；切回 `IDLE` 时反过来。
5. 进入 `SELECTING` 时把 `highlighted` 显式置 0：Textual 8.x 里它的初值是 `None`，
   而 `action_cursor_down` 在无高亮时是「移到第一个可选条目」——用户第一次按 ↓ 会觉得
   没反应，要按第二下才真的往下走。顺手也让 Enter 直接可用。

**验证：** 用 2 条 provider 配置启动应出现选择列表（在 T14 端到端验证）。

## T12: TUI View 拼装与渲染
**文件：** `src/qicode/tui/view.py`
**依赖：** T9、T4、T10
**步骤：**
1. 这一层是**纯函数 + 两个 widget**，不持有任何状态：界面长什么样在这里定死，
   什么时候重绘由 `app` 决定。这样渲染规则可以脱离整个 App 单独测。
2. 一条贯穿全篇的约定：**所有函数返回 `Text` / `Group` / `Markdown`，不返回裸字符串**。
   模型回复里出现 `[` 是很常见的事，而任何交给 `markup=True` 的 widget 的字符串都会被
   当成 Rich 标记解析——轻则把内容吃掉，重则抛 `MarkupError` 把界面打崩。
   `Text` 对象是已经解析好的，再交给 widget 不会被二次解析。
3. `PromptArea(TextArea)`（当初排在第 6 步的「提交语义」，最终落在这里）：
   Textual 8.x 的 `TextArea` 是**纯编辑器**，`_on_key` 里写死了
   `insert_values = {"enter": "\n"}` 并对 enter `stop()` + `prevent_default()`——
   键盘事件当场被吃掉，App 层挂 binding 根本收不到。所以在这一层接管：
   `enter` 发 `PromptArea.Submitted(value)` 消息（消息自带内容，handler 不必再回控件里捞）、
   **不**插换行；`NEWLINE_KEYS = {"alt+enter", "shift+enter", "ctrl+j"}` 插换行（F9）。
   三个键是必要的：`alt+enter` 在多数终端被发成 `ESC CR`，Textual 的 `_xterm_parser`
   只在键名长度为 1 时才补 `alt+` 前缀，`\r` 的键名是五字符的 `enter`，于是 alt 被丢掉、
   退化成普通 Enter，按下去消息直接就发出去了；`shift+enter` 同此；`ctrl+j` 是 LF，
   raw mode 下原样送出、哪个终端都一样，是那个保底可用的。
4. `user_block(text)` = `Text.assemble(("● ", "bold"), (text, ""))`。
   **不做 `You:` 之类的文字标签**（F7 只要求「可区分」），靠行首圆点加粗区分。
5. 状态栏：`status_bar(provider)` 用 `Table.grid(expand=True)` 左 `provider.name`、
   右 `provider.model`，两端对齐——比手工算空格可靠，终端宽度一变也不会错位（N6）。
6. `error_block(message)` = `Text(message, style="bold red")`。收的是**已经拼好的字符串**，
   不是异常对象：上游异常的原文可能夹带密钥，必须先过 `qicode.redact`（T16）。
7. `streaming_body(reply, elapsed)`：两种形态——还没拿到第一个增量就只显示
   `⠋ Imagining… (Ns)`；已有增量则正文 + 一行次要计时。转轮帧由**已用秒数**推出，
   而不是靠一个每次都 +1 的计数器，这样刷新频率变了转速也不会乱。
   `SPINNER_FRAMES` 用盲文点阵字符：等宽字体里显示宽度稳定是 1 列（同像素画的取舍）。
8. （T14 追加）`marked_markdown(text)` 与 `transcript(messages)` 给**退出回放**用，
   见 T14。
9. （T15 追加）助手回复改由 `ReplyBlock(Horizontal)` 承担两列版式，见 T15。
10. （T19 追加）启动横幅改由 `MascotBanner(Static)` 承担，见 T19。

**验证：** 把工具栏、状态栏、错误样式截图比对；`ruff check src/qicode/tui/` 无告警。

## T13: 入口装配
**文件：** `src/qicode/cli.py`（替换 T1 占位）
**依赖：** T2、T4、T9
**步骤：**
1. `CONFIG_PATH = ".qicode/config.yaml"` —— 相对**当前工作目录**找，不进 home、
   不看环境变量（spec「不做的事」里明确排除了其它配置来源）。
2. `def main() -> None`：
    - `try: cfg = load(CONFIG_PATH)`；`except ConfigError as exc: print(f"qicode: {exc}", file=sys.stderr); raise SystemExit(1) from None`。
      配置错误是**用户能自己修好**的，所以只给一行、不带 traceback（N4）。
    - `app = QicodeApp(cfg.providers)`；`app.run()`。
    - **横幅不在这里 print**，交给界面在 `on_mount` 里挂进对话区——否则它会留在
      Textual 接管屏幕**之前**的滚动缓冲里，两种输出混在一起。
3. （T14 追加）`run()` **之后**调 `_replay_transcript(app)`，把会话打到主屏幕。见 T14。

**验证：** `python -m qicode` 在合法配置下能启动 TUI；缺配置时打印可读错误并退出码非零。

## T14: 端到端联调与两项补课
**文件：** `tests/test_llm_providers.py`、`tests/test_cli.py`、`src/qicode/cli.py`、`src/qicode/tui/view.py`
**依赖：** T1–T13
**步骤：**
1. 用真实 anthropic 配置（`thinking: true`）跑：多轮对话、流式逐字、Imagining 计时、
   done 后 markdown 定型、思考内容不出现。
2. 用 openai 协议配置跑：同样多轮 + 流式。
3. 配两条 provider：启动出现选择列表，选定后状态栏正确。
4. 故意用错误 key：错误在对话区显示且不退出，可继续。
5. `/exit` 与 Ctrl+C：安全退出、终端无残留（终端 raw mode 由 Textual 自动还原）。
6. 建议用 tmux 验证 scrollback 行为：完成块用终端原生滚轮 / Ctrl+C 后 `[` 可回看。
7. **补课一（T7/T8 当时只靠实跑探针）**：写 `tests/test_llm_providers.py`，
   用假 SDK 客户端覆盖两适配器的事件翻译——正文增量、思考增量丢弃、正常结束、
   异常翻成 `err`、取消原样抛出、openai 侧 choices 为空的收尾块。
8. **补课二（退出后内容留不下来）**：Textual 跑在**备用屏幕**上，没有回滚缓冲，
   `run()` 一返回整屏内容**连同滚动历史一起消失**——用户按完 `/exit` 什么都看不到。
   加 `view.transcript(messages)` + `cli._replay_transcript(app)`，在 `run()` **之后**
   把对话重放到主屏幕，它才真正落进终端的回滚缓冲里。
   重放的是**对话本身**（用户说的、模型答的），不是把界面内容复制出来——那样会带上
   只在交互时有意义的边框和状态栏；耗时不重放。一句没聊就退出时直接返回，不留空白。
   助手回复按 markdown 重新渲染（同 F8 的理由）；`marked_markdown` 负责把 `●` 与
   一段 markdown 排成一行，遇到块级语法行首（标题 / 列表 / 引用 / 围栏 / 表格）时
   退化成「圆点独占一行 + 原文照旧」——排版难看一点，但不篡改语义。

**验证：** 逐条对照 `docs/v1/checklist.md` 记录证据；`pytest` 全绿。

## T15: 回复块改成两列、修「定型时跳版」
**文件：** `src/qicode/tui/view.py`、`src/qicode/tui/app.py`
**依赖：** T10、T12
**步骤：**
1. 把 `#streaming` 那块独立动态区**删掉**。旧写法里流式文字画在 `RichLog` 下面另一个
   Static 上，回复生成时贴着输入框，一定型写进 `RichLog` **整段跳到上面去**
   （历史不满一屏时 `RichLog` 从头顶开始排）。
2. 对话区从 `RichLog` 换成 `VerticalScroll`，改成一棵**控件树**：
   `_append(block)` 把定型内容包成 `Static(block, expand=True)` mount 进去；
   助手回复走 `_start_reply_block()`，mount 一块**空的 `ReplyBlock`** 作为对话区的
   **最后一个子节点**，流式与定型都刷它——控件自始至终没挪过窝。
3. `ReplyBlock(Horizontal)`：左列 `●`（`width: auto`）、右列正文（`width: 1fr`），
   `align-vertical: top` 让圆点对齐正文第一行。做成两列是因为前两条路都实测撞过墙：
   - Rich 的 `Table.grid`：**单元格里的 `Markdown` 根本不折行**，整段被压成一行、
     末尾加省略号截断。`expand` / `ratio` / `padding` 各种组合都试过，毫无区别。
   - 把 `● ` 拼进 markdown 源码：**纯中文长段会被搞砸**。Rich 的折行
     （`rich/_wrap.py` 的 `divide_line`）按空白分词，一整段不含空格的中文就是一个
     「词」；词比整行宽时它先换行再硬折，于是已经占着第一行的 `● ` 被晾在那儿、
     正文从第二行顶格开始。中英混排反而没事，而纯中文恰恰是最常见的情况。
   两列之后宽度由 Textual 布局算准，圆点判成 1 列还是 2 列都不影响布局。
4. `_follow_tail` / `_engage_tail_anchor`：内容**真的溢出视口之后**（`max_scroll_y > 0`）
   才 `anchor()`。不能一上来就锚定——Textual 的锚定**无条件贴底**（合成器每次布局算
   `内容底部 − 容器高` 后直接写进 `scroll_y`，绕过 `validate_scroll_y` 的 clamp），
   内容比视口矮时是负数，整块被往下推：启动时 banner 悬在屏幕下半截；更要命的是
   最后一块只要长高一行，上面所有内容都被顶上去一行。
   判断时机三处：`_follow_tail` 里立刻判一次、`call_after_refresh` 补一次（整段回复
   一口气到达时中间没有别的帧，只有这一下能接住）、`_end_turn` 再判一次。
5. CSS 用类型选择器 `#log > Static` 给「直接 mount 的成品块」定宽——Textual 的
   CSS 类型选择器匹配**基类**（`_css_type_names` 装的是整条 MRO 的类名），所以
   `MascotBanner` 这类子类也会被它命中。

**验证：** `tests/test_tui_app.py` 里对「定型前后位置不变」的断言；tmux 里实跑观察
回复定型时不跳。

## T16: 上游错误里的密钥先抹掉再显示（N5）
**文件：** `src/qicode/redact.py`、`src/qicode/tui/app.py`、`tests/test_redact.py`
**依赖：** T10
**步骤：**
1. 新模块 `qicode.redact`：`MASK = "***"`、`MIN_SECRET_LENGTH = 8`、
   `redact(text, secrets) -> str`。
2. `app._safe_message(err)` 在把错误交给界面**之前**过一遍
   `redact(str(err), [cfg.api_key for cfg in self.providers])`。
   **必须在这里做，不能更晚**：这句文字会被画进对话区，是用户肉眼可见的一屏内容，
   截屏、录屏、共享屏幕都带得走。抹的是**所有** provider 的 key，不止当前这个——
   出错时用户多半正要切到另一家去试。
3. 边界写清楚：短于 8 字符的「密钥」**放过**。`api_key` 在很多配置里根本不是密钥，
   是占位符（本地 Ollama 常写 `ollama`），无差别替换会把「cannot reach ollama server」
   这种正常排障信息毁掉。真实密钥都在 20 字符以上，8 这个门槛全盖住。
   空串由同一道关挡掉（`str.replace("", ...)` 会在每两个字符之间插一刀）。
4. `views.error_block` 收的是**已经拼好的字符串**，不是异常对象——只有 `app` 同时
   握着异常和 provider 配置，脱敏只能在它那里做。

**验证：** `pytest tests/test_redact.py`；再加一条界面级用例：错误文本里带上配置里的
`api_key`，断言屏幕上出现的是 `***`。

## T17: 把 provider / model 写进 system prompt（F4）
**文件：** `src/qicode/prompt.py`、`src/qicode/llm/anthropic_provider.py`、`src/qicode/llm/openai_provider.py`
**依赖：** T4、T7、T8
**步骤：**
1. `SYSTEM_PROMPT` 常量改成函数 `system_prompt(provider_name, model) -> str`。
   这两个值**只存在于本地配置里**，模型自己看不到：不告诉它，用户问「你是什么模型」时
   它只能答「我不掌握这个信息」（实测原话），或者凭训练数据编一个。
2. 措辞上有意分开两件事：「你是谁、跑在什么模型上」——我们知道，要求它照实说；
   「这个模型是哪家公司训练的」——我们不知道，就明确说不知道。一句笼统的「不要编造」
   会让它对**两者**都用同一句「我不知道」搪塞过去，而那正是要修的毛病。
3. 两个适配器各自改成现拼：anthropic 传顶层 `system=`，openai 兼容侧插成 messages
   的第一条（这条协议没有顶层 system 参数）。`conversation` 层保持纯 user/assistant。

**验证：** 真机问模型「你是什么模型」，答出的是**当前配置里的模型名与接入点名**。

## T18: 横幅版式——拉开间距、削掉尖头
**文件：** `src/qicode/prompt.py`、`src/qicode/tui/view.py`
**依赖：** T4
**步骤：**
1. 加 `BANNER_GAP = 4`：吉祥物和右侧文字之间留几格，太挤的话文字像是贴在脸上。
2. 图案上下各留一行空白（底部那一行别跟第一条消息贴在一起）。
3. 削掉 `MASCOT_ART` 头顶那一行 1 格宽的尖——只剩一格宽的突起在等宽字体里读起来
   像根天线，不像猫耳。

**验证：** tmux 抓 ANSI 还原色块，比对削尖前后的形状。

## T19: 吉祥物每 4 秒轻轻蹦一下（F7）
**文件：** `src/qicode/prompt.py`、`src/qicode/tui/view.py`、`tests/test_prompt.py`
**依赖：** T4、T12、T18
**步骤：**
1. `prompt.py` 加蹦跳节奏：`BOUNCE_HEADROOM = 1`（图案上方的起跳空间）、
   `BOUNCE_FRAMES = (1, 1, 0, 0)`（蹦一下的逐帧高度）、
   `BOUNCE_PERIOD_FRAMES = 40`（40 帧 × 0.1 秒 = 每 4 秒蹦一下）、
   `FRAME_INTERVAL = 0.1`，以及 `bounce_offset(frame) -> int`。
   绝大多数帧返回 0——蹦是**偶发**的，比一直来回晃更像「活着」，也不抢注意力。
2. `render_banner` 加 `art_offset: int = 0`，图案外套一个固定高度的**框**
   （`图案行数 + BOUNCE_HEADROOM`）：图案在框里往上挪、框本身不动，
   **返回的行数因而恒定**。右侧文字的落点按**框**高居中、与 `art_offset` 无关，
   图案蹦的时候文字纹丝不动。
3. `MascotBanner(Static)`：`on_mount` 里 `set_interval(FRAME_INTERVAL, _tick)`；
   `_tick` 先进可见性判断、再推进帧号、**图案没变就不 `update()`**
   （一个周期 40 帧里只有 4 帧不一样）。
   可见性用 `region` 与 `container_viewport` 比——两者**都是屏幕坐标**
   （实测：容器下移 4 行，横幅 `region.y` 从 0 变 4；容器再滚动 30 行，它从 4 变 −26），
   同一条坐标系里直接比，不用自己减 `scroll_y`；还没上屏时两者都是空区域，
   判成「看不见」正是想要的。
4. 一个容易漏的坑：图案的**显示宽度**要从原始 `MASCOT_ART` 上量。`MASCOT_BANNER`
   是 markup，一个格子是一串 `[on #xxxxxx] [/]`，字符串长度跟屏幕列数根本不是一回事
   （一行 12 格，markup 长达 72 字符），拿它去补占位行会把右侧文字推出屏幕。
5. 两条约束必须守住，也是测试要钉死的：**任何 `art_offset` 下行数都一样**；
   **右列文字不随 `art_offset` 移动**。横幅是对话区的第一个子节点，高一行矮一行
   下面全跟着回流——那正是 T15 刚修掉的「跳版」。

**验证：** `tests/test_prompt.py`（纯函数层穷举每个 offset、`bounce_offset` 的周期与
静止占多数、cwd 含 `[` 原样渲染）+ `tests/test_tui_app.py`（真界面：换帧不搅动布局、
滚出视野后**逐帧**断言帧号与内容都不再前进——攒一圈再比会因为末尾正好落回静止帧而假绿）。

## 执行顺序
```
T1 ─┬─ T2 ─┬─ T3
    │      └─ T5 ─┬─ T6
    │             ├─ T7
    │             └─ T8
    ├─ T4
    └─ T9 ─┬─ T10 ─┬─ T15 ─┬─ T19
           │       └─ T16  │
           ├─ T11         │
           └─ T12 ────────┘
T2,T4,T9 ─ T13 ─ T14
T4 ─ T18 ─ T19
T7,T8 ─ T17
```
（T4 可与 T2/T5 并行；T7、T8 可并行；T10/T11/T12 在 T9 后可并行推进。
T15–T19 是实现过程中追加的，编号即实际动工的先后。）
