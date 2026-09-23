# v2 工具系统 Tasks

> 基于已批准的 [spec.md](spec.md) + [plan.md](plan.md)。任务有序，每步留绿
> （`python -m qicode` 可启动 / `pytest` 通过 / `ruff check` 无告警）。
> 验证一律「先跑命令看输出，再下结论」。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `src/qicode/llm/__init__.py` | 新增 ToolCall/ToolResult/ToolDefinition/ROLE_*/tool_input；扩展 Message/StreamEvent；Provider.stream 加 tools、Provider 加 supports_tools |
| 修改 | `src/qicode/llm/anthropic_provider.py` | supports_tools、注入 tools、stream 解析 tool_use blocks、tool_use/tool_result 回灌 |
| 修改 | `src/qicode/llm/openai_provider.py` | supports_tools、注入 tools、按 index 拼 tool_calls、assistant.tool_calls/tool 消息回灌 |
| 新建 | `src/qicode/tool/__init__.py` | Tool Protocol、Result、Registry、new_default_registry、DEFAULT_TIMEOUT、_truncate |
| 新建 | `src/qicode/tool/{read_file,write_file,edit_file,bash,glob_tool,grep_tool}.py` | 6 个核心工具 |
| 新建 | `tests/test_tool.py` | 注册中心 + 各工具单测 |
| 新建 | `src/qicode/agent/__init__.py` | Agent、Event、ToolEvent、Phase、run（单轮闭环） |
| 新建 | `tests/test_agent.py` | fake provider 驱动单轮闭环（AC8/AC9）+ 取消穿透 |
| 修改 | `src/qicode/conversation.py` | add_assistant_with_tool_calls、add_tool_results |
| 修改 | `src/qicode/prompt.py` | `system_prompt` 增 Agent 角色与工具约定 |
| 修改 | `src/qicode/tui/app.py` | 接 registry；`_consume_agent_events`；工具事件分派；thinking 提示 |
| 修改 | `src/qicode/tui/view.py` | `ToolBlock`；`streaming_body` 增加工具执行形态 |
| 删除 | `src/qicode/tui/stream.py` | `consume` 由 `Agent.run` 取代，`TICK_INTERVAL` 迁至 `app.py` |
| 删除 | `tests/test_tui_stream.py` | 随 `stream.py` 一起走，覆盖搬到 `test_agent.py` |
| 修改 | `tests/test_tui_app.py` | `QicodeApp` 构造签名变了；补齐工具事件分派用例 |
| 修改 | `src/qicode/cli.py` | 构造 new_default_registry 注入 QicodeApp |

---

## T1: 扩展 llm 协议无关类型

**文件：** `src/qicode/llm/__init__.py`
**依赖：** 无
**步骤：**
1. 新增 `import json`（如未导入）。
2. 增加角色常量 `ROLE_USER = "user"` / `ROLE_ASSISTANT = "assistant"` / `ROLE_TOOL = "tool"`。
3. 新增 dataclass：`ToolCall(id: str, name: str, input: str)`、
   `ToolResult(tool_call_id: str, content: str, is_error: bool = False)`、
   `ToolDefinition(name: str, description: str, input_schema: dict[str, Any])`（各带中文 docstring）。
4. 新增模块级函数 `tool_input(call: ToolCall) -> dict[str, Any]`：`json.loads(call.input)`，
   非 dict 或抛 `ValueError` 一律返回 `{}`（N4：回灌路径不许抛）。
5. 给 `Message` 增字段 `tool_calls`/`tool_results`（`field(default_factory=list)`），
   `role` 字面量扩为 `Literal["user", "assistant", "tool"]`，`content` 给默认值 `""`。
6. 给 `StreamEvent` 增字段 `tool_calls: list[ToolCall] = field(default_factory=list)`；
   docstring 更新为四态语义说明（text / tool_calls / done / err）。

**验证：** `python -c "from qicode.llm import ToolCall, ToolResult, ToolDefinition, ROLE_TOOL, Message, StreamEvent, tool_input; print(Message(role='tool').tool_results, tool_input(ToolCall('1','read_file','不是 json')))"` 输出 `[] {}`；`ruff check src/qicode/llm/__init__.py` 无告警。

## T2: tool 包骨架（Tool Protocol、Result、Registry、_truncate）

**文件：** `src/qicode/tool/__init__.py`
**依赖：** T1
**步骤：**
1. 定义 `@dataclass class Result(content: str, is_error: bool = False)`。
2. 定义 `@runtime_checkable class Tool(Protocol)`：`name() -> str` / `description() -> str` /
   `parameters() -> dict[str, Any]` / `async def execute(self, args: str) -> Result`。
3. 定义 `_truncate(s: str, max_lines: int, max_chars: int) -> str`：超出尾部追加 `\n[truncated]` 标注。
4. 定义 `class Registry`：
   - `__init__` 初始化 `_order: list[str] = []` / `_tools: dict[str, Tool] = {}`；
   - `register(t)`：按 `t.name()` 入表，**重名抛 `ValueError`**（本项目取「不静默覆盖」这条）；
   - `get(name) -> Tool | None`；
   - `definitions() -> list[ToolDefinition]`：按 `_order` 把每工具的 name/description/parameters
     组成 `ToolDefinition`；
   - `async def execute(self, name, args, timeout) -> Result`：`get` 未命中返回
     `Result(is_error=True, content=f"未知工具: {name}")`；命中则
     `try: return await asyncio.wait_for(tool.execute(args), timeout)`，
     `except TimeoutError: return Result(is_error=True, content=f"工具 {name} 执行超时（{timeout}s）")`，
     `except Exception as e: return Result(is_error=True, content=f"工具 {name} 异常: {e}")`。
5. 常量 `DEFAULT_TIMEOUT: float = 30.0`。**暂不写** `new_default_registry`。

**验证：** `python -c "from qicode.tool import Tool, Result, Registry, DEFAULT_TIMEOUT; print(Registry().definitions())"` 输出 `[]`；`ruff check src/qicode/tool/` 无告警。

## T3: read_file 工具

**文件：** `src/qicode/tool/read_file.py`
**依赖：** T2
**步骤：**
1. 定义 `class ReadFileTool` 实现 `Tool` Protocol。
2. `parameters()` 返回手写 schema：`{"type": "object", "properties": {"path": {"type": "string", "description": "要读取的文件路径"}}, "required": ["path"]}`。
3. `async def execute(args)`：空 args 当 `"{}"`；`json.loads` 失败、`path` 缺失 →
   `is_error`；`pathlib.Path(path)` 读取——`is_dir()` / 不存在 / `PermissionError` → `is_error`；
   成功 `text.splitlines()` 后按行加行号（`f"{n:6d}\t{line}"`），经 `_truncate` 限 2000 行 / 256KB。

**验证：** `python -c "import asyncio; from qicode.tool.read_file import ReadFileTool; print(asyncio.run(ReadFileTool().execute('{\"path\":\"pyproject.toml\"}')).content[:80])"` 出现行号；读不存在文件得 `is_error=True`（T9 后补单测）。

## T4: write_file 工具

**文件：** `src/qicode/tool/write_file.py`
**依赖：** T2
**步骤：**
1. `class WriteFileTool`。
2. `parameters()`：`path` 与 `content` 均必填。
3. `execute`：解析 `path` / `content`（注意 `content` 允许空串，判缺失要用 `"content" not in data`
   而不是 `not data.get("content")`）；`Path(path).parent.mkdir(parents=True, exist_ok=True)`
   后 `Path(path).write_text(content)`（覆盖）；成功返回
   `Result(content=f"已写入 {path}（{len(content.encode())} 字节）")`；`OSError` → `is_error`。

**验证：** `ruff check src/qicode/tool/write_file.py`；T9 后单测写嵌套路径检查磁盘。

## T5: edit_file 工具

**文件：** `src/qicode/tool/edit_file.py`
**依赖：** T2
**步骤：**
1. `class EditFileTool`。
2. `parameters()`：`path` / `old_string` / `new_string` 三字段必填，描述里写明唯一匹配语义。
3. `execute`：读文件失败 → `is_error`；`n = content.count(old_string)`；
   `n == 0` → `Result(is_error=True, content="未找到匹配的内容")`；
   `n > 1` → `Result(is_error=True, content=f"匹配到 {n} 处，old_string 不唯一，请提供更长上下文使其唯一")`；
   `n == 1` → `content.replace(old_string, new_string, 1)` 写回，返回成功（AC4 要的就是这三条文案**可区分**）。

**验证：** `ruff check src/qicode/tool/edit_file.py`；T9 后单测覆盖 0/1/多三情形。

## T6: bash 工具

**文件：** `src/qicode/tool/bash.py`
**依赖：** T2
**步骤：**
1. `class BashTool`。
2. `parameters()`：`command` 必填。
3. `execute`：`proc = await asyncio.create_subprocess_shell(cmd, stdout=PIPE, stderr=PIPE)`；
   `stdout_b, stderr_b = await proc.communicate()`（超时由 Registry 那层 `wait_for` 管，
   这里不重复套一层）；正常返回组装
   `f"exit_code: {proc.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"`，
   经 `_truncate(s, max_lines=10000, max_chars=30000)`；
   **`proc.returncode != 0` 时设 `is_error=True`**（spec F9 把非零退出算作执行失败），
   但 content 里 stdout/stderr/exit_code 一个不少，模型自己判断细节。

**验证：** `ruff check src/qicode/tool/bash.py`；T9 后单测 `echo hi` 与超时命令（注入极短 timeout 跑 `sleep 5`）。

## T7: glob 工具

**文件：** `src/qicode/tool/glob_tool.py`
**依赖：** T2
**步骤：**
1. `class GlobTool`。
2. `parameters()`：`pattern` 必填（如 `**/*.py`），`path` 可选（默认 `.`）。
3. `execute`：`root = Path(args.get("path") or ".")`；`root.glob(pattern)`（`**` 由 `pathlib`
   原生支持）；过滤出文件（非目录）；收集相对路径 `sorted` 后取前 100；
   循环中每 100 个 `await asyncio.sleep(0)` 让出 event loop；
   无匹配返回 `Result(content="无匹配")`（**非** `is_error`，见 spec F9）。

**验证：** `ruff check src/qicode/tool/glob_tool.py`；T9 后单测 `**/*.py` 能命中 `src/qicode/` 下文件。

## T8: grep 工具

**文件：** `src/qicode/tool/grep_tool.py`
**依赖：** T2
**步骤：**
1. `class GrepTool`。
2. `parameters()`：`pattern` 必填（Python 正则，描述里注明），`path` / `glob` 可选。
3. `execute`：`try: rx = re.compile(pattern) except re.error as e: return Result(is_error=True, content=f"正则非法: {e}")`；
   `root = Path(args.get("path") or ".")`；遍历 `root.rglob(glob or "*")`，
   对每个文件 `with open(file, errors="replace") as f: for lineno, line in enumerate(f, 1):`，
   命中则收集 `f"{file}:{lineno}:{line.rstrip()}"`；`OSError` / `UnicodeDecodeError` 跳过该文件；
   ≤100 命中，超出尾部标注；每文件结束 `await asyncio.sleep(0)`；
   `len(line)` 超过 1MB 时显式标注「该行过长，未完整搜索」，避免假「无命中」误导模型；
   无命中返回 `Result(content="无命中")`（**非** `is_error`）。

**验证：** `ruff check src/qicode/tool/grep_tool.py`；T9 后单测搜一个已知关键字命中。

## T9: new_default_registry 与 tool 单测

**文件：** `src/qicode/tool/__init__.py`、`tests/test_tool.py`
**依赖：** T3–T8
**步骤：**
1. `__init__.py` 增 `new_default_registry()`：依次 `register` 6 个工具（顺序即
   `definitions()` 的导出顺序），返回 `Registry`。
2. `tests/test_tool.py`：测 `definitions()` 返回恰好 6 条且名称有序（AC1）；重名 `register`
   抛 `ValueError`；`read_file` 存在/不存在/是目录；`write_file` 新建 + 嵌套路径（`tmp_path`）
   检查磁盘；`edit_file` 0/1/多三情形错误可区分；`bash` `echo hi` 与非零退出（都带完整输出）、
   超时（注入极短 timeout 跑 `sleep 5`）；`glob` `**/*.py`；`grep` 命中 + 无命中
   （无命中断言 `is_error is False`）。
3. **测试写法沿用 v1**：同步 `def test_...` 里 `asyncio.run(...)`，**不引入 `pytest-asyncio`**
   （v1 的 `test_tui_stream.py` / `test_llm_providers.py` 全是这个写法，见 plan 技术决策）。

**验证：** `pytest tests/test_tool.py -v` 全通过；输出确认 6 条定义、edit 三情形文案不同。

## T10: Provider.stream 加 tools + supports_tools（注入定义，暂不解析）

**文件：** `src/qicode/llm/__init__.py`、`src/qicode/llm/anthropic_provider.py`、`src/qicode/llm/openai_provider.py`、`src/qicode/tui/stream.py`
**依赖：** T1
**步骤：**
1. `__init__.py`：`Provider.stream` 签名改为
   `stream(self, msgs: list[Message], tools: list[ToolDefinition]) -> AsyncIterator[StreamEvent]`；
   `Provider` 增加 `supports_tools` 属性；更新 Protocol docstring。
2. `anthropic_provider.py`：`stream` 加 `tools` 形参；新增 `_to_anthropic_tools(tools)` 转
   `[{"name", "description", "input_schema"}]` 并设入请求参数；新增 `supports_tools` 返回
   `not self._cfg.thinking`；流解析暂不变。
3. `openai_provider.py`：同理，新增 `_to_openai_tools(tools)` 转
   `[{"type": "function", "function": {"name", "description", "parameters"}}]` 入参；
   `supports_tools` 返回 `True`。
4. `tui/stream.py`：`consume` 里 `provider.stream(msgs)` 暂改为传 `[]` 第二参数
   （T16 会用 `Agent.run` 整个替掉这个模块）。
5. `tests/test_llm_providers.py`、`tests/test_tui_stream.py` 里的假 provider 补上
   `supports_tools` 属性与 `tools` 形参。

**验证：** `pytest tests/test_llm_providers.py tests/test_tui_stream.py -v` 通过；
`python -m qicode` 发一条纯文本仍正常（工具定义已随请求发送，模型未必调用）；
`ruff check src/qicode/llm/` 无告警。

## T11: anthropic 适配器解析工具调用 + 回灌

**文件：** `src/qicode/llm/anthropic_provider.py`
**依赖：** T10
**步骤：**
1. 流循环沿用 v1 的 `async with self._client.messages.stream(**params) as stream:`；
   `event.type == "content_block_delta"` 时：`text_delta` → `yield StreamEvent(text=...)`；
   `thinking_delta` / `input_json_delta` 跳过（SDK 内部已累加 input JSON）。
2. 流正常结束后：`final_message = await stream.get_final_message()`；若
   `final_message.stop_reason == "tool_use"`，遍历 `final_message.content`，
   对 `block.type == "tool_use"` 收集
   `ToolCall(id=block.id, name=block.name, input=json.dumps(block.input))`；
   非空则 `yield StreamEvent(tool_calls=calls)`；随后 `yield StreamEvent(done=True)`。
3. `_to_anthropic_messages` 扩展：assistant 有 `tool_calls` 时 content 用数组
   `[{"type": "text", "text": preamble}] + [{"type": "tool_use", "id": c.id, "name": c.name,
   "input": tool_input(c)} for c in calls]`（`tool_input` 而不是裸 `json.loads`，见 T1）；
   `ROLE_TOOL` 消息把每个 `ToolResult` 用
   `{"type": "tool_result", "tool_use_id": r.tool_call_id, "content": r.content,
   "is_error": r.is_error}` 拼成一条 `{"role": "user", "content": [...]}`。
   （Anthropic 的 tool_result 走 user 消息，这是协议规定，不是笔误。）
4. 补一条单测：`to_*_messages` 的转换结果对三种回合（纯文本 assistant / 带 tool_calls 的
   assistant / ROLE_TOOL）各断言一次；含工具历史的请求**不带** `thinking` 字段。

**验证：** `pytest tests/test_llm_providers.py -v` 通过；`ruff check src/qicode/llm/anthropic_provider.py` 无告警。
（真机那条验不了——手上没有 anthropic 密钥，与 v1 相同。）

## T12: openai 适配器解析工具调用 + 回灌

**文件：** `src/qicode/llm/openai_provider.py`
**依赖：** T10
**步骤：**
1. 流循环维护 `tool_calls_buf: dict[int, dict[str, str]]`（按 `delta.tool_calls[i].index` 累加）：
   `if tc.id: buf[idx]["id"] = tc.id`、`if tc.function.name: buf[idx]["name"] = tc.function.name`、
   `if tc.function.arguments: buf[idx]["args"] = buf[idx].get("args", "") + tc.function.arguments`；
   正文 `delta.content` 仍 `yield StreamEvent(text=...)`。
2. 流结束后（`finish_reason == "tool_calls"` 或 buf 非空）：按 index 排序构造
   `ToolCall(id=v["id"], name=v["name"], input=v.get("args") or "{}")`，
   `yield StreamEvent(tool_calls=calls)`；再 `yield StreamEvent(done=True)`。
   **继续沿用 `async with` 包住 stream**（v1 已踩过：`AsyncStream.close()` 只在流被读完时
   才自动调用）。
3. `_to_openai_messages` 扩展：assistant 有 `tool_calls` 时发
   `{"role": "assistant", "content": preamble or None, "tool_calls": [{"id": c.id,
   "type": "function", "function": {"name": c.name, "arguments": c.input or "{}"}}]}`；
   `ROLE_TOOL` 消息每个 `ToolResult` 发一条
   `{"role": "tool", "tool_call_id": r.tool_call_id, "content": r.content}`。
4. 补单测：按 index 拼多工具调用的分片（两个工具交错分片，断言拼出两条完整 `ToolCall`）；
   空 arguments 归一为 `"{}"`；消息转换三种回合各断言一次。

**验证：** `pytest tests/test_llm_providers.py -v` 通过；`ruff check src/qicode/llm/openai_provider.py` 无告警。

## T13: conversation 扩展

**文件：** `src/qicode/conversation.py`、`tests/test_conversation.py`
**依赖：** T1
**步骤：**
1. 新增 `add_assistant_with_tool_calls(self, text: str, calls: list[ToolCall])`：
   `self._messages.append(Message(role=ROLE_ASSISTANT, content=text, tool_calls=list(calls)))`。
2. 新增 `add_tool_results(self, results: list[ToolResult])`：
   `self._messages.append(Message(role=ROLE_TOOL, tool_results=list(results)))`。
3. 保留现有方法不变（`messages()` 返回副本那条约定照旧）。
4. `tests/test_conversation.py` 补一条：依次 `add_user`、`add_assistant_with_tool_calls`、
   `add_tool_results`、`add_assistant` 后 `messages()` 长度 = 4、role 序列正确、
   `tool_calls`/`tool_results` 内容正确。

**验证：** `pytest tests/test_conversation.py -v` 通过。

## T14: agent 单轮闭环

**文件：** `src/qicode/agent/__init__.py`、`tests/test_agent.py`
**依赖：** T9, T11, T12, T13
**步骤：**
1. `agent/__init__.py`：定义 `Phase`(START/END)、`ToolEvent`、`Event`、`class Agent`、
   `__init__(provider, registry)`、`async def run(self, conv) -> AsyncIterator[Event]`
   （按 plan 的 run 算法 8 步）。`_stream_once(conv, defs)` 内部 helper：
   `async for se in self._provider.stream(conv.messages(), defs):` 转发 text 并累积
   preamble、收集 tool_calls。`ToolEvent.args` 取 `input` 截到 80 字符。
2. `tests/test_agent.py`：用实现 `Provider` Protocol 的 `FakeProvider`（带 `supports_tools`）
   编排三种脚本——
   (a) 请求#1 yield 1 个 ToolCall（`read_file` with `{"path": "..."}`）、请求#2 yield 文本
   「文件已读取」→ 断言 Event 序列含 `tool=START/END` 与最终 `text`、
   `conv.messages()` 末尾为 assistant 文本（AC8）；
   (b) 请求#1 yield 工具、请求#2 仍 yield 工具 → 断言只调用一次 `registry.execute`、
   不再触发执行（AC9）；
   (c) `supports_tools` 为 False 的 provider → 断言传给 `stream` 的 `tools` 为空。
   `FakeProvider` 内部用 `call_count` 切换脚本段；Registry 用真的 `new_default_registry()`
   或 fake 工具均可。
3. 把 v1 `tests/test_tui_stream.py` 里那条**取消能穿透 async generator** 的用例
   改写过来，覆盖 `Agent.run`——`stream.py` 要删，那份覆盖不能跟着一起丢。

**验证：** `pytest tests/test_agent.py -v` 全通过；输出确认单轮上限生效。

## T15: prompt 系统提示词扩展

**文件：** `src/qicode/prompt.py`
**依赖：** 无
**步骤：**
1. 扩写 `system_prompt(provider_name, model)`：在 v1 那句身份说明之后，补上「你是一个能使用
   工具的 Agent，可以读写改文件、执行命令、按模式找文件、搜代码内容；需要事实或要动手时
   先调用相应工具，拿到结果再给简洁答复」。**别把六个工具的参数说明抄进来**——那些由
   工具定义本身带给模型，重复一遍只会白占上下文。

**验证：** `pytest tests/test_prompt.py -v` 通过；`ruff check src/qicode/prompt.py` 无告警。

## T16: tui 接入 agent + ToolBlock

**文件：** `src/qicode/tui/app.py`、`src/qicode/tui/view.py`、删除 `src/qicode/tui/stream.py`
**依赖：** T14, T15
**步骤：**
1. `view.py`：新增 `ToolBlock(Horizontal)`，与 `ReplyBlock` 同构（左列圆点、右列正文）：
   - `show_running(name, args)`：正文只显示 `name(args)`（加粗）；
   - `show_result(name, args, summary, is_error)`：正文为 `Group(调用行, 缩进的 ⎿ 结果摘要)`，
     摘要 `dim`、错误 `red`，UI 侧截断 ~8 行；
   - 圆点用 `bold cyan`（助手回复是 `bold`），DEFAULT_CSS 照抄 `ReplyBlock` 那两条宽度规则。
2. `view.py`：`streaming_body(reply, elapsed)` 增加一种形态——执行工具时返回
   `Text.assemble((f"{frame} ", DIM), (f"{name}({args}) Running…", ""))`，转轮仍用
   `SPINNER_FRAMES`，不新增第二套动画。
3. `app.py`：`QicodeApp.__init__(self, providers: list[ProviderConfig], registry: Registry)`，
   存 `self._registry`；新增 `self._cur_tool: ToolBlock | None = None`。
4. `app.py`：把 `TICK_INTERVAL` 从 `stream.py` 迁过来（它只是界面刷新节奏）。
5. `app.py`：`submit` 改成 `asyncio.create_task(self._consume_agent_events())`；
   `_consume_agent_events` 内部构造 `Agent(self.provider, self._registry)` 后
   `async for ev in agent.run(self.conv):` 按 plan 那张分派表处理——
   - `ev.text`：没有回复块就先 `_start_reply_block()`；`cur_reply += ev.text`；`_refresh_streaming()`；
   - `ev.tool` START：`cur_reply` 非空则定型当前块，为空则把空块从对话区摘掉；
     然后 `_start_tool_block()` 并 `show_running(...)`；
   - `ev.tool` END：`self._cur_tool.show_result(...)`；清 `self._cur_tool`；
   - `ev.done`：定型最终答复 + `_end_turn()`；
   - `ev.err`：`_finish_with_error(ev.err)`（v1 的 `_safe_message` 脱敏照走）。
6. `app.py`：**删掉 `_finish_with_assistant` 里的 `self.conv.add_assistant(reply)`**——
   历史改由 agent 写，TUI 只画。空回复的处理也跟着交给 agent 的单轮提示占位。
7. `app.py`：`on_mount` 里若 `provider.supports_tools` 为假，在对话区打一行
   「当前配置开启了 thinking，本阶段工具暂不可用」。
8. 删除 `src/qicode/tui/stream.py` 与 `tests/test_tui_stream.py`（覆盖已搬到 T14）；
   更新 `tests/test_tui_app.py`：构造签名多加一个 registry，补工具事件分派的用例
   （至少覆盖「preamble → 工具行 → 最终答复」三个块依次出现）。

**验证：** `pytest -v` 全通过；`python -m qicode` 启动正常、纯文本对话不回归；
`ruff check src/qicode/tui/` 无告警。

## T17: cli 接线

**文件：** `src/qicode/cli.py`
**依赖：** T16
**步骤：**
1. `from qicode.tool import new_default_registry`；构造 `registry = new_default_registry()`；
   `QicodeApp(cfg.providers, registry).run()`。
2. `_replay_transcript` 顺带补上工具行的回放：`transcript(messages)` 现在要能认出
   `ROLE_TOOL` 回合（画成 `⎿ 结果摘要`），否则退出回放会把工具结果整段当正文印出来。

**验证：** `pytest tests/test_cli.py -v` 通过；`python -m qicode` 在合法配置下能启动并进入对话。

## T18: 全量验证与端到端冒烟

**文件：** 无（验证）
**依赖：** T1–T17
**步骤：**
1. `ruff format --check .`；`ruff check .`；`pytest -v`；`mypy src/qicode/`。
2. 用 `.qicode/config.yaml`（openai 兼容端点）跑：问「读 docs/v2/spec.md 并用一句话总结」
   → 观察工具行 `● read_file(docs/v2/spec.md)` + 缩进结果摘要 + 最终答复（AC8/AC11）。
3. 触发各错误：读不存在文件、edit 匹配不到、bash 非零退出、bash 超时 →
   结构化回灌、UI 可区分、程序不退出（AC12）。
4. 触发体量上限：读一个 >2000 行的文件、跑一条长输出命令、grep 一个高频词（AC13）。
5. 验单轮上限：让它「先读 A 再读 B」——应当在第一次工具后停下并给出答复（AC9）。
6. 用 tmux 验：对话区里 `●` 工具行与正文不打架、宽窄缩放下不错位、
   `/exit` 之后工具行随会话一起重放到终端回滚缓冲（AC11）。
7. （可选）若有 anthropic 配置，重复步骤 2 验证跨协议一致（AC10）——
   目前**验不了**，手上没有 anthropic 密钥。

**验证：** 全部命令通过、端到端链路与错误恢复符合预期。

## T19: 交付后硬伤修复

**文件：** `src/qicode/tool/{bash,edit_file,grep_tool,read_file,write_file}.py`、
`tests/test_tool.py`、`docs/v2/checklist.md`
**依赖：** T18
**背景：** v2 交付后做了一次三层代码审查（工具层 / 适配器层 / TUI 层，各由一个独立子代理
跑），查出三个硬伤——**会丢数据、会挂死进程**的那类，不是风格问题。所以这一条不是加功能，
而是**把已经勾上的条目修到名副其实**：这轮发现 checklist 里有 5 条当初的证据只覆盖了
顺利路径。成因、修法、反向证明见 `docs/v2/checklist.md` 末尾「T19 硬伤修复记录」。

**步骤：**
1. `bash` 超时杀不干净 → `start_new_session=True`（`setsid()`，让 `sh` 当进程组组长）
   + 超时时 `os.killpg(SIGKILL)` 杀**整个进程组**。管道 / 链式命令不再永久挂死。
2. `edit_file` 静默改坏文件 → 改成 `read_bytes` → **严格** UTF-8 解码（解不开就拒绝，
   不猜编码）→ 两遍匹配（先 `\n`，不中再试 `\r\n`）→ 替换时对齐原文行尾 → `write_bytes`。
3. `grep` 灾难性回溯冻死界面 → `SIGALRM` 给每次匹配掐 1 秒表（`MATCH_BUDGET`），
   搜索期间装处理器、结束**还原**；Windows / 非主线程降级为「不掐表」。
4. 顺手统一几条读路径的编码参数：`read_file` / `write_file` / `grep` 一律写死
   `encoding="utf-8"`（不跟随 locale），`write_file` 加 `newline=""`（不做换行转换）。
5. 每条硬伤都要有**反向证明**：撤掉修复逻辑后重跑新用例，确认它确实失败——否则无法
   排除「这个用例本来就过」。
6. 重跑四道门禁，回头核 `checklist.md`：把证据覆盖面不足的 5 条按**更宽的输入空间**
   重跑（管道、CRLF、非 UTF-8、恶意正则、大文件字节比对）并回填。

**验证：** 三个硬伤在真终端复现不再出现、反向证明成立、`ruff check .` /
`ruff format --check .` / `pytest`（**249 passed**）/ `mypy src/qicode/` 全绿。

## 执行顺序

```
T1 ─┬─ T2 ─┬─ T3 ─┐
    │       ├─ T4 ─┤
    │       ├─ T5 ─┼─ T9 ─┐
    │       ├─ T6 ─┤      │
    │       ├─ T7 ─┤      │
    │       └─ T8 ─┘      │
    ├─ T10 ─┬─ T11 ──────┤
    │        └─ T12 ─────┤
    ├─ T13 ──────────────┤
    └─ T15               │
                T9,T11,T12,T13 ─→ T14 ─→ T16 ─→ T17 ─→ T18 ─→ T19
                                   T15 ──┘
```
