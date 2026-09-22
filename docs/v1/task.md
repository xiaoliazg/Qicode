# Qicode V1 Tasks

> 依据已批准的 spec.md + plan.md。执行规则：按序执行，每个任务先跑验证、有证据再标完成。
>
> **范围变更（2026-09-22）**：后端从 vLLM 切到本地 Ollama，同时把 Anthropic 原生协议的适配器提前到 V1 交付、
> 思考开关的请求参数改为可配置。T1–T11 已按本文件实施完成；本次变更落在 **T12（Anthropic 适配器）**、
> **T13（清理与复验）** 两个新任务，外加对 T1/T2/T4/T6/T7/T8/T10/T11 的措辞与范围修订（下表与正文已同步更新）。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `pyproject.toml` | 项目元信息、依赖（openai/anthropic/pyyaml/prompt_toolkit/rich）、`qicode` 入口、requires-python>=3.10 |
| 新建 | `qicode/__init__.py` | 版本号 `__version__` |
| 新建 | `qicode/config.py` | ProviderConfig（5 个必填 + 2 个可选字段）、ConfigError、ConfigLoader（定位/解析/校验/${ENV} 展开/模板引导） |
| 新建 | `qicode/provider/base.py` | ChatProvider ABC、StreamDelta、ProviderError |
| 新建 | `qicode/provider/openai_compat.py` | OpenAICompatProvider、system 提示常量、异常翻译、create_provider 工厂 |
| 新建 | `qicode/provider/anthropic_compat.py` | AnthropicProvider（Anthropic 原生协议，对接 DeepSeek 等） |
| 新建 | `qicode/provider/__init__.py` | 导出 provider 层公开符号 + `create_provider` 工厂（按 `protocol` 分发） |
| 新建 | `qicode/session.py` | ChatMessage、Session（历史/切换/chat_stream 转发） |
| 新建 | `qicode/tui/interrupt.py` | StreamInterrupter（termios cbreak + select 轮询的 Esc 监听线程） |
| 新建 | `qicode/tui/renderer.py` | StreamRenderer（rich Live + 思考块 + Markdown + 前导空行剥除）、StreamResult |
| 新建 | `qicode/tui/repl.py` | Repl 主循环、命令分发、ProviderError 统一出口 |
| 新建 | `qicode/tui/__init__.py` | 导出 run_repl |
| 新建 | `qicode/cli.py` | argparse（-c/-p）、启动编排、provider 选择、退出码（0/1/2） |
| 新建 | `qicode/__main__.py` | `python -m qicode` 入口 |
| 新建 | `config.example.yaml` | 示例配置（OpenAI 兼容 + Anthropic 两种协议各一例，含 thinking_params 注释） |
| 新建 | `README.md` | 安装/配置/使用说明（含各协议配置示例与 thinking 前提） |
| 新建 | `tests/test_config.py` | 配置加载单测 |
| 新建 | `tests/test_provider.py` | OpenAI 兼容适配器单测（mock chunk 流） |
| 新建 | `tests/test_anthropic_provider.py` | Anthropic 适配器单测（mock 事件流） |
| 新建 | `tests/test_session.py` | session 单测 |
| 新建 | `tests/test_interrupt.py` | Esc 监听器单测（伪 tty，含退出后不抢 stdin 的回归用例） |
| 新建 | `tests/test_renderer.py` | 渲染器单测（非 tty Console） |
| 新建 | `tests/test_repl.py` | 命令分发与对话流程单测 |

## 任务列表

### T1: 项目骨架

**文件：** `pyproject.toml`、`qicode/__init__.py`
**依赖：** 无
**步骤：**
1. 写 `pyproject.toml`：`[project]` name=qicode、version=0.1.0、requires-python=">=3.10"、dependencies=["openai", "pyyaml", "prompt_toolkit", "rich"]；`[project.optional-dependencies] dev=["pytest"]`；`[project.scripts] qicode = "qicode.cli:main"`（cli 后建，先声明）
2. 写 `qicode/__init__.py`：`__version__ = "0.1.0"`
3. `pip install -e .` 安装

> **范围变更**：依赖清单增 `anthropic`，见 T12。

**验证：** `python -c "import qicode; print(qicode.__version__)"` 输出 `0.1.0`

### T2: config 模块 + 单测

**文件：** `qicode/config.py`、`tests/test_config.py`
**依赖：** T1
**步骤：**
1. `ProviderConfig` frozen dataclass（六字段，`thinking` 缺省 False）
2. `ConfigError(message, line=None)`
3. `ConfigLoader`：`DEFAULT_PATH`、`TEMPLATE` 常量（六字段示例 + thinking 注释）、`load(path=None)` 类方法（定位→读→yaml 解析→校验→展开→返回列表）
4. 校验规则：顶层 `providers` 列表非空；每项 name/protocol/model/base_url/api_key 必填非空字符串；protocol 仅 `openai_compatible`；name 不重复；base_url 以 `http://` 或 `https://` 开头；thinking 仅可选 bool；YAML 错误带 `problem_mark.line`
5. `_expand_api_key`：`${VAR}` 整串匹配 → 取环境变量，未设置抛 ConfigError（写明变量名）；否则原样返回
6. 单测覆盖：文件不存在（message 含模板）/ 坏 YAML（带行号）/ 缺字段 / 重复 name / 非法 protocol / 非法 base_url / 环境变量未设置 / 环境变量已设置 / 合法双 provider 解析

> **范围变更**：`ProviderConfig` 增可选字段 `thinking_params`、`SUPPORTED_PROTOCOLS` 增 `anthropic`、`TEMPLATE` 换双协议示例，见 T12。

**验证：** `python -m pytest tests/test_config.py -v` 全绿

### T3: provider 基础层

**文件：** `qicode/provider/base.py`、`qicode/provider/__init__.py`
**依赖：** T2
**步骤：**
1. `base.py`：`ProviderError(Exception)`、`StreamDelta` dataclass（kind/text/error）、`ChatProvider` ABC（`name` property + `stream_chat(messages, *, cancel_event)` abstractmethod，docstring 写明契约：打断=正常 done 无 error；出错=done 携带 error 而不抛异常）
2. `__init__.py` 导出四个符号（openai_compat 的符号在 T4 补进导出，anthropic_compat 的在 T12 补进）

**验证：** `python -c "from qicode.provider import ChatProvider, StreamDelta, ProviderError; import inspect; print(inspect.isabstract(ChatProvider))"` 输出 `True`

### T4: OpenAI 兼容适配器 + 单测

**文件：** `qicode/provider/openai_compat.py`、`qicode/provider/__init__.py`、`tests/test_provider.py`
**依赖：** T3
**步骤：**
1. `SYSTEM_PROMPT` 常量（「你是 Qicode，一个运行在终端里的 AI 助手」类文案）
2. `OpenAICompatProvider.__init__`：存 config，建 `OpenAI(base_url, api_key, max_retries=0)`（构造不触网）
3. `stream_chat`：组装 `[system] + messages`；`thinking=True` 时 kwargs 加 `extra_body={"chat_template_kwargs": {"enable_thinking": True}}`；`client.chat.completions.create(stream=True)` 迭代；每个 chunk 前查 `cancel_event.is_set()` → 置位则 break 产 `done`（无 error）；`getattr(delta, "reasoning", None)` → `StreamDelta("thinking", r)`；`delta.content` → `StreamDelta("content", c)`；正常结束产 `done`
4. `_translate_error(e, api_key)`：APIConnectionError→「无法连接 {base_url}，请确认服务已启动」；APITimeoutError→超时；APIStatusError→带状态码；流中断→「流中断」；message 兜底 replace api_key 为空串；统一包成 ProviderError
5. `create_provider(config)` 工厂：`openai_compatible` → OpenAICompatProvider；其他 protocol → ProviderError（「V1 暂不支持」）
6. 更新 `__init__.py` 导出 OpenAICompatProvider/create_provider
7. 单测（mock：monkeypatch `OpenAI` 客户端，喂假 chunk 序列，不触网）：thinking 开→请求带 chat_template_kwargs；thinking 关→不带；reasoning chunk→thinking 事件；content chunk→content 事件；cancel_event 预置→立即 done 无 error；连接异常→ProviderError 且 message 不含 key

> **范围变更**：thinking 参数改为可配置（`config.thinking_params` 优先于内置默认值）；工厂从本模块**迁到 `provider/__init__.py`**（它服务所有协议，留在某个适配器文件里会让另一个适配器反向 import 它），见 T12。

**验证：** `python -m pytest tests/test_provider.py -v` 全绿

### T5: session 模块 + 单测

**文件：** `qicode/session.py`、`tests/test_session.py`
**依赖：** T4
**步骤：**
1. `ChatMessage` dataclass（role/content）
2. `Session(providers)`：存列表、按首个 provider 建当前实例（`create_provider`）、`_messages` 空列表
3. 方法：`add_user` / `add_assistant` / `clear_history` / `messages`（返回浅拷贝）/ `current_name` / `all_names` / `switch(name)`（KeyError 若不存在；重建适配器；历史保留）/ `chat_stream(ev)`（纯转发 `self._current_provider.stream_chat(self.messages(), cancel_event=ev)`）
4. 单测：add 顺序保持 / clear 后为空 / messages 拷贝隔离（改副本不影响内部）/ switch 换当前且历史保留 / switch 不存在抛 KeyError / chat_stream 转发（假 provider 子类）

**验证：** `python -m pytest tests/test_session.py -v` 全绿

### T6: StreamInterrupter

**文件：** `qicode/tui/interrupt.py`、`tests/test_interrupt.py`
**依赖：** T5
**步骤：**
1. `__enter__`：`sys.stdin` 须是 tty（否则直接 no-op 模式）；`termios.tcgetattr(fd)` 存原值 → 新值 `cbreak` 关 `ECHO` → `tcsetattr`；清 `_stop` 事件；启动 daemon 线程监听
2. 监听循环必须是 `select.select([fd], [], [], 0.1)` 带超时轮询 + 每轮检查 `_stop`，**不能用阻塞式 `os.read`**（见下方踩坑）
3. `__exit__`：`finally` 里先 `_stop.set()` → `join(timeout=1.0)` 等监听线程真正退出 → 再恢复原 termios
4. 写清 docstring：为什么必须 cbreak、为什么单独线程（主线程在 SDK 迭代里阻塞）、为什么必须 select 轮询
5. 单测（`os.openpty()` 造伪 tty + monkeypatch `sys.stdin`，不碰真实终端）：tty 下按 Esc 置位 event / 非 tty 走 no-op 不报错 / 退出后线程已结束 / **退出后再往 pty 写字符，字符必须仍留在 pty 里（没被监听线程吃掉）** / 退出后 termios 的 ECHO、ICANON 语义位已复原

> **踩坑（已修，务必保留回归用例）**：最初用阻塞 `os.read(fd, 1)` + `join(timeout=0.5)`，前提是"恢复 termios 后 os.read 会抛错退出"——**这个前提是错的**：线程会一直阻塞在 `os.read` 上，与 prompt_toolkit 抢 stdin，导致第一轮对话之后所有按键都被吞掉（表现为第二问起输入框空、无回复）。改成 select 轮询 + stop 事件才真正可靠。

**验证：**
- `python -m pytest tests/test_interrupt.py -v` 全绿
- 交互式验证（tmux 里跑测试脚本：进入上下文后 sleep 3 秒并打印 `ev.is_set()`）：期间按 Esc → 最终打印 `True`；不按 → `False`
- 连续两轮真实对话，第二轮输入正常回显并被处理（回归用例的现场版）

### T7: StreamRenderer + 单测

**文件：** `qicode/tui/renderer.py`、`tests/test_renderer.py`
**依赖：** T5（StreamDelta 来自 T3）
**步骤：**
1. `StreamResult` dataclass：`text`（正文，入库用）+ `error`（`ProviderError | None`）。渲染层只负责把两个东西各自带出去，怎么处理由 repl 决定
2. `StreamRenderer(console=None)`：`_thinking_buf`/`_content_buf` 两个 str 缓冲；console 缺省 `Console()`
3. `run(deltas) -> StreamResult`：`Live(screen=False, refresh_per_second=10, get_renderable=self._renderable)`；消费 delta：thinking→追加 `_thinking_buf`；content→**首个非空 content 增量先 lstrip("\n")**（思考开启时服务端实测会带前导空行）再追加；done→记下 `delta.error` 并结束；返回 `StreamResult(self._content_buf, error)`
4. **每个增量追加完立刻 `live.refresh()` 强制重绘**（N1「逐字」的落点：rich 的 `refresh()` 立即执行、不受 `refresh_per_second` 节流；帧率只作兜底）
5. `_renderable`：`Group`（thinking 块：`Text(_thinking_buf, style="dim italic")` 仅当非空 + 正文 `Markdown(_content_buf or "")`）
6. 思考文本不入 `text`、不入库（D11）
7. 单测（`Console(record=True, file=StringIO(), width=80)` 非 tty）：混合 thinking/content 序列 → text 正确且无前导空行；纯 content（无 thinking）→ 无思考块；空流 → 空串；**done 带 error → error 被带出来且已生成正文不丢**；done 无 error → error 为 None；**逐字刷新 → 用假 Live 类替换掉 `Live`，断言 refresh 调用次数等于增量数**（真 tty 下的观感由 T11 的 AC5 验）

**验证：** `python -m pytest tests/test_renderer.py -v` 全绿

### T8: Repl 主循环 + 单测

**文件：** `qicode/tui/repl.py`、`qicode/tui/__init__.py`、`tests/test_repl.py`
**依赖：** T6、T7
**步骤：**
1. `run_repl(session, console=None)`：`PromptSession()`（InMemoryHistory 默认）；while 循环：`prompt("› ")`，KeyboardInterrupt→第一次清行继续/2 秒内第二次 return；EOFError→return
2. 输入以 `/` 开头 → `_handle_command(session, line, console) -> bool`（False 表示退出）：`/clear`→清空+确认行；`/provider`→列出（当前项 `*` 前缀）；`/provider <name>`→switch，KeyError→「未找到 provider: x」；`/exit`→False；未知→「未知命令：xxx（支持 /clear /provider /exit）」
3. 普通消息流程：`add_user` → `ev=Event()` → `with StreamInterrupter(ev): result = StreamRenderer(console).run(session.chat_stream(ev))` → 先 `if result.error: self._report_error(result.error)` → 再 `if result.text: add_assistant(result.text)`
4. **错误必须两条通路都接**：`result.error`（provider 按契约用 done 事件携带，不抛异常）走 `_report_error`；生成途中抛出的 `ProviderError`/`KeyboardInterrupt` 用 `try/except` 接住，同样交给 `_report_error`。`_report_error` 是唯一出口（红面板），会话不崩（N2）
5. 正文为空则不入历史（避免空 assistant 消息污染后续上下文）；截断产生的半截正文照常入库（AC8「所见即所得」）
6. `tui/__init__.py` 导出 run_repl
7. 单测（`_handle_command` 抽成纯函数可测）：/clear 清空 / /provider 列表含当前标记 / /provider x 切换 / /provider 不存在 提示 / /exit 返回 False / /foo 提示且返回 True / 大小写不敏感；对话流程：正常入库顺序 / 截断半截入库 / 异常通路出红面板且不入 assistant / **事件通路（done 带 error）出红面板且不入空回复** / 事件通路带半截正文时正文仍入库 / 空回复不入库 / 主循环单次 Ctrl+C 继续、双次退出、EOF 退出

> **踩坑（已修，务必保留回归用例）**：provider 的契约是"出错不抛异常、改用 `done` 携带 error"，早期 repl 只 `except ProviderError` 接了异常通路，`delta.error` 被静默丢弃——服务端返回 502 时用户只看到一条空回复、一句报错都没有。引入 `StreamResult` 后两条通路都归 `_report_error`。

**验证：** `python -m pytest tests/test_repl.py -v` 全绿

### T9: cli 入口 + __main__

**文件：** `qicode/cli.py`、`qicode/__main__.py`
**依赖：** T8
**步骤：**
1. `main(argv=None) -> int`：argparse（`-c/--config`、`-p/--provider`、`--debug`）
2. `ConfigLoader.load(args.config)`：ConfigError → stderr 打印（含模板/行号）→ 返回 1
3. provider 选择：`-p` 给了则校验存在（不存在→1）；否则打印编号列表，循环 `input("选择 provider [1-N]: ")`（非法重选；EOF/Ctrl+C→退出 1）
4. 建 `Session(configs)` + `switch(所选)` → `run_repl(session)` → 返回 0
5. 最外层 try/except 兜底：意外异常→`--debug` 打印堆栈，否则「未预期的错误，可用 --debug 查看详情」→ 返回 2
6. `__main__.py`：`raise SystemExit(main())`

**验证：**
- `qicode --help` 显示 -c/-p/--debug
- `qicode -c /nonexistent.yaml` → 退出码 1，输出含配置模板
- `qicode -c <protocol 写成 gemini 的配置>` → 退出码 1，输出含「暂不支持」与支持的协议列表（AC20）
- 合法配置 + `-p` 在 tmux 里启动 → 进入 `› ` 提示符（冒烟，深度交互留 T11）

### T10: 示例配置 + README

**文件：** `config.example.yaml`、`README.md`
**依赖：** T9
**步骤：**
1. `config.example.yaml`：四种配置示例——① 本地 Ollama（`openai_compatible`，`base_url: http://127.0.0.1:11434/v1`，`model: qwen2.5vl:7b`，`api_key: ollama`，thinking 关）；② 同一协议下 thinking 开启的示例（注释说明默认请求参数是 `chat_template_kwargs.enable_thinking`，以及服务端/模型需真的支持思考输出）；③ **Anthropic 官方 Claude**（`protocol: anthropic`、`base_url: https://api.anthropic.com`、`api_key: ${ANTHROPIC_API_KEY}`、`model: claude-...`，注释给出 `thinking_params: {type: adaptive, display: summarized}` 的写法（**不要写 `{type: enabled, budget_tokens: N}`**——那套固定预算写法在当前 Claude 模型上已移除、传了直接 400），并**如实注明该链路尚未真机验证**）；④ Anthropic 兼容服务（DeepSeek 的 Anthropic 端点，同样注明未实测）。字段说明改为「5 个必填 + `thinking` / `thinking_params` 两个可选」，并说明 `thinking_params` 缺省时用适配器默认值
2. `README.md`：项目简介（后端为本地 Ollama，协议层同时提供 Anthropic 原生适配器）/ Python ≥3.10 + conda 环境约定 / `pip install -e ".[dev]"` / 配置说明（字段表 + `${ENV_VAR}` + 两种协议的配置片段 + `thinking_params` 用法）/ 启动与命令（/clear /provider /exit、Esc 打断、Ctrl+C×2、Ctrl+D）/ thinking 的前提说明（由后端与模型决定，本机 Ollama 的 `qwen2.5vl:7b` 无思考能力）/ 开发（pytest）

**验证：** 对照 README 步骤从零走一遍（新目录 `pip install -e .` → 复制示例配置 → `qicode` 启动到提示符），全部走得通；`config.example.yaml` 能被 `ConfigLoader.load` 正常解析

### T11: 集成冒烟（真服务）

**文件：** 无新文件（用 `/tmp` 下的临时配置，**不进仓库**）
**依赖：** T10
**步骤：**
1. 写临时配置：① `ollama`（`http://127.0.0.1:11434/v1`，模型 `qwen2.5vl:7b`，`api_key: ollama`，thinking 关）② `broken`（`http://127.0.0.1:9/v1`，用于错误恢复演练）
2. tmux 启动 `qicode -c 临时配置 -p ollama`
3. 连发三问：① 多轮记忆（告诉它一个信息→几轮后问回）② 让它输出代码块（看 Markdown 渲染）③ 长回复生成中按 Esc（看截断+可继续对话引用截断内容）
4. 依次执行 `/clear`、`/provider`、`/foo`、`/provider broken` + 发一条消息（看错误面板）、切回 ollama、`/exit`
5. 收尾补验：上键翻历史、`Ctrl+D` 与 `Ctrl+C`×2 退出并检查退出码

**验证：** 上述每步行为符合 spec AC5/AC7/AC8/AC9/AC12/AC15/AC16/AC17，tmux 录屏或滚动回看确认；AC13/AC14（思考块）与 AC18（Anthropic 真机）本机无可用后端，按 spec 已注明的前提留待接入支持思考的模型后复验

### T12: 双协议与可配置思考参数（本轮范围变更的代码部分）

**文件：** `pyproject.toml`、`qicode/config.py`、`tests/test_config.py`、`qicode/provider/openai_compat.py`、`tests/test_provider.py`、`qicode/provider/anthropic_compat.py`（新建）、`tests/test_anthropic_provider.py`（新建）、`qicode/provider/__init__.py`、`qicode/tui/renderer.py`、`tests/test_renderer.py`
**依赖：** T4、T11
**步骤：**

*12.1 配置层*

1. `ProviderConfig` 增可选字段 `thinking_params: dict | None = None`（5 个必填 + 2 个可选）
2. `SUPPORTED_PROTOCOLS` 改为 `("openai_compatible", "anthropic")`；未知 protocol 的报错文案含「暂不支持」并列出支持项（AC20）
3. 校验 `thinking_params`：出现时必须是 mapping（dict），值原样保留——内部键名是协议相关的，交给适配器解释，配置层不越界校验；非 mapping 报错指向该字段
4. `TEMPLATE` 换双协议示例（OpenAI 兼容 + Anthropic），注释说明 `thinking` 是开关、`thinking_params` 是「怎么表达」
5. 单测补：未知 protocol 提示「暂不支持」/ `thinking_params` 非 mapping 报错 / `anthropic` 协议能解析且 `thinking_params` 原样保留

*12.2 OpenAI 兼容侧：参数可配置*

6. 加 `DEFAULT_THINKING_PARAMS = {"chat_template_kwargs": {"enable_thinking": True}}` 常量，作为缺省表达
7. `stream_chat` 里 thinking 开启时取 `config.thinking_params or DEFAULT_THINKING_PARAMS` 作为 `extra_body`（AC19）
8. `pyproject.toml` 依赖清单补 `anthropic`
9. 单测补：自定义 `thinking_params` 时请求体里是自定义那份 / 未配置时是默认那份

*12.3 Anthropic 原生适配器*

10. `DEFAULT_THINKING_PARAMS = {"type": "adaptive", "display": "summarized"}` 常量（本协议的缺省表达）。两个字段都不是随便挑的：固定预算写法在当前 Claude 模型上已被移除（传了 400），`display` 缺省为 `"omitted"` 会让思考内容变成空串、界面看不到任何思考文字
11. `AnthropicProvider.__init__`：存 config，建 `Anthropic(base_url=config.base_url, api_key=config.api_key, max_retries=0)`（构造不触网）
12. `stream_chat`：把 `ChatMessage` 列表转成 Anthropic 的 messages（role 只有 user/assistant）；内置 `SYSTEM_PROMPT` 走**顶层 `system` 参数**（Anthropic 协议里 system 不是一条消息，这条与 OpenAI 侧不同，注释里写清）；`thinking=True` 时传 `thinking=config.thinking_params or DEFAULT_THINKING_PARAMS`；`max_tokens` 传模块常量 `DEFAULT_MAX_TOKENS = 16384`（本协议必填、无服务端默认值；开启思考后 thinking 与正文共享该上限，偏小会让长回答被拦腰截断）；**不传 `temperature`**（SDK 1.7.0 的 `messages.stream()` 签名里已无此参数）
13. 流式实现用 `with client.messages.stream(...) as stream:` 逐事件迭代（D14），**只处理原生 `content_block_delta` 一路**：`event.type == "content_block_delta"` 且 `event.delta.type == "text_delta"` → `StreamDelta("content", event.delta.text)`；`event.delta.type == "thinking_delta"` → `StreamDelta("thinking", event.delta.thinking)`；其余（含 `"signature_delta"`、以及所有非增量事件）一律跳过。⚠️ 迭代器会把同一个增量 fire 两次——先原生 `content_block_delta`、再 SDK 合成事件——**两条路任选其一、绝不能都处理**，否则正文与思考都会输出两遍。本项目取原生那一路（官方 `streaming.md` 的写法、线级稳定接口）；`text_delta`/`thinking_delta` 是 **delta 的子类型名**，不是顶层事件类型；每个事件前查 `cancel_event.is_set()` → 置位则 break 产 `done`（无 error）
14. `_translate_error`：`APITimeoutError`（先判，它是 `APIConnectionError` 的子类）→ 超时 / `APIConnectionError` → 无法连接，提示检查服务与地址 / `APIStatusError` → 带状态码 / 其他 → 兜底；统一包成 `ProviderError` 并做 api_key 擦除；请求阶段与流迭代阶段各自兜住，产 `done` + error 而非抛异常
15. 工厂：`create_provider` 与 `PROTOCOLS` 表放 `provider/__init__.py`（从 `openai_compat.py` 迁出，原因见该文件 docstring），`anthropic` 进表；`provider/__init__.py` 同时导出 `AnthropicProvider`
16. 单测（monkeypatch 掉 `Anthropic` 客户端，喂假事件序列，不触网）：原生 delta 序列 → thinking/content/done 顺序不变；`signature_delta` 与非增量事件被跳过；**原生 `content_block_delta` 与合成事件同时出现时，正文只被处理一次（防重复的回归用例）**；常量形状用例（`type` 必须是 adaptive、不得含 `budget_tokens`、`display` 必须是 summarized）；thinking 开→请求带默认 `thinking` 参数；thinking 开 + 自定义 `thinking_params`→请求体里是自定义那份；thinking 关→不带 `thinking`；system 提示走顶层参数、不出现在 messages 里；`max_tokens` 有值；cancel_event 预置→立即 done 无 error；连接异常→done 带 ProviderError 且 message 不含 key；工厂对 `anthropic` 返回 `AnthropicProvider`、对未知协议抛 ProviderError

*12.4 渲染逐字化（N1）*

17. `StreamRenderer.run()` 改为：`Live(screen=False, refresh_per_second=10, get_renderable=...)`，**每个增量追加进缓冲后立即 `live.refresh()`**——rich 的 `refresh()` 立即执行、不受帧率节流，这样字符才是一个一个长出来的；原来的 `refresh_per_second=4` 只作兜底
18. 注释写清代价与退路：每 token 重解析半截 Markdown + 整块重绘，V1 长度下可忽略；若某些终端可见闪烁，退路是节流到 20~30fps
19. 单测：用假 `Live` 类替换 `qicode.tui.renderer.Live`，断言 `refresh()` 调用次数等于增量数（真 tty 下的观感由 T13 的 AC5 复验）

**验证：**
- `python -m pytest tests/test_anthropic_provider.py tests/test_provider.py tests/test_config.py tests/test_renderer.py -v` 全绿
- `python -m pytest tests/ -v` 全绿
- 确认 `qicode/session.py`、`qicode/cli.py` **一行未改**，`qicode/tui/` 下只有 `renderer.py` 因 12.4 的渲染调整而改动、并非因新增协议而改动 —— AC18 的「上层零改动」由此坐实

### T13: 示例、文档与措辞清理 + 全量复验

**文件：** `config.example.yaml`、`README.md`、`pyproject.toml`、`qicode/config.py`、`qicode/cli.py`、`qicode/provider/openai_compat.py`、`qicode/tui/renderer.py`、`tests/test_renderer.py`、`tests/test_provider.py`、`tests/test_session.py`
**依赖：** T12
**步骤：**
1. 清理已废弃后端的措辞：仓库内不再出现 `agent-brain`、不再出现 `10.21.1.45`，`vLLM` 只允许作为**历史说明**出现；示例与文档统一指向本地 Ollama + Anthropic 两协议
2. **保留**「思考结束后首个 content chunk 带前导空行、须剥除」的处理——这是与具体后端无关的通用边界情况；注释里说明来历，代码与回归用例都留着
3. `pyproject.toml` description、`qicode/cli.py` 的 `--help` 描述、`config.py` / `openai_compat.py` / `renderer.py` 的 docstring 与注释同步更新
4. `config.example.yaml` 与 `README.md` 按 T10 的双协议要求改写（这是 T10 在范围变更下的重做）
5. 全量复验：单测 + tmux 重跑 T11 的主流程

**验证：**
- `grep -rn "agent-brain\|10\.21\.1\.45" .` 无命中；`grep -rn "vLLM" .` 仅剩历史说明处
- `python -m compileall qicode/` 无输出错误
- `python -m pytest tests/ -v` 全绿
- `python -c "from qicode.config import ConfigLoader; print(len(ConfigLoader.load('config.example.yaml')))"` 输出与示例内 provider 数量一致
- tmux 重跑 T11 流程通过（含 Esc 打断与错误恢复）

### T14: 思考开关语义修正（真机验证发现的缺陷）

**背景：** 用 DeepSeek 的 Anthropic 端点做 T12.3 的真机验证时，暴露出原设计的两处问题——都不是「跑不起来」，而是「跑起来了但行为不对」，单测与本地 Ollama 都照不出来（Ollama 的 qwen2.5vl:7b 压根不会思考，所以不显现）。

**文件：** `qicode/provider/openai_compat.py`、`qicode/provider/anthropic_compat.py`、`qicode/config.py`、`tests/test_provider.py`、`tests/test_anthropic_provider.py`、`spec.md`、`plan.md`、`README.md`、`config.example.yaml`、`checklist.md`
**依赖：** T12、T13

> **踩坑（务必保留回归用例）**：原实现是「`thinking: true` 时才看 `thinking_params`，否则什么都不发」。真机受控对比（同一道推理题，DeepSeek 的 Anthropic 端点）：
>
> | 配置 | 实际发出去的参数 | 思考字符数 |
> |------|------------------|-----------|
> | `thinking: false` | 不发 | 146 |
> | `thinking: true` | `{adaptive, summarized}` | 211 |
> | `thinking: true` + `{type: disabled}` | `{type: disabled}` | 0 |
> | `thinking: false` + `{type: disabled}` | **不发** | 178 |
>
> 两个后果：**(a)** `thinking: false` 的实际语义只是「不主动请求开启」，服务端仍按自身默认值思考（DeepSeek 默认按需思考，Anthropic 官方 Opus 5 / Fable 5 默认开启）——**AC14 按字面不成立**；**(b)** 用户显式写的参数被**静默丢弃**，配置里明明写了却不起作用、还没有任何提示，这违背本项目「报错要指向具体字段、不静默忽略」的一贯做法。
>
> 另注：早期我曾用「1+1」这种平凡问题测 `thinking: false`，得到 0 个思考增量，据此写下「开关确实被服务端尊重」——**该结论是错的**，换需要推理的问题就露馅了。教训：验证开关类行为必须用能触发该行为的问题。

**步骤：**
1. 两个适配器统一改为：显式写了 `config.thinking_params` → **无条件使用**；否则 `thinking: true` → 适配器默认值；两样都没有 → 不发该参数
2. `config.py` 补 `thinking` 与 `thinking_params` 的字段语义说明与四种组合规则（`TEMPLATE` 同步）
3. 单测各补一条「开关为假 + 显式写了关闭参数 → 参数确实进了请求体」
4. 文档同步：spec 的 F1/F5 与 AC14 重述、plan 的 D3/D13 与两处设计说明、README 与 `config.example.yaml` 的用法说明

**验证：**
- `python -m pytest tests/ -q` 全绿（122）
- 真机复验（DeepSeek 的 Anthropic 端点）：`thinking: false` + `thinking_params: {type: disabled}` → **0 个思考字符**（修复前同一配置 178）；同题只写 `thinking: false` 时 187，对比成立
- AC14 按重述后的口径通过


### T15: 输入机制统一（provider 选择改走 prompt_toolkit）

**背景：** 在真实终端里发现启动后偶发多出一个空提示符 `›`。排查时确认了两件事：① 多出的 `›` 就是「提交了一个空行」，主循环对空行是 `continue`，因此无害、不进历史；② **原把它归因于「`input()` 与 prompt_toolkit 混用留下残留换行」的结论并未被验证实**——用 tmux 复现时两种机制表现完全一致，区分不开；该现象更可能只是用户多按了一次回车（实测：一次回车 = 一个 `›`，两次回车 = 两个 `›`）。但这暴露了一个真实的隐患：一个进程里同时存在两套输入机制。

**文件：** `qicode/tui/selection.py`（新建）、`qicode/tui/__init__.py`、`qicode/cli.py`、`plan.md`
**依赖：** T9

**步骤：**
1. 新建 `qicode/tui/selection.py`：把「打印编号列表 + 读入选择 + 非法重问 + Ctrl+C/D 放弃」整块从 cli 挪进来，输入改用 `PromptSession().prompt(...)`
2. `cli.py` 删掉 `input()`，改为调 `select_provider`；`-p` 的校验仍留在 cli（那是编排逻辑不是交互）
3. `tui/__init__.py` 导出 `select_provider`
4. plan.md 的模块职责同步：输入整体归 tui，cli 不再自己做输入

**说明：** 这是**消除隐患**的改动，不是那个 `›` 现象的补丁（按两次回车照样是两个 `›`，那是空行提交的正常行为）。收益是：一个进程只剩一套输入机制，且输入按 plan.md 的划归回到 tui 层。

**验证：**
- `python -m pytest tests/ -q` 全绿（122）
- tmux 实跑：选择后进入 `›`，能正常对话；`-p` 路径不受影响


### T16: 界面呈现增强（启动 banner / 生成指示 / 状态栏 / 输入框边框）—— **已完成 16.1–16.4**

> 实施记录（2026-09-22）：四步全部完成并逐项真机验证（见 checklist「界面呈现」小节）。
> 与初版方案的偏差一处：**吉祥物改成了「带背景色的空格」拼的像素画**，不用线条画也不用 `█`。
> 起因是拿实物与 Claude Code 的实心 logo 对比后发现，小尺寸下线条画显得又细又廉价，档次差一截；
> 而 `█` 这类实心块字符在部分终端会在 1 格/2 格间摇摆、会让整图歪掉。
> 带背景色的空格宽度恒为 1 格，既拿到实心块的分量感又没有宽度风险（理由写在 `banner.py` 的模块 docstring）。
> **重写记录（T16 ④ 输入区，第三次返工定案）**：`PromptSession` 换成了自搭的**非全屏 `Application`**。
> 前两次失败的原因：① `bottom_toolbar` 画在**屏幕最底**（文档原话 "displayed at the bottom of the screen"），而 `PromptSession` 把输入行画在**光标处**，两者天生不在一起，屏幕空着时输入行就停在半空——居居两次指出「输入跑到中间」；② 换成 `rprompt` 又污染回滚（状态在历史里重复出现三遍）。
> 定案做法：`HSplit` 第一个窗口**不指定高度**充当弹性占位，把「分隔线 / 输入行 / 状态行」三行顶到屏幕底部；`full_screen=False` 走正常屏幕，回滚历史照常保留（与 Claude Code 同此）。可行性是**先做最小实验验证过**才动的手。
> 又两处返工：① **横线铺不满宽度**——`shutil.get_terminal_size()` 在 prompt_toolkit 接管 stdin 后会退回 `(80, 24)` 兜底值（实测确实返回 80），终端越宽缺口越明显；改用 `app.output.get_size().columns`。
> ② **状态行每轮都留在对话记录里**——app 退出时会把整个输入区留在屏幕上，于是 `▶▶ provider · model` 在记录里重复出现一遍又一遍。改用 `Application(erase_when_done=True)` 让退出时擦掉输入区，再由 `_read_line` 补一条 `❯ 你问的话` 进对话流：记录干净，用户问过什么也照样留得下。
>
> 另踩一个坑记下来：key binding 里**不能直接 `raise`**，prompt_toolkit 会自己接住并弹「Exception / Press ENTER to continue...」，异常传不到主循环（Ctrl+D 按下去不退就是这个）；必须用 `event.app.exit(exception=...)`。
> 回归全部重跑：Esc 打断、`/exit`、`Ctrl+D`、`Ctrl+C×2` 退出码均 0、单次 Ctrl+C 只清行、上键翻历史、`/provider` 切换后状态实时更新、回滚历史可翻。
>
> **返工记录（T16 ③ 状态栏）**：初版用 `bottom_toolbar` 做状态栏，结果**光标在屏幕上方、状态在屏幕最底，中间空一大片**——居居一眼看出「输入的位置不在输入框里」。
> 根因是我用错了 API：prompt_toolkit 的 `bottom_toolbar` 文档原话是「displayed at the bottom of the screen」，它画在**屏幕最底**，压根不是「输入行下面」。
> 改用 `rprompt`（右对齐挂在 prompt 第一行）后又踩第二次坑：**横线占满整宽，rprompt 没位置被整个藏掉**。修法是横线按状态宽度截短、给它留位，宽度每次现算（用 `rich.cells.cell_len`，中文按显示宽度算）。
> 结论：状态栏贴在输入行右侧，与光标不再脱节。**与 Claude Code 的形制仍有差别**——他是把整个输入区钉在屏幕底部（全屏式布局），代价是放弃终端自身的回滚历史，与 F2「历史可向上翻阅」冲突，因此不采用。
>
> 另：吉祥物比例调过一轮——初版脸是 6 格宽 × 2 行高（终端一格约 1:2，折合 6×4），
> 看着又宽又扁、像猫不像人；现为 8 格宽 × 4 行高（折合 8×8），头发只在两侧各留 2 格。

**背景：** 原始需求是「仿 Claude Code 的交互式 TUI」，但当时的 spec F2 只约束了功能（能输入、能流式、能打断），**没有一条约束外观**，交付成了朴素的 `› ` 行式 REPL。补记见 plan.md「模块四附」与 spec.md 的 AC21–AC24。

**文件：** `qicode/tui/banner.py`（新建）、`qicode/tui/selection.py`、`qicode/tui/repl.py`、`qicode/tui/renderer.py`、`tests/test_renderer.py`、`tests/test_repl.py`、`spec.md`、`plan.md`、`checklist.md`
**依赖：** T15

**步骤（顺序即实施顺序，前两步风险最低）：**

*16.1 启动 banner（AC21）*
1. 新建 `qicode/tui/banner.py`：渲染「程序名 + 版本 + provider + model + 工作目录」
2. 由 cli 在进入 REPL 前调用一次（选完 provider 之后），**明确打出「已进入对话」这类字样**，消掉「还在选 provider」的混淆
3. 窄终端降级：宽度不足时只印一行纯文本，不印图形字符

*16.2 首 token 前的生成指示（AC23）*
4. `StreamRenderer` 增加「尚未收到任何增量」的状态：此时 `_renderable` 返回 spinner + `<model> 正在生成… (N s)`，而非空 Markdown
5. 动画与秒数靠 `refresh_per_second` 的兜底心跳驱动（**这正是当初保留兜底帧率的用处**），不依赖增量
6. 首个增量到达后**无缝切换**为现有的「思考块 + 正文」，不闪、不重排
7. 单测：假 Live 下，未收到增量时 renderable 是生成指示；收到首个增量后立即变成正文（用现成的假 Live 断言 renderable 的形态变化）

*16.3 输入态底部状态栏（AC22）*
8. `PromptSession(bottom_toolbar=...)` 显示 `<provider> · <model>`
9. ⚠️ 生成期底栏不可见是**刻意取舍**（常驻需全屏模式、会牺牲回滚历史，见 plan.md），不要为了「常驻」改用 `screen=True`
10. `/provider` 切换后底栏要跟着更新（状态栏回调读 session 当前值，别缓存）

*16.4 输入框边框（AC24，最后做）*
11. prompt_toolkit 多行 prompt + `Frame`，`❯` 作提示符
12. ⚠️ 这一条动输入层，与 termios 切换、Ctrl+C 处理同区域（T6 踩坑处）——做完必须重跑 AC8 与 AC16

**验证：**
- `python -m pytest tests/ -q` 全绿
- tmux 实跑：AC21（含 60 列窄终端）、AC23（用本地 Ollama 出明显首 token 延迟）、AC22（切换后底栏更新）、AC24（边框 + Esc + 三条退出路径）
- **既有 39 项全量重跑**，重点盯 AC5（逐字不被 spinner 切换破坏）、AC8、AC16、N1（不闪烁、不重排）


### T17: 进去之后再选模型（去掉启动菜单 + `/model` 面板）

**背景：** 原设计是启动时弹菜单让用户选 provider（spec F1 原文）。实际用起来两个问题：① 拦在门口多一步；② **菜单内容会一直留在对话记录最上面**——「开始对话之后这个还在」。改为直接进对话、默认用配置里第一个 provider，换模型在对话里走 `/model`。

**文件：** `qicode/tui/selection.py`（重写）、`qicode/cli.py`、`qicode/tui/repl.py`、`qicode/session.py`、`qicode/tui/__init__.py`、`tests/test_repl.py`、`spec.md`、`plan.md`、`checklist.md`
**依赖：** T16

**步骤：**
1. `selection.py` 从「启动选择器」重写成「`/model` 面板」：标题 + 说明 + 选项列表（`❯` 光标、方向键移动、回车选中、Esc 取消）+ 底部按键提示。用 `Application(erase_when_done=True)`，选完面板自身不留进对话记录
2. 列对齐用 `rich.cells.cell_len` 按**显示宽度**算，不能用 `len`（中文一个字占两格，会错位）
3. `cli.py` 去掉交互选择：`-p` 指定则校验，否则**取配置里的第一个**；删掉对 `select_provider` 的引用
4. `repl.py` 加 `/model`（取消时什么都不做）；未知命令提示里补上 `/model`。**原 `/provider` 删除**，其「按名直接切」的能力并入 `/model <name>`
5. `session.py` 加 `provider_configs()`（返回副本）与 `current_model()`，供面板展示与状态栏用
6. 文档同步：spec 的 F1/F6/AC1 重写 + 新增 AC25；plan 的模块职责 + D16

**追加（同日）：** ① 面板最初没有弹性占位，从光标处往下画，光标靠近底部时会把上方对话内容顶上去（「选择模型的时候会挤上去」）——加弹性占位后贴底显示；② **原 `/provider` 并入 `/model`**（不带参数弹面板、带参数直接切），两套命令做同一件事只会让人犹豫用哪个。

**验证：**
- `python -m pytest tests/ -q` 全绿（127）
- tmux 实测：启动**不再弹菜单**直接进对话、默认 `my-ollama`；`/model` 面板列出两项、方向键移动光标、回车切到 `my-deepseek` 且状态栏随之更新、面板被擦除；**Esc 取消后 provider 不变**
- 回归：对话、上键历史、Esc 打断、`/exit`、`Ctrl+D`、`Ctrl+C×2` 退出码全 0；`/model <name>` 直接切换可用（未知名字提示「未找到模型」、同名提示「当前已经是」）、`/provider` 已不存在（敲了走「未知命令」提示）


### T18: 进入前擦干净整屏（AC21 前半段）

**背景：** T16/T17 做完后，横幅上方仍留着启动那行的 shell 提示符——`(Qicode) wanzg …/Qicode main !? v3.12.0 Qicode 16:12 ❯ qicode`。居居看图后指出：「进去之后，其他的都要没有才对」。长提示符 + 命令行本身挂在那儿，观感上就像「还在命令行里、没进去」，把 T16 那行「已进入对话」的效果抵消掉了。

**文件：** `qicode/cli.py`、`tests/test_cli.py`（新建）、`spec.md`、`plan.md`、`checklist.md`

**步骤：**
1. `cli.py` 新增模块级 `_clear_screen(console)`：非终端直接返回（免得把重定向输出污染成乱码），终端则写 `ESC[2J`（清可见区）+ `ESC[3J`（清回滚缓冲）+ `ESC[H`（光标归位）
2. 在 `_run` 里**擦屏紧挨横幅、且在它之前**调用——两件事是一套的，只打横幅不擦屏等于没做
3. 顺手更正 spec.md AC22 里那句已被实测证伪的理由（「常驻底栏需要全屏模式、会牺牲回滚历史」），以及 spec.md AC21 补上擦屏要求
4. 单测三连：终端写全序列 / 三段顺序正确（`2J` < `3J` < `H`）/ 非终端一个字节都不写

**验证：**
- `python -m pytest tests/ -q` 全绿（130）
- tmux 实测（100×30）：启动后**第 1 行就是 `Qicode 0.1.0`**，启动行完全不见；`capture-pane -S -100`（**含回滚缓冲**）里搜 `python -m qicode`／`(Qicode)`／`wanzg` 均无结果——可见区与回滚缓冲都擦掉了
- 回归：对话正常、`/exit` 退出码 0、屏幕正常交还 shell


## 执行顺序

```
T1 → T2 → T3 → T4 → T5
                      ↘
            T6 ─┐（可并行）
            T7 ─┤
                → T8 → T9 → T10 → T11 → T12 → T13 → T14 → T15 → T16 → T17 → T18
```

提交节奏：T2/T4/T5（各模块单测全绿后）、T8（tui 完成）、T11（集成冒烟通过）、T13（新增协议 + 清理复验）各提交一次。

实施记录（2026-09-22）：T1–T11 已完成并逐模块验证；T6 的监听线程与 T8 的错误通路在端到端测试中各暴露过一个缺陷，均已修复并补了回归用例。T12（双协议 + 可配置思考参数）、T13（清理与复验）已完成。T14 是在 T12.3 真机验证（DeepSeek 的 Anthropic 端点）中发现的思考开关语义缺陷，已修复；同一次真机验证还补齐了 AC10/AC13/AC18 与场景 B/E——这些原本因「本机没有支持思考的后端、也没有 Anthropic key」而挂着。
