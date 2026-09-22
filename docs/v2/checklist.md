# v2 工具系统 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为；括号内为验证方式与对应需求。
> 编码前定稿，编码后逐条回填证据：**只有拿到证据才勾**。
> 证据分三类——① pytest 用例 ② 真终端实跑 ③ 两者都有；需要 anthropic 真密钥的
> 条目一律留空并写明原因（与 v1 相同，手上只有 OpenAI 兼容侧的密钥）。
>
> **定稿时预判会留空的两条（「两协议一致」、「场景 5」）最后都勾上了**，因为 DeepSeek
> 同时提供 anthropic 与 openai 两个兼容端点，可以拿同一个 key、同一个模型做干净的
> A/B（见文末「跨协议 A/B」）。**没有任何一条空着**——但「官方 `api.anthropic.com`」
> 这个缺口仍然存在，写进了「已知未覆盖」，请连同那一段一起看。
>
> **回填时间：2026-09-22（T18）。** 真终端实跑一律用 tmux（120×40，中途缩到 72×30 验
> 宽窄缩放）。跑过三个工作目录，配置都在 `/tmp/` 下（密钥取自 `/tmp/ds_key`）：
>
> | 目录 | protocol | base_url | 用途 |
> |---|---|---|---|
> | `/tmp/qicode_e2e` | `anthropic` | `api.deepseek.com/anthropic` | 主场景（含 2500 行 `big.txt`、`notes.txt`、指向仓库 `docs/` 的符号链接） |
> | `/tmp/qicode_e2e_openai` | `openai` | `api.deepseek.com/v1` | 跨协议 A/B 的 openai 侧 |
> | `/tmp/qicode_think` | `anthropic` + `thinking: true` | 同上 | 验 `supports_tools` 的界面提示 |
>
> **没有**用仓库里那份 `.qicode/config.yaml`，从头到尾没读它（只 grep 过 `name` /
> `protocol` / `model` / `base_url` 这类结构行）、没改它。

## 实现完整性

- [x] 注册中心导出 6 条工具定义且按名可查（验证：`pytest tests/test_tool.py -k registry`，断言 `definitions()` 长度 == 6、名称有序、`get` 命中/未命中、重名 `register` 抛 `ValueError`）。(AC1/F1)
  **证据 ①：** `pytest tests/test_tool.py -k registry -v` → 8 passed（六个工具按注册顺序导出、`get` 命中/未命中、重名抛 `ValueError`、`execute` 的透传/未知工具/超时/意外异常四条路径）。
- [x] read_file 带行号读出内容；读不存在 / 是目录 / 参数非法各返回结构化错误（验证：单测 + 手测读 `docs/v2/spec.md` 见行号、读不存在文件得 `is_error`）。(AC2/F2)
  **证据 ③：** ① `test_read_file_numbers_lines` / `_missing_file` / `_on_directory` / `_reports_bad_arguments`（4 组参数）全绿；② tmux 实跑 `读 docs/v2/spec.md` → `⎿      1      # v2 工具系统 Spec`，行号在位；实跑 `read_file pyproject.toml`（该目录下不存在）→ `⎿ 文件不存在: pyproject.toml`。
- [x] write_file 创建/覆盖文件，父目录自动创建（验证：单测用 `tmp_path/"a/b/c.txt"` 后读回内容一致；另测 `content` 为空串时不被误判成缺参数）。(AC3/F2)
  **证据 ③：** ① `test_write_file_creates_nested_paths` / `_overwrites` / `_allows_empty_content` / `_requires_content_key`；② tmux 实跑「新建文件 out/hello.txt 写入内容 琪琪来啦」→ `⎿ 已写入 out/hello.txt（12 字节）`，`out/` 这个不存在的父目录被自动建出来。
- [x] edit_file 唯一匹配替换成功；0 处与 >1 处返回**可区分**错误（含匹配数）（验证：单测三情形，断言文案两两不同且 >1 那条含 N）。(AC4/F2)
  **证据 ③：** ① `test_edit_file_replaces_unique_match`、`test_edit_file_reports_distinguishable_errors[...未找到匹配]`、`[...匹配到 2 处]`、`test_edit_file_error_messages_differ_between_zero_and_many`；② tmux 实跑「把 notes.txt 里的『不存在的这句话』改成『新值』」→ `⎿ 未找到匹配的内容。`old_string` 必须与文件内容逐字符一致（包括缩进和换行），请先 read_file 确认原文。`
- [x] bash 返回 stdout/stderr/退出码；**非零退出是 `is_error` 但内容完整**；超时命令被终止并返回超时结果（验证：单测 `echo hi`、`false`、注入极短超时跑 `sleep 5`）。(AC5/F2/N1)
  **证据 ③：** ① `test_bash_returns_stdout_and_zero_exit` / `_nonzero_exit_is_error_but_keeps_output` / `_timeout_is_reported_and_kills_the_process`；② tmux 实跑 `ls -l ... && stat -c ...`（macOS 的 BSD stat 不认 `-c`）→ `⎿ exit_code: 1` + stdout、stderr 两段都完整回灌，模型据此解释了报错原因；实跑 `sleep 40` → `⎿ 工具 bash 执行超时（30 秒）`，`DEFAULT_TIMEOUT = 30.0` 生效。
- [x] glob 列出匹配文件；grep 返回 `file:line:content`（验证：单测 `**/*.py` 命中、关键字 grep 命中）。(AC6/F2)
  **证据 ③：** ① `test_glob_finds_files_recursively`、`test_grep_reports_file_line_and_content`、`test_grep_honours_glob_filter`；② 直接调 `registry.execute` 实跑 `grep 工具 docs/v2` → 首行 `checklist.md:1:# v2 工具系统 Checklist`，`file:line:content` 格式成立。
- [x] glob / grep **无命中是 `is_error=False`** 的正常结果，不是错误（验证：单测搜一个绝不存在的关键字，断言 `is_error is False` 且文案含「无命中」）。(F9 的边界)
  **证据 ③：** ① `test_glob_no_match_is_not_an_error`、`test_grep_no_match_is_not_an_error`；② 直接调工具实跑：grep `绝不存在的关键字zzz` → `无命中：没有任何文件内容匹配 ...`（`is_error=False`）；glob `**/*.py` on `docs/` → `无匹配：没有文件符合模式 ...`（`is_error=False`）。
- [x] 流式工具调用解析正确：模型一次回复的工具名与完整 JSON 参数被拼齐（验证：agent fake 单测断言 `input` 是完整 JSON；openai 侧另测两个工具**交错分片**能各拼各的；端到端发「读 X 文件」，工具行参数与请求一致）。(AC7/F4)
  **证据 ③：** ① `test_openai_accumulates_interleaved_tool_call_fragments`、`test_openai_output_order_follows_index_not_arrival`、`test_anthropic_yields_tool_calls_before_done`、`test_anthropic_finds_tool_calls_only_in_the_final_message`；② tmux 实跑里工具行的参数（`{"path": "docs/v2/spec.md"}`）与请求里写的目标文件一致，且真被执行。
- [x] `tool_input` 对非法 JSON 返回 `{}` 而不抛（验证：单测喂 `"不是 json"`、`"[1,2]"`、`"null"` 三种，断言都得到 `{}`）。(N4)
  **证据 ①：** `test_parse_args_treats_empty_string_as_empty_object`、`test_parse_args_reports_non_object_json`（`不是 json` / `[1, 2]` / `null` / `42` 四组）、`test_anthropic_replay_survives_illegal_tool_input`（历史回放那一侧同样不抛）。
- [x] 单轮闭环端到端：问「读 X 并总结」→ 模型调用 read_file → 结果回灌 → 给出最终文本总结（验证：`python -m qicode` 跑通，答复体现文件内容）。(AC8/F5/F6)
  **证据 ②：** tmux 实跑「读 docs/v2/spec.md 并用一句话总结」→ `● read_file({"path": "docs/v2/spec.md"})` → `⎿ 1…# v2 工具系统 Spec …` + `… 还有 102 行` → 最终答复准确概括了 spec 内容（统一工具抽象 + 注册中心 + 六个核心工具 + 两协议流式解析 + 单轮闭环）。
- [x] 单轮上限：需连续两步工具的任务，第一轮工具后即停、不发起第二轮工具执行（验证：`tests/test_agent.py` 脚本 (b) 断言只调用一次 `registry.execute`；或端到端观察）。(AC9/F6)
  **证据 ③：** ① `test_second_request_asking_for_tools_is_ignored`（请求#2 又要工具时不执行）、`test_tool_limit_gets_its_own_message`；② tmux 实跑「新建 out/hello.txt 再 bash 查看」——模型把两步拆到两次请求，第二轮又要工具时被拦下，对话区出现 `● 模型还想继续调用工具，但一轮只执行一次。请再发一条消息让它接着做。`，再发一条「继续」后它才跑 bash。**没有**自动发起第二轮工具执行。
- [x] `supports_tools` 生效：anthropic 配置开 `thinking: true` 时，请求体**不带** `tools`，且对话区有一行说明；openai 侧恒为 `True`（验证：`tests/test_agent.py` 脚本 (c) + 单测断言 `AnthropicProvider(cfg_thinking_true).supports_tools is False`）。(plan「关于 thinking 与工具的冲突」)
  **证据 ③：** ① `test_anthropic_supports_tools_is_the_inverse_of_thinking[False-True]` / `[True-False]`、`test_openai_supports_tools_regardless_of_thinking`、`test_anthropic_omits_tools_when_none_given`、`test_anthropic_sends_tools_with_input_schema`、`test_tools_are_withheld_when_the_provider_says_so`；② 另起一个 `/tmp/qicode_think` 配置（`thinking: true`）真终端启动 → 对话区顶部出现 `当前配置开启了 thinking，本阶段工具暂不可用`，纯文本对话仍正常（`说一句你好` → `你好`）。
- [x] 工具行 Claude Code 风格：对话区出现 `● name(关键参数)` + 缩进的 `⎿ 结果摘要`，过长截断（验证：端到端跑一次工具任务，肉眼比对 + tmux 回滚）。(AC11/F8)
  **证据 ②：** tmux 实跑多个工具任务，版式稳定为
  ```
   ● read_file({"path": "demo.txt"})
       ⎿      1      hello
              2      world
  ```
  超长参数按 `MAX_ARGS_PREVIEW = 80` 截断并标 `…`（实跑里 `bash({"command": "ls -l out/hello.txt && cat out/hello.txt && echo && stat -c '%s \u…`）；超长结果按 `MAX_SUMMARY_LINES = 8` 截断并标 `… 还有 N 行（完整内容已回灌给模型）`。
- [x] 工具失败结构化回灌且 UI 可区分、程序不退出（验证：读不存在文件 / edit 匹配不到 / bash 非零退出，各触发后再正常发一条）。(AC12/F9/N4)
  **证据 ③：** ① 各工具的错误路径单测；② tmux 实跑触发三类失败（文件不存在 / edit 未找到匹配 / bash `exit_code: 1`），每次都拿到结构化文本、整块转红（`capture-pane -e` 验到 `●` 与 `⎿` 都是 `38;2;255;0;0` / `38;2;244;0;95` 红色系），随后继续发消息对话正常——进程一次都没退出。

## 集成

- [x] 两协议工具流程一致：anthropic 与 openai（含兼容 `base_url`）跑同一组工具任务，触发/展示/回灌/错误行为一致（验证：两种配置各跑「读 X 并总结」）。**anthropic 侧目前验不了**（无密钥），只能靠 `tests/test_llm_providers.py` 里的消息转换单测覆盖。(AC10/F3/F7/N3)
  **证据 ③：这一条后来补上了真机 A/B（见文末「跨协议 A/B」）。** ① 单测侧：`test_anthropic_message_conversion_covers_all_three_rounds` / `test_openai_message_conversion_covers_all_three_rounds`、`test_anthropic_yields_tool_calls_before_done` / `test_openai_accumulates_interleaved_tool_call_fragments`、`test_anthropic_skips_the_text_block_when_preamble_is_empty` / `test_openai_sends_null_content_for_an_empty_preamble`；② 真机：同一个 key、同一个模型 `deepseek-chat`，只换 `protocol` 和 `base_url`，跑同一组任务，两个适配器（`AnthropicProvider` / `OpenAIProvider`）的触发、展示、回灌、错误行为逐项一致。
  **仍然没验的：** 官方 `api.anthropic.com`（见「已知未覆盖」）。行内那句「anthropic 侧目前验不了」是**定稿时**的判断，现在被兼容端点部分推翻了——保留原文以便对照。
- [x] 结果回灌进历史并被第二轮请求携带：assistant tool_use 回合 + tool_result 回合出现在续答上下文（验证：`tests/test_agent.py` 断言 `conv.messages()` 末尾序列；另断言 `conv.messages()` 被调了两次且第二次能看到第一次写入的内容）。(F6)
  **证据 ①：** `test_tool_round_writes_four_messages_into_history`（user / assistant+tool_calls / tool / assistant 四条，顺序固定）、`test_second_request_carries_the_tool_rounds`。
- [x] 历史只由 agent 写：TUI 侧不再出现 `conv.add_assistant`（验证：`grep -rn "add_assistant" src/qicode/tui/` 无输出）。(plan 技术决策「历史由谁写」)
  **证据 ①：** `grep -rn "add_assistant" src/qicode/tui/` 只剩两处，都在**注释**里（`app.py:408`、`app.py:520`），写的是「v1 在这里写历史，v2 不写」——没有任何一处是真的调用。
- [x] 工具执行不阻塞界面：执行期间工具行显示 `name(args) Running…` 指示，界面可响应（验证：让模型跑一个稍慢的 bash（如 `sleep 3`），观察界面持续刷新、能滚动，asyncio event loop 不卡顿）。(N2)
  **证据 ②：** tmux 实跑 `sleep 40`（会在 30 秒处超时）：对话区出现 `● ⠋ bash({"command": "sleep 40 && echo \"done\""}) Running…`，等待期间转轮持续换帧（相隔 1 秒两次捕获分别拿到 `⠋` 和 `⠧`）——事件循环没被工具阻塞；这 30 秒里还把窗口从 120×40 缩到 72×30，界面立刻按新宽度重排。
- [x] 对话区顺序正确：preamble 文本 → 工具行 → 结果摘要 → 最终答复 按序出现不交错（验证：多工具任务后回看对话区顺序；单 event loop 内 `VerticalScroll.mount()` 按事件顺序追加保序）。(F8)
  **证据 ③：** ① `tests/test_tui_app.py` 的三块顺序用例；② tmux 实跑「先读 demo.txt，再读 notes.txt」→ 一次请求里两个调用，对话区依次是 `● read_file(demo.txt)` / `⎿ …` / `● read_file(notes.txt)` / `⎿ …` / 最终答复，没有交错。
- [x] 工具执行期间**不改动已定型的块**：结果摘要出现后，后续正文另开新块，不会把结果顶走（验证：端到端观察「工具行 → 最终答复」两块的边界，答复出现时结果摘要原地不动）。(F8)
  **证据 ③：** ① `test_tool_round_renders_preamble_tool_row_then_final_reply`（三块类型恰为 `ReplyBlock / ToolBlock / ReplyBlock`，且开场白与 `⎿ 1→hello` 在最终答复出现后仍在屏上）、`test_empty_preamble_leaves_no_blank_reply_block`；② tmux 实跑全程：每个工具结果摘要都在最终答复出现后原地不动，答复是另一个带 `●` 的块。
- [x] 结果体量受控：读大文件 / 长输出 bash / 海量 grep 命中被工具级上限截断并标注 `[truncated]`，不撑爆界面/上下文（验证：读一个 >2000 行文件、跑长输出命令观察截断）。(AC13/N5)
  **证据 ③：** ① `test_truncate_marks_line_overflow` / `_marks_char_overflow` / `test_read_file_truncates_long_file`；② 直接调 `registry.execute` 实跑三例——`read_file big.txt`（2500 行）→ 2001 行、末行 `[truncated]`；`bash seq 1 20000` → 6221 行、末行 `[truncated]`（`MAX_OUTPUT_CHARS = 30000` 先触发）；`grep 工具 docs/v2` → 100 条 + `…（命中太多，只显示前 100 条。请把 pattern 写得更具体，或用 glob 限定文件范围）`。③ tmux 里 `read_file big.txt` 的界面表现：只画 8 行 + `… 还有 1993 行（完整内容已回灌给模型）`。
- [x] 退出回放认得工具回合：`/exit` 后工具行与结果摘要按原样重放到终端，不被当成正文整段印出（验证：跑一次工具任务后 `/exit`，翻终端回滚）。(AC11/F8)
  **证据 ②：** tmux 实跑一整场会话（8 轮、含 9 次工具调用）后按 `/exit`，翻回滚缓冲：每个工具轮都重放成「调用行 + `⎿` 结果行」，与对话区里同一版式；`ROLE_TOOL` 那条空 content 不再画成光秃秃的 `●`。
- [x] 系统提示词体现 Agent 角色：问「你能做什么」答复提及可用工具能力（验证：发一条询问，观察答复）。(F3)
  **证据 ②：** tmux 实跑「你能做什么」→ 答复列出读写文件 / 执行命令 / glob / grep / 读文件五类能力，并主动说出 30 秒超时、不做连环调用、无权限确认等约束，还准确复述了 provider「deepseek」与模型名。

## 编译与测试

- [x] `python -m qicode` 能正常启动（在合法配置下进入 TUI）。
  **证据 ②：** tmux 里多次启动（`/tmp/qicode_e2e`、`/tmp/qicode_think`），横幅 + 输入框 + 状态栏就位，能正常对话。
- [x] `ruff check .` 无告警。
  **证据：** `All checks passed!`
- [x] `ruff format --check .` 通过。
  **证据：** `36 files already formatted`
- [x] `pytest -v` 通过（`tests/test_tool.py`、`tests/test_agent.py` 新建并全绿；`test_tui_stream.py` 已随 `stream.py` 删除）。
  **证据：** `pytest -q` → **233 passed**。按文件分：`tests/test_tool.py` 45 条、`tests/test_llm_providers.py` 49 条、`tests/test_tui_app.py` 35 条、`tests/test_tui_view.py` 18 条（T17 新建）、`tests/test_agent.py` 17 条，全部通过。
- [x] `mypy src/qicode/` 通过。
  **证据：** `Success: no issues found in 22 source files`
- [x] 异步测试**没有**引入 `pytest-asyncio`（验证：`grep -n "pytest-asyncio\|asyncio_mode" pyproject.toml` 无输出，新测试全用 `asyncio.run`）。(plan 技术决策「异步测试怎么写」)
  **证据 ①：** `grep` 无输出；`tests/test_agent.py` 与非流式的 provider 用例全部是同步 `def test_...` 里包一个 `asyncio.run(...)`。
- [x] 密钥不回显/不打印：对话区与任何输出均不出现 `api_key`；工具执行结果里也不带（验证：通读运行输出、检索无明文 key）。(N6)
  **证据 ②：** 把四份 tmux 捕获（含带转义码的 `capture-pane -e`）与整场 `/exit` 回放文本丢进脚本，逐份检索 `/tmp/ds_key` 的内容与 `sk-` 前缀 → 全部 `False`。工具结果里出现过的是 `ls -l out/hello.txt` 这类输出，不含任何配置字段。

## 端到端场景

- [x] 场景 1（读文件并总结）：openai 兼容端点 → 问「读 docs/v2/spec.md 用一句话总结」→ `● read_file(...)` 工具行 + 结果摘要 + 最终 markdown 答复 → `/exit` 退出，终端无残留。
  **证据 ②：** 见「单轮闭环端到端」那条；`/exit` 后回到普通 shell 提示符，备用屏幕内容随回放落进回滚缓冲，终端本身无残留（回放是主屏幕上的一次正常输出）。
- [x] 场景 2（写/改/执行链路）：让模型「新建一个文件并写入内容，再用 bash 查看它」→ 观察 write_file 与 bash 工具行依次出现、结果正确（单轮内多工具顺序执行）。
  **证据 ②：** 这条**没有**在单轮内走完——模型把两步拆到了两次请求，第二轮又要工具时被 AC9 拦下并给出单轮提示，再发「继续」才执行 bash，结果是 `⎿ exit_code: 1` + 正确 stdout + BSD stat 的 stderr，最终答复判断「文件写对了，报错出在 stat 参数」。**单轮内多工具顺序执行**由另一条实跑补上：「先读 demo.txt，再读 notes.txt」→ 一次请求里两个 `read_file`，对话区依次出现两个工具行与各自的结果摘要，然后给最终答复。
- [x] 场景 3（错误恢复）：让模型 edit 一段不存在的文本 → 工具返回「未找到匹配」结构化错误、UI 红色提示、程序不退出 → 再正常发一条继续对话。
  **证据 ②：** tmux 实跑「把 notes.txt 里的『不存在的这句话』改成『新值』」→ `⎿ 未找到匹配的内容…` 整块红色，模型随即说明「替换匹配 0 次，文件未被改动」；之后连续又发了几条消息，对话一直正常。
- [x] 场景 4（单轮上限）：让它「先读 A 再读 B」→ 停在第一次工具之后，给出答复或单轮提示，不自动发起第二轮。(AC9)
  **证据 ②：** tmux 实跑「先读 demo.txt，再读 notes.txt」→ 模型把两次读放进**同一次**请求（一次批量、两个工具行），执行完直接给答复，**没有**自动发起第二轮；另一条实跑（写文件那次）覆盖了「第二轮又想要工具」的分支，对话区给出单轮上限提示。
- [x] 场景 5（跨协议，若有 anthropic 配置）：切到 anthropic 配置重跑场景 1 → 工具触发/展示/回灌/答复行为与 openai 一致。**目前验不了**。
  **证据 ②：** 在 `protocol: anthropic` 的配置上重跑了场景 1，行为与 openai 侧一致（详见文末「跨协议 A/B」）。**注意**：跑的是 DeepSeek 的 **anthropic 兼容**端点，不是官方端点——场景 1 要验的「行为一致」这件事验到了，官方服务特有的语义没验到。定稿时写的「目前验不了」现在只对官方端点成立。

## 已知未覆盖

- **官方 `api.anthropic.com` 真机链路**：工具定义注入、`tool_use` 解析、`thinking`
  与工具的互斥。手上没有官方密钥，这一半仍然只有单测与文档核对。
  **注意区分**：`AnthropicProvider` 这条**代码路径**是真机跑过的（DeepSeek 的
  anthropic 兼容端点），没跑过的是**官方服务本身**——两边的差异在于官方独有的
  `thinking` 块结构、官方 SDK 的真实响应形状与 `stop_reason` 语义。
  与 v1 的 anthropic 侧同一条缺口。
- **Anthropic thinking 与工具并用**：本阶段按设计互斥（开了 thinking 就不发工具定义），
  完整支持留待后续阶段。
- **并发工具执行**：本阶段顺序执行，不做并发加速（spec「不做的事」）。
- **权限确认**：写文件与执行命令不做授权确认（spec「不做的事」）。

## 跨协议 A/B（T18 补做）

定稿时把「两协议一致」判成验不了，是因为手上没有 **Anthropic** 的密钥。但 DeepSeek
同时提供 anthropic 与 openai 两个兼容端点——于是可以拿**同一个 key、同一个模型
`deepseek-chat`**，只换 `protocol` 与 `base_url`，做一次变量极干净的对照：两边跑的是
同一套 `qicode.tool` + `qicode.agent`，真正不同的只有 `AnthropicProvider` 与
`OpenAIProvider` 这两个适配器。这比拿 ollama 对照强得多（那是另一个模型、另一套本地栈）。

| 观察项 | anthropic 侧 | openai 侧 | 一致？ |
|---|---|---|---|
| 工具行 | `● read_file({"path": "docs/v2/spec.md"})` | 同 | ✅ |
| 结果摘要缩进与截断 | `  ⎿ …` + `… 还有 102 行（完整内容已回灌给模型）` | 逐字相同 | ✅ |
| 参数预览截断 | 超 80 字符标 `…` | 同 | ✅ |
| 单轮内多工具批量 | 两个工具行依次出现、各自结果 | 同 | ✅ |
| 文件不存在 | `⎿ 文件不存在: …` 整块红 | 同 | ✅ |
| bash 非零退出 | `⎿ exit_code: 1` + stdout/stderr 完整、整块红 | 同 | ✅ |
| 超时 | `⎿ 工具 bash 执行超时（30 秒）` | 逐字相同 | ✅ |
| `/exit` 回放 | 工具轮重放成「调用行 + `⎿` 结果行」 | 同 | ✅ |

三个补充说明：

- **两侧的 `… 还有 102 行` 连数字都一样**——因为截断规则（`MAX_SUMMARY_LINES`）在界面层，
  不在适配器里；数字撞上反而是个正面信号：两边回灌给模型的确实是同一份内容。
- **`thinking: true` 只有 anthropic 侧有意义**（openai 协议没有这个概念，
  `supports_tools` 恒为 `True`），不存在「两协议一致」的问题。
- 这次 A/B 推翻的是「anthropic 侧验不了」，**没有**推翻官方端点那个缺口。

## T18 实跑时新看到的

- **单轮上限的提示不进历史**：`TOOL_LIMIT` / `EMPTY_REPLY` 这两类提示是界面层的
  `err` 事件，不写进 `Conversation`，所以 `/exit` 回放里看不到它们。
  对模型来说「它刚才想调工具但被拦了」这件事因此不留痕——用户再发一条「继续」时，
  模型是从「上一次工具结果」接着想的，不知道中间有提示。
  这是本阶段的有意取舍（错误提示不入历史，免得污染上下文），记在这里免得以后当成 bug。
- **界面上的工具结果只显示前 8 行，但 `[truncated]` 那行在界面上看不到**：工具层的
  截断标记写在内容的**末尾**，而界面摘要只取前 8 行 + 一句「还有 N 行」。
  也就是说「工具自己截过」这件事，界面用户看不到，只有模型看得到。
  本次实跑里是模型自己在答复里说出来的（「末尾标注 `[truncated]`」）。是否要在界面上
  也提示，留待后续决定。
