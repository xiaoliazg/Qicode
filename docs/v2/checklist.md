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
>
> ---
>
> **二次回填：2026-09-23（T19，硬伤修复）。** v2 交付后做了一次三层代码审查，查出三个
> 硬伤（成因、修法、反向证明见文末「T19 硬伤修复记录」）。修完回头核这份 checklist，
> 发现**有 5 条当初是虚勾的**——打勾时给的证据都真实，但**只覆盖了顺利路径**，而硬伤
> 恰恰长在证据没盖到的那片输入空间里：
>
> | 条目 | 当初的证据 | 漏掉的输入空间 | 当时实际的后果 |
> |---|---|---|---|
> | AC4 `edit_file` | ASCII 内容 + LF 文件 | CRLF 文件、非 UTF-8 文件 | 静默改坏整个文件 |
> | AC5 `bash` 超时 | `sleep 40`（单命令） | 管道 / `&&` 链式命令 | 超时后 `wait()` 永不返回，请求挂死 |
> | AC6 `grep` | 普通关键字 | 正则的**形状** | 灾难性回溯把界面冻死 |
> | N2 不阻塞界面 | `sleep 40` 期间界面能刷新 | 灾难性回溯 | 连 `wait_for` 都不触发，只能 `kill -9` |
> | AC13/N5 体量受控 | 三个上限都验了 | 上限之外文件有没有被动过 | （与 AC4 同源） |
>
> **教训**：一条勾只代表**它证据覆盖到的范围**，不代表那条需求成立。这 5 条已按更宽的
> 输入空间重跑，下面每条都标了「**T19 补**」；勾保留，因为它们现在**真的有证据**了。
>
> T19 的实跑目录仍是 `/tmp/qicode_e2e`（配置同下表），另造三个夹具：`crlf.txt`
> （CRLF 三行）、`latin1.txt`（ISO-8859 编码）、`stress/bomb.txt`（80 个 `a` + `!`，
> 用来触发灾难性回溯）。
>
> ---
>
> **三次回填：2026-09-23（T20，硬伤修复第二轮）。** 把 T19 那轮审查里**剩下没修完**的
> 三条做完：一次打断留下孤儿 `tool_use`（之后每轮 400）、`read_file` 撞 FIFO 让进程
> **永久挂死**、`bash` 输出超限时不说清「是我们杀的」。成因、修法、反向证明见文末
> 「T20 硬伤修复记录（第二轮）」。
>
> 这一轮**没有新增虚勾**——T19 已经把那 5 条按更宽的输入空间重跑过了。T20 修的是
> 审查清单里当时标了「未修」的那几条，外加一条措辞订正：**AC9 的「一轮工具调用」容易被
> 读成「一次工具调用」**，已在 `spec.md` 里改成「一轮 = 一次批量」并补上 F6 的准确定义。
> 实跑目录改在 `/tmp/qc_smoke`（每次用完即删，含密钥的临时配置不放着过夜）。
>
> ---
>
> **四次回填：2026-09-23（T21，界面修复）。** 这一轮不是审查查出来的，是居居看着真界面
> 提的三点：**光标是个白方块**、**输入框总像没聚焦**、**背景是应用自己画的黑框框**。
> 三条是**三个不同的根因**，全在 Textual 自己身上，见文末「T21 界面修复记录」。
>
> ---
>
> **五次回填：2026-09-23（T22，ollama 那条 tools 报错）。** 居居让「顺便看看 ollama 那个
> tools 报错」，查下来比想象中重：配本地 ollama 的 Qicode **每一轮都 400，完全没法用**。
> 根因是 `supports_tools` 把「协议支持工具」当成了「模型支持工具」。修法是撞到那种 400 就
> 摘掉工具重发一次（第 5 条取舍见文末「T22 修复记录」）。
>
> 这一轮的性质跟 T19/T20 不同：不是审查查出来的缺陷，而是**一条我们从来没验过的接入路径**
> ——v2 的两条协议一致性 A/B 用的是 DeepSeek 的端点，ollama 只用来跑过 v1 的对话，
> **从没带着工具跑过**。checklist 里也没有对应的验收项，所以这一条不算「虚勾」，
> 算**覆盖缺口**：验收当时列的六条工具路径都验了，没验的是「后端模型本身收不收工具」。

## 实现完整性

- [x] 注册中心导出 6 条工具定义且按名可查（验证：`pytest tests/test_tool.py -k registry`，断言 `definitions()` 长度 == 6、名称有序、`get` 命中/未命中、重名 `register` 抛 `ValueError`）。(AC1/F1)
  **证据 ①：** `pytest tests/test_tool.py -k registry -v` → 8 passed（六个工具按注册顺序导出、`get` 命中/未命中、重名抛 `ValueError`、`execute` 的透传/未知工具/超时/意外异常四条路径）。
- [x] read_file 带行号读出内容；读不存在 / 是目录 / 参数非法各返回结构化错误（验证：单测 + 手测读 `docs/v2/spec.md` 见行号、读不存在文件得 `is_error`）。(AC2/F2)
  **证据 ③：** ① `test_read_file_numbers_lines` / `_missing_file` / `_on_directory` / `_reports_bad_arguments`（4 组参数）全绿；② tmux 实跑 `读 docs/v2/spec.md` → `⎿      1      # v2 工具系统 Spec`，行号在位；实跑 `read_file pyproject.toml`（该目录下不存在）→ `⎿ 文件不存在: pyproject.toml`。
- [x] write_file 创建/覆盖文件，父目录自动创建（验证：单测用 `tmp_path/"a/b/c.txt"` 后读回内容一致；另测 `content` 为空串时不被误判成缺参数）。(AC3/F2)
  **证据 ③：** ① `test_write_file_creates_nested_paths` / `_overwrites` / `_allows_empty_content` / `_requires_content_key`；② tmux 实跑「新建文件 out/hello.txt 写入内容 琪琪来啦」→ `⎿ 已写入 out/hello.txt（12 字节）`，`out/` 这个不存在的父目录被自动建出来。
- [x] edit_file 唯一匹配替换成功；0 处与 >1 处返回**可区分**错误（含匹配数）（验证：单测三情形，断言文案两两不同且 >1 那条含 N）。(AC4/F2)
  **证据 ③：** ① `test_edit_file_replaces_unique_match`、`test_edit_file_reports_distinguishable_errors[...未找到匹配]`、`[...匹配到 2 处]`、`test_edit_file_error_messages_differ_between_zero_and_many`；② tmux 实跑「把 notes.txt 里的『不存在的这句话』改成『新值』」→ 结果行「未找到匹配的内容。`old_string` 必须与文件内容逐字符一致（包括缩进和换行），请先 read_file 确认原文。」（原文这里把整段套在了一个代码段里，里面还嵌了 `` `old_string` ``，渲染会断开，T19 顺手改成引号包裹。）
  **T19 补（原证据只有 ASCII + LF，补 CRLF 与非 UTF-8）：** ① 新增 `test_edit_file_keeps_crlf_line_endings`、`test_edit_file_matches_lf_old_string_against_a_crlf_file`、`test_edit_file_refuses_non_utf8_instead_of_replacing_bytes`、`test_edit_file_leaves_untouched_lines_byte_identical`。② tmux 实跑「把 `crlf.txt` 的前两行 `first line` / `second line` 整体换成 `第一条` / `第二条`（保持两行）」→ 模型给的 `old_string` 里是 `\n`，工具走第二遍 CRLF 匹配 → `⎿ 已修改 crlf.txt`；落盘后逐字节核：**3 个 CRLF、0 个裸 LF、0 个裸 CR**，`file` 仍判 `with CRLF line terminators`，未动的第三行逐字节不变。③ tmux 实跑「用 edit_file 改 `latin1.txt`」→ `⎿ latin1.txt 不是 UTF-8 文本（'utf-8' codec can't decode byte 0xe9 in position 3: invalid continuation byte），本工具不修改它。请先用 bash 确认它的真实编码。`；**md5 前后完全一致**（`c6129cbb…`），原始字节 `caf 351 na 357 ve` 原样还在。
    **这两条在修复前都是「静默改坏」**：`read_text` 的 `errors="replace"` 把非 UTF-8 字节换成 U+FFFD、universal newlines 把 `\r\n` 折成 `\n`，然后**整篇写回**——改一个字节的地方，动整个文件。
- [x] bash 返回 stdout/stderr/退出码；**非零退出是 `is_error` 但内容完整**；超时命令被终止并返回超时结果（验证：单测 `echo hi`、`false`、注入极短超时跑 `sleep 5`）。(AC5/F2/N1)
  **证据 ③：** ① `test_bash_returns_stdout_and_zero_exit` / `_nonzero_exit_is_error_but_keeps_output` / `_timeout_is_reported_and_kills_the_process`；② tmux 实跑 `ls -l ... && stat -c ...`（macOS 的 BSD stat 不认 `-c`）→ `⎿ exit_code: 1` + stdout、stderr 两段都完整回灌，模型据此解释了报错原因；实跑 `sleep 40` → `⎿ 工具 bash 执行超时（30 秒）`，`DEFAULT_TIMEOUT = 30.0` 生效。
  **T19 补（原证据是 `sleep 40` 这条「单命令」，补管道与链式）：** ① 新增 `test_bash_timeout_returns_promptly_for_pipelines_and_chained_commands`、`test_bash_timeout_kills_the_whole_process_tree`。② tmux 实跑真管道 `sleep 45 | cat` → 第 13s / 21s 两次捕获转轮帧都在变（界面没冻），约 30 秒返回 `⎿ 工具 bash 执行超时（30 秒）`；跑完 `pgrep -fl "sleep 45"` → **无孤儿**。
    **修复前这里是永久挂死**：`sh -c` 拉起的管道子进程是 `sh` 的**孙进程**，`await proc.wait()` 不只要等进程退出，还要等所有管道 EOF（`BaseSubprocessTransport._try_finish` 要求 `_pipes` 全部断开）；孙进程握着写端不放，那个 EOF 永远不来。`sleep 40` 之所以能过，只是因为它没有管道。修法是 `start_new_session=True`（`setsid()`，让 `sh` 当进程组组长）+ 超时时 `os.killpg(SIGKILL)` 杀**整组**。
- [x] glob 列出匹配文件；grep 返回 `file:line:content`（验证：单测 `**/*.py` 命中、关键字 grep 命中）。(AC6/F2)
  **证据 ③：** ① `test_glob_finds_files_recursively`、`test_grep_reports_file_line_and_content`、`test_grep_honours_glob_filter`；② 直接调 `registry.execute` 实跑 `grep 工具 docs/v2` → 首行 `checklist.md:1:# v2 工具系统 Checklist`，`file:line:content` 格式成立。
  **T19 补（原证据只有普通关键字，补正则的「形状」）：** ① 新增 `test_grep_stops_a_catastrophic_regex_instead_of_freezing`、`test_grep_still_matches_a_long_line_within_the_budget`、`test_grep_leaves_the_alarm_handler_as_it_found_it`。② tmux 实跑 `grep({"pattern": "(a+)+$", "path": "stress"})`（夹具 `bomb.txt` = 80 个 `a` + `!`）→ 结果行「正则 `(a+)+$` 在 bomb.txt:1 上匹配超过 1 秒仍未结束，已中止搜索。这通常是嵌套量词（如 `(a+)+`）在长行上引发的灾难性回溯，请改写 pattern 让它更具体。」，界面无卡顿，模型据此自行改写 pattern。③ 修复前的标度实测：n=20 → 0.06s、n=24 → 0.89s、n=26 → 3.60s、n=28 → 14.46s（≈每多一个字符 ×4），n=60 已属「几小时」；修复后 n=28 / 60 / 200 一律 **1.00 秒**返回，正常搜索仍是 0.0003 秒（没被殃及）。
- [x] glob / grep **无命中是 `is_error=False`** 的正常结果，不是错误（验证：单测搜一个绝不存在的关键字，断言 `is_error is False` 且文案含「无命中」）。(F9 的边界)
  **证据 ③：** ① `test_glob_no_match_is_not_an_error`、`test_grep_no_match_is_not_an_error`；② 直接调工具实跑：grep `绝不存在的关键字zzz` → `无命中：没有任何文件内容匹配 ...`（`is_error=False`）；glob `**/*.py` on `docs/` → `无匹配：没有文件符合模式 ...`（`is_error=False`）。
- [x] 流式工具调用解析正确：模型一次回复的工具名与完整 JSON 参数被拼齐（验证：agent fake 单测断言 `input` 是完整 JSON；openai 侧另测两个工具**交错分片**能各拼各的；端到端发「读 X 文件」，工具行参数与请求一致）。(AC7/F4)
  **证据 ③：** ① `test_openai_accumulates_interleaved_tool_call_fragments`、`test_openai_output_order_follows_index_not_arrival`、`test_anthropic_yields_tool_calls_before_done`、`test_anthropic_finds_tool_calls_only_in_the_final_message`；② tmux 实跑里工具行的参数（`{"path": "docs/v2/spec.md"}`）与请求里写的目标文件一致，且真被执行。
- [x] `tool_input` 对非法 JSON 返回 `{}` 而不抛（验证：单测喂 `"不是 json"`、`"[1,2]"`、`"null"` 三种，断言都得到 `{}`）。(N4)
  **证据 ①：** `test_parse_args_treats_empty_string_as_empty_object`、`test_parse_args_reports_non_object_json`（`不是 json` / `[1, 2]` / `null` / `42` 四组）、`test_anthropic_replay_survives_illegal_tool_input`（历史回放那一侧同样不抛）。
- [x] 单轮闭环端到端：问「读 X 并总结」→ 模型调用 read_file → 结果回灌 → 给出最终文本总结（验证：`python -m qicode` 跑通，答复体现文件内容）。(AC8/F5/F6)
  **证据 ②：** tmux 实跑「读 docs/v2/spec.md 并用一句话总结」→ `● read_file({"path": "docs/v2/spec.md"})` → `⎿ 1…# v2 工具系统 Spec …` + `… 还有 102 行` → 最终答复准确概括了 spec 内容（统一工具抽象 + 注册中心 + 六个核心工具 + 两协议流式解析 + 单轮闭环）。
- [x] 单轮上限：**一轮 = 一次批量**——同一次请求里的多个工具调用**全部执行**，但结果回灌之后**不再发起第二轮**（验证：`tests/test_agent.py` 的 `test_second_request_asking_for_tools_is_ignored` 断言请求#2 又要工具时不再触发执行；端到端见场景 4）。(AC9/F6)
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
  **T19 补（原证据用 `sleep 40`，补真正的「C 调用卡住」反例）：** AC5 / AC6 那两次 tmux 实跑同时验了这条——`sleep 45 | cat` 等待的 30 秒里转轮持续换帧（13s / 21s 两帧不同），`(a+)+$` 那次 1 秒内返回且界面可响应。
    **为什么这条当初会虚勾**：`sleep` 是**会让出 CPU 的**，事件循环本来就轮得转，所以它验不出「界面被卡死」。真正的反例是同步 C 调用——`re.search` 匹配期间**全程持有 GIL**，实测 60 字符输入下 `asyncio.wait_for(20s)` 不触发，另起一个准备 25 秒后 `os._exit` 的看门狗线程也**没能执行**（抢不到 GIL），进程只能 `kill -9`。也就是说对这类卡顿，「丢线程 / 加超时」全都无效，只有信号（处理器跑在主线程、不需要抢 GIL）能救。
- [x] 对话区顺序正确：preamble 文本 → 工具行 → 结果摘要 → 最终答复 按序出现不交错（验证：多工具任务后回看对话区顺序；单 event loop 内 `VerticalScroll.mount()` 按事件顺序追加保序）。(F8)
  **证据 ③：** ① `tests/test_tui_app.py` 的三块顺序用例；② tmux 实跑「先读 demo.txt，再读 notes.txt」→ 一次请求里两个调用，对话区依次是 `● read_file(demo.txt)` / `⎿ …` / `● read_file(notes.txt)` / `⎿ …` / 最终答复，没有交错。
- [x] 工具执行期间**不改动已定型的块**：结果摘要出现后，后续正文另开新块，不会把结果顶走（验证：端到端观察「工具行 → 最终答复」两块的边界，答复出现时结果摘要原地不动）。(F8)
  **证据 ③：** ① `test_tool_round_renders_preamble_tool_row_then_final_reply`（三块类型恰为 `ReplyBlock / ToolBlock / ReplyBlock`，且开场白与 `⎿ 1→hello` 在最终答复出现后仍在屏上）、`test_empty_preamble_leaves_no_blank_reply_block`；② tmux 实跑全程：每个工具结果摘要都在最终答复出现后原地不动，答复是另一个带 `●` 的块。
- [x] 结果体量受控：读大文件 / 长输出 bash / 海量 grep 命中被工具级上限截断并标注 `[truncated]`，不撑爆界面/上下文（验证：读一个 >2000 行文件、跑长输出命令观察截断）。(AC13/N5)
  **证据 ③：** ① `test_truncate_marks_line_overflow` / `_marks_char_overflow` / `test_read_file_truncates_long_file`；② 直接调 `registry.execute` 实跑三例——`read_file big.txt`（2500 行）→ 2001 行、末行 `[truncated]`；`bash seq 1 20000` → 6221 行、末行 `[truncated]`（`MAX_OUTPUT_CHARS = 30000` 先触发）；`grep 工具 docs/v2` → 100 条 + `…（命中太多，只显示前 100 条。请把 pattern 写得更具体，或用 glob 限定文件范围）`。③ tmux 里 `read_file big.txt` 的界面表现：只画 8 行 + `… 还有 1993 行（完整内容已回灌给模型）`。
  **T19 补（原证据只验了三个上限，补「上限之外文件有没有被动过」）：** 重跑三例，数字与上一条逐字一致（2500 行 → 2001 行 + `[truncated]`；`seq 1 20000` → 6221 行 + `[truncated]`；`grep 工具 docs/v2` → 100 条 + 提示），并额外做了 `big.txt` 的 md5 前后比对 → **未变**。「截断」是**只读**路径上的事，跟 AC4 的写路径分开看：读的宽容（`errors="replace"`）不会落盘，写的必须严格。
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
  **T20 复核（数字更新）：** `pytest -q` → **264 passed**。按文件分：`test_tool.py` **69**、`test_llm_providers.py` **50**、`test_tui_app.py` 35、`test_config.py` 30、`test_agent.py` **22**、`test_tui_view.py` **19**、`test_conversation.py` 9、`test_prompt.py` 9、`test_redact.py` 8、`test_tui_select.py` 7、`test_cli.py` 6。
    **249 → 264 的 +15 拆得开**：② 加 **11** 条（`test_tool.py`：非普通文件拦截 6 条 + `_run_blocking` 原语 4 条 + 目录文案不回归 1 条）、③ 加 **3** 条（`test_tool.py`：超限说明在场 / 不误报 / 位置在正文前部）、① 加 **1** 条（`test_agent.py`：半截工具回合保留已拿到的真结果）。`test_tool.py` 55 → 69、`test_agent.py` 21 → 22，其余文件一条未动。
- [x] `mypy src/qicode/` 通过。
  **证据：** `Success: no issues found in 22 source files`
  **T19 复核：** 上面四条（`ruff check .` / `ruff format --check .` / `pytest -q` / `mypy src/qicode/`）在三个硬伤修完后全部重跑，除了 `pytest` 的条数，输出逐字未变（`All checks passed!` / `36 files already formatted` / `Success: no issues found in 22 source files`）。
  **T20 复核：** T20 三条修完后同样重跑四条，输出**逐字仍未变**（同上三句），`pytest -q` → **264 passed**（见上一行的分文件数字）。② 用到的 `_run_blocking` 是新的执行原语，`mypy` 仍需 22 个源文件全过——PEP 695 泛型参数写法在这版 mypy 下无告警。
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
- **中断键**：没有「打断这一轮」的按键——`App.BINDINGS` 里只有 `ctrl+c → 退出`，
  Esc 什么也没接（spec「不做的事」：不支持中途取消）。Ctrl+C 是退出整个程序，
  退出前会 `_cancel_stream()`，所以取消路径仍然被走到，但那不等于中断。
  **注意**：一旦加上中断键，`docs/v2/checklist.md`「硬伤 4」那条潜伏缺陷就变成活的
  ——那条已经修好了，这里只是记一笔因果。

## 留给下一阶段（T19/T20 审查提出、本轮明确不做）

下面两条是 T19 那轮三层代码审查提出来的，都**不是缺陷**，是范围/产品的取舍。
居居在 2026-09-23 定了：**不在 v2 修**，随权限阶段一起做。记在这里，免得它们
只活在对话里。

- **脱敏范围（redact scope）**：`qicode.redact` 目前只有**一个**调用点——
  `tui/app.py:560`，洗的是上游异常原文。工具**读出来的文件内容**、`bash` 的输出，
  上屏 / 进历史 / `/exit` 回放**全是原文**。
  之所以不顺手扩：全量脱敏要靠模式匹配（`sk-…`、`AKIA…` 之类），就会有误报，
  把用户正常讨论的字符串也抹掉。**边界怎么划是产品决定**，且它跟权限是一件事
  （「谁的密钥能在什么范围内出现」），所以放到权限阶段一起定。
- **超长单行的渲染上限**：`view.py` 对一行内容的渲染长度没有上限。
  实测 100 万字符单行 → **58ms**，是**卡顿**不是冻死（Textual 自己会 wrap）。
  加不加渲染上限、加多少，是体验取舍，本阶段不动。

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

## T19 硬伤修复记录（2026-09-23）

v2 交付后做了一次三层代码审查（工具层 / 适配器层 / TUI 层，各由一个独立子代理跑），
查出三个「硬伤」——不是风格问题，是会**丢数据、挂死进程**的那类。下面的数字都是实跑出来的。
「反向证明」指的是**把修复逻辑撤掉，确认用例确实失败**——不这么做就无法排除「这个用例
本来就过」，那单测绿了就说明不了任何事。

### 硬伤 1 · `bash` 超时后请求永久挂死

- **症状**：`sleep 40` 能正常超时，但 `sleep 45 | cat` 这类**管道**或 `a && b` **链式**
  命令超时后，整个请求再也不返回——界面停在 `Running…` 不动，只能杀进程。
- **根因**：`asyncio.create_subprocess_shell` 实际执行的是 `sh -c "..."`，管道里的
  `cat` 是 `sh` 的**孙进程**。`await proc.wait()` 不只要等进程退出，还要等**所有管道
  EOF**（`BaseSubprocessTransport._try_finish` 要求 `_pipes` 全部断开，而读端 EOF 得等
  所有写端先关）。杀了 `sh`，孙进程还攥着写端，EOF 永远不来。`sleep 40` 之所以看着正常，
  只是因为它**没有管道**——这正是这条当初虚勾的原因。
- **修法**：`start_new_session=True`（内部 `setsid()`，让 `sh` 成为**进程组组长**），
  超时时 `os.killpg(os.getpgid(proc.pid), SIGKILL)` 杀**整组**。
- **成效**：超时后 **0.50 秒**返回；`pgrep` 确认无孤儿。
- **反向证明**：撤掉修法后重跑新用例 → `wait()` 挂死；另用一个「每 5 秒追加内容」的
  哨兵文件观察，它从 65 字节涨到 90 字节（证明孙进程还活着）。

### 硬伤 2 · `edit_file` 静默改坏文件

- **症状**：改 CRLF 文件 → 整篇被折成 LF；改非 UTF-8 文件 → 坏字节变成 `U+FFFD` 后
  写回。两种都是**静默**的：工具返回「已修改」，用户和模型都察觉不到。
- **根因**：`Path.read_text` / `write_text` 的默认值都在动内容——`errors="replace"`
  把解不开的字节换成 `U+FFFD`，universal newlines 把 `\r\n` 折成 `\n`，写回时再按
  `os.linesep` 展开。**只改一个字节的地方，动了整个文件。**
- **修法**：`read_bytes` → **严格** `decode("utf-8")`（解不开就拒绝，不猜编码）→ 匹配
  （先按原样，不中再把 `\n` 换成 `\r\n` 试第二遍）→ 替换时让 `new_string` 的行尾跟
  **匹配到的原文**对齐 → `write_bytes`。
- **为什么必须有第二遍匹配**：模型手里的 `old_string` 抄自 `read_file`，而 `read_file`
  走 universal newlines，**给模型看的行尾一律是 `\n`**；CRLF 文件里的真实字节却是
  `\r\n`。少了这一步，模型在 CRLF 项目里永远匹配不上跨行的 `old_string`，而它收到的
  错误提示（「必须逐字符一致」）会把它引向完全错误的方向。
- **反向证明**：撤掉修法后，CRLF / 非 UTF-8 / 未触碰行三个场景**全被改坏**。

### 硬伤 3 · `grep` 灾难性回溯冻死界面

- **症状**：`(a+)+$` 这类嵌套量词作用在长行上，界面**完全冻死**，连 Ctrl+C 都按不动。
- **根因**：`re` 是回溯式引擎，标度实测 ≈ **每多一个字符 ×4**（n=20 → 0.06s、
  n=24 → 0.89s、n=26 → 3.60s、n=28 → 14.46s；n=60 已属「几小时」）。而 `re.search`
  是同步 C 调用，卡住时事件循环连取消回调都跑不了——**`Registry` 那层 30 秒的
  `wait_for` 形同虚设**。更狠的是 `_sre` 匹配期间**全程持有 GIL**：实测 60 字符输入下
  `wait_for(20s)` 不触发，另起一个准备 25 秒后 `os._exit` 的看门狗线程也**没能执行**
  （抢不到 GIL），只能 `kill -9`。
- **修法**：`SIGALRM` 给每次匹配掐 1 秒表（`grep_tool.MATCH_BUDGET`）。它**是唯一**
  能真正中断的办法——信号处理器跑在**主线程**、不需要抢 GIL，而 CPython 的 `_sre` 在
  匹配循环里会周期性调用 `PyErr_CheckSignals()`（Ctrl+C 能打断跑疯的正则，靠的就是它），
  所以处理器里抛的异常能从匹配内部**真正中断**它。搜索期间装处理器、结束**还原**
  （`SIGALRM` 的默认动作是杀进程，残留下来属于查不出来的那类 bug）。
- **降级**：Windows 没有 `SIGALRM`、非主线程装不上，两种情况都退成「不掐表」——工具还
  能用，不会一上来就报错。
- **成效**：n=28 / 60 / 200 一律 **1.00 秒**返回；正常搜索 0.0003 秒，没被殃及。
- **反向证明**：把预算调大（等价于旧逻辑）跑同一个用例，外层 25 秒硬超时 → 退出码
  **137**（SIGKILL），连 pytest 的收集信息都没输出。

### 这一轮真正学到的东西

**「丢进线程 / 加个超时」不是万能药。** 对**会主动让出 CPU** 的阻塞（文件 IO、
`sleep`）它们有效；对**同步 C 调用**（`re.search`、某些解析器）无效——那种情况下别的
线程连 GIL 都抢不到，杀也杀不动。判据是：**这段代码会不会主动让出 GIL**。这直接决定了
`read_file`（可以 `to_thread`）和 `grep`（不能，而且 `to_thread` 还会同时废掉
`await asyncio.sleep(0)` 的让出效果、以及信号那层保护）为什么走两条不同的路。

**另一条**：「一条 checklist 勾只代表它证据覆盖到的范围」。这一轮 5 条虚勾，全都是因为
打勾时挑的输入**恰好绕开了**缺陷所在的那片空间（单命令 vs 管道、ASCII+LF vs CRLF+非
UTF-8、普通关键字 vs 恶意正则）。写验收证据时值得专门问一句：**「我挑这个输入，是因为
它有代表性，还是因为它跑得通？」**

## T20 硬伤修复记录（第二轮）（2026-09-23）

T19 之后，把同一轮三层审查里**剩下没修完**的几条做完。判据和 T19 一样：只收「会挂死
进程、会废掉整场对话」那一类，风格问题不在这轮的范围内。数字同样都是实跑出来的，
反向证明的做法也一样（撤掉修复逻辑，确认用例确实失败）。

### 硬伤 4 · 一次打断留下孤儿 `tool_use`，此后每轮都撞 400

- **症状**：模型说「我调 read_file」之后、工具结果写进历史之前，这一轮被取消——这条
  **残废的历史留在会话里**，之后不管发什么，服务端都以 400 拒绝。
- **今天打不打得出来（诚实交代）**：**打不出来。** 本阶段 `App.BINDINGS` 里只有
  `ctrl+c → 退出`，取消只发生在 `_quit` 的 `_cancel_stream()` 里，而紧接着就是
  `exit()`——会话本来就没了，「之后每轮都 400」没有之后。所以这一条是**潜伏的**，
  不是线上能复现的。
  它仍然算硬伤，有两个理由：一是 `Agent.run` 是对外可复用的一层，任何调用方
  `cancel()` 都会踩到这段路径；二是「给 Qicode 加个中断键」是明显的下一步（本轮
  ④⑤⑥ 里的 ⑤ 就是它），**中断键一加上，这条立刻从潜伏变成每天都会中**。修的代价
  是 8 行，不修的代价是「加中断键那天炸」。
- **根因**：Anthropic 协议要求每个 `tool_use` 块在**紧邻的下一条**消息里有配对的
  `tool_result`。取消发生的那一刻，assistant 那条（带 `tool_use`）已经入历史了，而
  tool 那条还没写——历史停在「有 use、没有 result」的形状上。**而且是黏的**：这段历史
  是会话的一部分，每轮都原样发出去，于是每轮都 400。实测 400 原文：
  `'tool_use' ids were found without 'tool_result' blocks immediately after`。
- **修法**：取消时**把这一轮补完整**再 `raise`——已经跑完的工具保留**真结果**，还没轮到的
  补一条「用户取消了这次工具调用，没有执行结果」（`CANCELLED_RESULT`）。
  **为什么不干脆撤掉那条 assistant 回合**：撤掉的话模型完全不知道刚才调过工具，下一轮
  会从头再来一遍；补一条说明更诚实，而且对「前几个跑完了、后几个没轮到」这种半截状态
  天然成立。
- **反向证明**：撤掉补齐逻辑（等价旧行为）→ 两条用例断言失败
  （`['user','assistant']` vs 期望的 `['user','assistant','tool']`）；装回去即过。
  **注意这个证明的层级**：用例是**直接 `cancel()` 那个 task** 的，证的是「取消走到这里
  时历史是完整的」，不是「用户能按出这个取消」——后者本阶段不存在。

### 硬伤 5 · `read_file` 撞上 FIFO，进程永久挂死

- **症状**：`read_file` 读一个**没有写端**的命名管道，工具在 2 秒时如实报了「超时」，
  `main()` 也确实返回了——**然后进程就再也不动了**，只能 `kill -9`（退出码 137）。
- **根因**：两件事叠在一起。
  1. `open()` 在没有写端的 FIFO 上**永久阻塞**，这是内核行为，不是我们的 bug。
  2. 更要紧的是**「超时生效」和「进程能退出」是两件事**。超时取消的只是那个 `await`，
     线程还在跑。而 `asyncio.run()` 收尾时会调 `loop.shutdown_default_executor()`——
     它把默认线程池里**每一个线程**都 join 掉。一个我们早已放弃的阻塞调用，就这样扣住了
     整个进程。实测的形状很说明问题：`[2.00s] wait_for 到点抛 TimeoutError`、
     `[2.00s] main() 即将 return`（都打出来了），然后**没有任何后续输出**。
- **修法**：分两层，`(a)` 治已知的几种、`(b)` 治这一整类。
  - **(a) 门口拦下**：开读前先 `stat` 看 inode 类型，非普通文件（FIFO / 套接字 / 字符设备 /
    块设备）直接拒绝并说明是什么。三个文件工具（读 / 写 / 改）统一走
    `tool._refuse_if_special`。做法本身也**必须在线程里**：`stat()` 自己碰上网盘挂死
    一样会阻塞，放事件循环上等于把刚绕开的坑换个地方挖。
  - **(b) 换掉线程池**：`asyncio.to_thread` → 自建的 **守护线程** 原语 `_run_blocking`
    （`loop.call_soon_threadsafe` 回填 Future）。守护线程被 `threading._shutdown()` 跳过，
    `asyncio.run()` 也不等它——**进程想退就退**。代价说在明处：被放弃的线程**泄漏一个**，
    这是拿「漏一个线程」换「进程关不掉」。4 处调用（`read_file` 1、`write_file` 1、
    `edit_file` 2）全部换掉——只换 `read_file` 的话，往挂死的网络盘**写**照样能扣住进程。
- **成效**：FIFO / `/dev/zero` / `/dev/random` 一律 **0.00 秒**返回结构化错误，进程退出码
  **0**。对照组未回归：目录仍报「是一个目录，不是文件」、普通文件照常读、**新建**
  不存在的文件照常、**软链到普通文件**照常（`stat` 跟链接）。
- **反向证明**：
  - 撤掉层 (a) → FIFO 用例**把整个 pytest 挂死**（外层 25 秒硬超时 → 退出码 **137**，
    连 pytest 的收集信息都没输出）；字符设备用例断言失败，旧行为下它**真的读回了 256KB
    的 `\x00`**，还返回 `is_error=False`。
  - 撤掉层 (b) 的性质（`daemon=True` → `False`）→ 守护线程用例失败。
  - 另做了一组 A/B，同一个「永不返回的阻塞调用」：`to_thread` 侧 2.00 秒超时→`main()` 返回
    →**再无输出**→退出码 **137**；`_run_blocking` 侧前缀**逐字相同**，差别只在最后一步
    ——`asyncio.run` 正常返回，退出码 **0**。

### 硬伤 6 · `bash` 输出超限时，没人告诉模型是谁杀的

- **症状**：跑一条输出超过 256KB 的命令，模型只看得到 `exit_code: -9`——一个它没发过的
  信号退出码，正文里**没有一个字**解释。
- **根因**：`slurp` 读满 `_MAX_PIPE_BYTES` 就 `_kill_tree`（这本身是必须的：不杀的话
  管道缓冲区填满，子进程阻塞在写 stdout 上，整个工具卡死到超时）。但「我们发过 SIGKILL」
  这个事实**只存在于我们的代码里**，没往结果正文里写。模型于是只能猜命令为什么崩，
  然后去改命令的**逻辑**——而该改的是命令的**范围**。
- **修法**：`slurp` 多返回一个「是不是读满上限才停的」，正文里据此加一段说明，写清三件事
  ——**谁干的**（我们发的 SIGKILL）、**为什么**、**接着怎么办**（缩小范围，如加 `| head -100`）。
  位置排在 `exit_code` **紧后面**：`_truncate` 是**从尾部**切的（`text[:max_chars]`），
  写在前面的东西无论输出多长都不会被截掉，而这条说明恰恰是最需要在场的那种。
  另外在工具的 `description()` 里也补了一句。
- **反向证明**：关掉这段说明重跑新用例 → 断言失败，旧行为下正文就是光秃秃的
  `exit_code: -9\nstdout:\ny\ny\ny…`。
- **意外收获**：真机跑的时候，模型**读了 `description()` 那句，事前就绕开了**——它把
  `yes` 主动改成 `yes | head -5`，并解释「直接裸跑会被工具在 256KB 处截断，没有意义」。
  强迫它原样跑之后，它拿到提示又正确区分了**两层截断**（256KB 的读取上限 vs 30000 字符的
  展示上限）：「结尾的 `[truncated]` 是另一层截断，具体截到多少我说不准」。
  **写进描述里的护栏，比写在错误里的护栏早一步生效。**

### 这一轮真正学到的东西

**「超时生效了」不等于「进程能退出了」。** 这是硬伤 5 的全部教训。超时能取消 `await`，
但取消不了一个正在阻塞的线程；而进程收尾时会去 join 那些线程。判断一个「卡住的调用」
有没有真的被解决，要看的不是 `wait_for` 抛没抛 `TimeoutError`，而是**进程最后退没退出去**。
这和 T19 那条（「丢线程/加超时对同步 C 调用无效」）是一对：那条说的是**线程抢不到 GIL**，
这条说的是**线程根本没人管得住**。

**另一条**：护栏可以写在**事前**，也可以写在**事后**。硬伤 6 原本只打算补事后说明，
顺手在 `description()` 里加了一句，结果真机上模型**先读了描述**、直接绕开了那条路。
工具描述是模型唯一的「使用说明书」，值得当成产品界面来写。

## T21 界面修复记录（2026-09-23）

v2 交付后居居看真界面提了三点。查下来是**三个不同的根因**，全都不是我们写的逻辑，
而是 Textual 的默认行为撞上了我们的 CSS。

### 问题 1 · 输入框里是个白方块（该是根细竖线）

**根因**：`textual/widgets/_text_area.py` 的 DEFAULT_CSS 里，聚焦时

```
&:focus .text-area--cursor {
    color: $input-cursor-foreground;     /* #121212 */
    background: $input-cursor-background; /* #E0E0E0 */
    text-style: reverse;
}
```

——**反色方块**。Textual 里光标就是「把那一格整个刷成另一个颜色」，没有细线这个形态。

**顺带查出的第二件事**：`cursor_blink` 默认是开的，而它闪的方式就是这一格「有 / 无」。
每 0.25 秒采样 8 次，结果是 **4 次白块、4 次完全看不到光标**——居居说的「总是不聚焦」
就是这个，不是他看错。

**修法**：

- `PromptArea.cursor_blink = False`：不闪了。闪烁本来是为了在满屏光标里指出「键盘现在
  打给谁」，Qicode 只有一个位置固定的输入框，闪动只带来干扰。
- CSS 把 `TextArea .text-area--cursor` 改成 `color: $accent; text-style: underline;
  background: ansi_default`——**压在字上**的那一格画橙色下划线，字还在。
- `PromptArea.render_line`：**那一格本来是空白**时（空输入、或光标在行尾，也就是打字时
  绝大多数时刻）把那一格换成 `▏`（U+258F 左八分之一块），得到一根真正的细竖线。

**为什么只在空白格画 `▏`**：Textual 是「一格一个字符」的网格，画不出终端原生光标那种
**画在格子边界上、不占格子**的竖线。在一格有字的位置塞 `▏`，那个字就被吃掉——光标移到
`hello` 中间会显示成 `hell▏ world`，看着像文本丢了。所以按「那一格有没有内容」分两路：
空白格换 `▏`（一个字符都不丢），有字格走 CSS 下划线（字完好）。这是**能力上限，不是偷懒**。

### 问题 2 · 一有字就冒出一个近黑色的方块

**根因**：TextArea 的 DEFAULT_CSS 里还有一条

```
& .text-area--cursor-line { background: $boost; }
```

而 `$boost` = `#FFFFFF0A`——**带 alpha 的** 4% 白。alpha 色落地要找个底色去叠，而
`#input` 的 `background` 是 `transparent`，于是它叠在了「透明黑」上：`255 × 10/255 = 10`，
合出来正好是 `#0A0A0A`。**光标所在那一整行被涂上一层近黑**。

空输入时看不见，是因为占位符那条渲染路径把它盖住了——所以这个框只在打字时才冒出来。

定位方式：monkeypatch `Strip.apply_style`，一旦发现某个 segment 的背景是 `(10,10,10)`
就打印调用栈，栈顶直接指到 `_text_area.py:1629`；再打出 `TextArea._theme.cursor_line_style`
就看到了 `bgcolor=#0a0a0a` 的来源。

**修法**：`PromptArea.highlight_cursor_line = False`。整行高亮本来就是编辑器语义，
聊天输入框不需要。

### 问题 3 · 背景是应用自己画的黑框框

**根因**：`App.ansi_color` 默认 `None → theme.ansi`（深色主题为 `False`），Textual 会把
「终端默认背景」解析成主题写死的 `$background`（`#121212`），整个界面据此刷底色。

**修法**：`QicodeApp.ansi_color = True`——**一行**。

实测（`App.run_test` 里直接读）：

| `ansi_color` | `App.screen.styles.background` | 含义 |
|---|---|---|
| `False`（原） | `Color(18, 18, 18)` | `#121212`，自己画的底 |
| `True`（现） | `Color(0, 0, 0, ansi=-1)` | `ansi_default`，输出 `\x1b[49m` |

对照实验：纯文本（不含任何样式）在 tmux 里 `capture-pane -e` 抓到的**一个转义都没有**，
所以「某行没有 `48;2;…`」可以反过来证明它用的是终端自己的背景。

### 验证

| 项 | 方式 | 结果 |
|---|---|---|
| 四个门禁 | ruff check / ruff format / mypy / pytest | 全绿，**264 passed** |
| 空输入 | tmux 90×22 实跑，抓转义 | `❯ ▏Send a message...`，输入行**零背景转义** |
| 有字（光标在行尾） | 同上 | `❯ hello▏`，零背景转义 |
| 光标压在字上 | 送两次 `Left` | `hel` + `\x1b[4m\x1b[38;2;254;166;43m` + `l`——下划线 + 橙色，**字完好** |
| 提交一轮（请求失败） | 同上 | `● hello` / `● Error code: 502` 都在，对话区零背景转义 |
| 整屏背景 | `grep -c 48;2` | 全屏仅 **6 处**，全在**启动横幅**的像素画里（那是刻意画的图案） |
| 多 provider 选择界面 | 另起 2 provider 配置 | 正常；那几处背景色来自主题的 `$primary` / `$boost`，与本次改动无关 |
| 退出回放 | `Ctrl+C` | `● hello` 正常回放到主屏幕 |

### 顺带修掉的一处小瑕疵

空输入时光标在第 0 格，`▏` 会把占位符的首字符吃掉，显示成 `❯▏end a message...`。
占位符改成 `" Send a message..."`（**开头故意一个空格**）当「光标位」，就成了
`❯▏Send a message...`，一个字不少。不是占位符写错了。

---

## T22 模型不吃工具定义时的运行时降级（2026-09-23）

### 起因

居居让「顺便看看 ollama 那个 tools 报错」。复现（`/tmp/qc_ollama` 配本地 ollama，
`qwen2.5vl:7b`）：**每一轮**都是

```
● Error code: 400 - {'error': {'message': 'registry.ollama.ai/library/qwen2.5vl:7b
  does not support tools', 'type': 'invalid_request_error', ...}}
```

也就是说，**配了 ollama 的 Qicode 完全没法用**。而用户看到的只是一句英文报错，
跟他自己做的事毫无关系。

### 根因

`OpenAIProvider.supports_tools` 硬编码 `True`，当时的注释写的是「这条协议没有 thinking
的约束，工具一直可用」。**协议支持 ≠ 模型支持**——这一条把两者当成了同一件事。

### 为什么不能提前问出来（这条决定了修法的形状）

`Provider.supports_tools` 的 docstring 原本写着：

> 做成**显式声明**、由上层据此决定传不传 `tools`，而不是让适配器偷偷把 `tools` 丢掉：
> 后者会让配了 `thinking: true` 的用户发现工具静默失灵，且无处可查

Anthropic 那条链路**做得到**这件事：`thinking: true` 写在配置里，**发请求之前**就知道。
OpenAI 这条路**做不到**：配置里没有这个字段，协议也没有查询接口，唯一的办法是带着
`tools` 发一次、看对方怎么答。

**信息可得性不一样，两条链路的答案就只能有两个来源。** 所以协议那句 docstring 这轮
改成了「一条提前知道、一条撞了才知道」，并写明对上层而言两者是一回事——照旧只看
`supports_tools`，不需要知道它是怎么来的。

### 修法：撞到就摘掉工具重发一次

`stream()` 里 `create()` 外面套了一个最多两轮的循环：

| 条件（三条缺一不可） | 动作 |
|---|---|
| 第 **1** 次请求 | 带着工具去试 |
| 是 `BadRequestError`（400）| 别的状态码不重试 |
| 报文形状是「否定词 + support + tool 挨在本句里」 | 不是这种 400 就原样抛出去 |

命中就：置本地 `dropped_tools`、`tools_param = openai.omit`、**把 `messages[0]` 的
system prompt 换成没有工具的版本**，然后 `continue` 重发。

几处判断连同理由：

1. **允许重发的前提**是那个 400 在 `create()` **当场**就抛了，一个字的正文都还没
   yield 出去——重发不会出现「同一段话说了两遍」。代码里留了一句：哪天有网关先吐正文
   再报这个 400，这个重试就不再安全。
2. **只认 400。** 「不支持工具」是请求格式问题，协议上就该是 400。别的状态码夹着这句话
   （比如网关 500）说明是网关自己有毛病，那时候摘工具等于把毛病盖住。
3. **重试时换掉 system prompt**（第 1 段那次的漏网点，见下）。不换的话第二次请求就成了
   「说明里写着能用工具、参数里却没有工具」——模型对不上这种矛盾的方式是**编**。
4. **两次都失败时报的是第二个错。** 摘掉工具之后仍然失败，说明毛病跟工具有关无关
   （密钥、模型名、网络），那条才是用户此刻真要去解决的。第一次那句我们已经照办了，
   再报它只会把人引到一个改不出结果的方向上。
5. **`_tools_rejected` 在「重试成功」之后才置**，不是一撞上 400 就置。
   「对方说工具不支持」和「毛病真出在工具上」是两回事：重试失败时我们并没有拿到证据，
   这时候把工具永久判死，会让一个只是碰巧撞上别的问题的接入点从此再也用不上工具。
   重试**成功**才是铁证——同一条请求、只少了 `tools`，它通了。（这一条是**写用例时
   才想明白的**，第一版是撞上就置。）
6. **`_NO_TOOLS_PATTERNS` 认形状、不认意思。** `the parameter is not supported and the
   tool argument is wrong` 会被误判（用例里钉着）。之所以还敢用，是因为按第 5 条，
   误判的代价上限只是多花一次往返，随后用户看到的是重试那次的真实报错。

### 上游配套（两处）

- **`supports_tools` 翻成 `not self._tools_rejected`**，于是 `agent` 每轮开头那句
  `defs = ... if supports_tools` **一行都不用改**，下一轮自动就不发工具了；整个会话
  只在第一轮撞那一次 400。
- **`system_prompt(..., tools=False)`** 新增一个开关，无工具时那段「你可以使用工具」
  整段换成另写的一段。**是另写不是反话**，最后一句点名了要禁止的形态：

  > **不要**在回复里假装调用工具——比如写一段形如 `read_file({"path": "a.py"})` 的文字
  > 再接着往下编内容。

  只说「不要假装调用工具」模型不知道「假装」在界面上长什么样。

### 界面提示（不许静默）

`TOOLS_UNAVAILABLE` 拆成两句，因为**下一步该动的地方不搭界**：

| 常量 | 成因 | 什么时候能说 |
|---|---|---|
| `TOOLS_UNAVAILABLE_THINKING` | 配置开着 thinking，去**改配置** | 开局就知道，进门就说 |
| `TOOLS_UNAVAILABLE_BY_MODEL` | 模型不吃工具定义，去**换模型** | 撞了才知道，这一轮跑完才说 |

合成一句「工具暂不可用」等于把用户丢在原地：他知道坏了，但不知道该动哪儿。

`_warn_if_tools_unavailable(notice)` 三个调用点：`on_mount` / `_select_provider`
（开局那类）、`_end_turn`（撞出来那类，放在那儿因为它是正常答复和出错两条收尾路径
**唯一**的汇合点）。一个 `_tools_warned` 保证**整个会话只说一次**——成因一旦成立就不会
撤销，每轮刷一遍只是噪音。

### 写用例时抓出来的三件事

**① provider 里一个真 bug**：见修法第 5 条，判死时机从「撞上」挪到「重试成功」。

**② 我自己的注释在说谎**（同 T20 那次的性质）。`_NO_TOOLS_PATTERNS` 上面原本写着
「全文匹配会把 `max_tokens is not supported, invalid tool arguments` 也算进来」。
写用例一撞，**它根本不匹配**：`supported;` 后面是分号，紧挨的那个 `\s+` 当场把模式断掉。
原话既高估了全文匹配的危害，又没说清正则的真实边界。已改成「认形状不认意思 + 误判代价
有上限」两条。

**③ 第一版测试是假绿的。** 假件把 `messages` **列表对象的引用**存进了记录，而适配器
重试时是原地改 `messages[0]` ——于是两次请求的记录都被改写成新措辞，那条「重试换了措辞」
的用例看着过了却什么都没测。加浅拷贝解决，并在假件里写明原因（真实 SDK 在调用那一刻
就序列化发出去了，本来没有这个别名问题）。

### 反向证明（变异测试）

把修复逐个改坏，确认用例会红——否则「用例通过」说明不了任何事：

| 变异 | 结果 |
|---|---|
| 删掉重试时的 system prompt 替换 | 红 1 条（`..._tells_the_model_it_has_no_tools`） |
| 改回「一撞上就判死」 | 红 1 条（`..._the_second_error_is_the_one_reported`） |
| 完全不重试 | 红 **4** 条 |

三次变异后还原，`diff` 逐字节确认无残留。

### 验证

| 项 | 方式 | 结果 |
|---|---|---|
| 四个门禁 | ruff check / ruff format / mypy / pytest | 全绿，**279 passed**（改前 264，+15） |
| 降级真的发生 | 真机 ollama + 请求轨迹探针 | 第 1 轮 `6 个工具 / 说明：有工具` → 400 → `omit / 说明：无工具`，正常出正文 |
| 只撞一次 | 同探针，第 2 轮 | 只发**一次**请求，直接 `omit / 说明：无工具` |
| `supports_tools` 翻转 | 同探针 | 撞之前 `True`，撞之后 `False` |
| 界面提示 | tmux 100×28 实跑两轮 | 第 1 轮出现 1 次、第 2 轮**不再出现** |
| 提示位置 | 同上 | 在那一轮回复**下面**——用户能把「刚才那句答复是没工具情况下给的」对上号 |
| 无工具措辞真的管用 | 同上，问「读一下当前目录下的文件列表」 | 模型如实答「我无法直接访问…你需要执行 ls 并把结果展示给我」，**没有**编造假的工具输出 |

### 已知不覆盖

- **只看 ollama 的原话**这一种写法做过真机验证，另外几种措辞（`tool calling is not
  supported by this model` 等）只有单测。换网关遇到没见过的写法时，表现会退回改之前的
  老样子——用户仍能看到原始 400，只是不会自动降级。
- **没验过「先吐正文再报这个 400」的网关**（如果有的话），那种情况下重发不安全，
  代码里留了注记但没加判断。
- 界面提示只说一次，如果用户中途改配置重启才可能再看到——这符合预期，但没有用例覆盖
  「换 provider 之后提示要不要重来」这件事（本阶段选择界面的 provider 一旦选定就不换）。
