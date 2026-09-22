# Qicode V1 Checklist

> **阶段一（V1 对话基座）已结项：48 项全部勾选**（2026-09-22）。
>
> 但「勾选」不等于「验证强度相同」，读的时候请分开看——各项的差别都写在该条目里了：
> - **真机验证**：在真实服务上跑过（主力是本地 Ollama；思考链路相关条目用 DeepSeek 的 Anthropic 端点）
> - **仅单测覆盖**：Anthropic **官方** Claude 侧特有的行为（`refusal` 收尾、`display` 的可选值等）——手上没有官方 key
> - **受本机模型能力限制**：如 AC8「引用被截断内容」那条，`qwen2.5vl:7b` 的回忆能力不足导致演示不成功，换更强模型后应复验
>
> 每一项通过运行代码或观察行为来验证，聚焦系统行为。
> 端到端类条目统一在 **tmux** 中执行（项目约定）：`tmux new -s qicode-test` 启动，
> `qicode -c <测试配置> -p <name>` 进入对话，滚动回看输出判定。
>
> 当前后端：**本地 Ollama**（`http://127.0.0.1:11434/v1`，模型 `qwen2.5vl:7b`）。
> 复验过程按时间记一笔：T12 单测复核 + T13 在 tmux 里对本地 Ollama 重跑 T11 主流程（含配置引导闭环、双协议切换、三条退出路径、AC5 逐字刷新的客观测量），外加一轮对 **DeepSeek 的 Anthropic 端点**的真机验证（用它补齐了思考链路相关的 AC10/AC13/AC18 与场景 B/E）。
> T14：真机验证暴露的思考开关语义缺陷（`thinking: false` 关不掉思考、显式参数被静默丢弃）已修复并复验，AC14 按重述后的口径通过。

## 实现完整性

- [x] config 模块可被调用（验证：`python -c "from qicode.config import ConfigLoader, ProviderConfig, ConfigError"` 导入成功）
- [x] provider 层可被调用且 ABC 不可实例化（验证：`python -c "from qicode.provider import ChatProvider, create_provider, StreamDelta, ProviderError; ChatProvider()"` 抛 `TypeError`）
- [x] session 可被调用（验证：`python -c "from qicode.session import Session, ChatMessage"` 导入成功）
- [x] tui 可被调用（验证：`python -c "from qicode.tui import run_repl"` 导入成功）
- [x] 命令行入口可用（验证：`qicode --help` 显示 `-c/-p/--debug`，退出码 0；`python -m qicode --help` 同样可用）
- [x] 配置校验覆盖 spec 全部规则（验证：坏配置——缺字段/重复 name/非法 protocol/非法 base_url——各给一条命令或测试用例，报错均指向具体字段）
- [x] `${ENV_VAR}` 展开双向正确（验证：AC4 场景，未设置→明确提示变量名；设置→正常请求）
- [x] 配置模板引导（验证：AC2 场景，删配置启动 → 输出含完整可复制模板；照模板建配置后重启成功）
- [x] 坏 YAML 报错带行号（验证：AC3 场景，故意写坏一行 → 提示含行号/字段，无裸堆栈）
- [x] Anthropic 适配器可被调用（验证：`python -c "from qicode.provider import AnthropicProvider"` 导入成功；工厂按 `protocol` 分发——实测 `anthropic` → `AnthropicProvider`、`openai_compatible` → `OpenAICompatProvider`；未知协议抛 `ProviderError` 且提示里列出两个支持项）
- [x] `thinking_params` 类型校验到位（验证：实测报「providers 第 1 项 字段 'thinking_params' 必须是键值对（mapping），实际为 str」，指向字段而非崩溃/静默忽略）

## 集成

- [x] 启动流程（验证：AC1 场景已按 T17 重述——双 provider 配置启动后**直接进入对话、不弹选择菜单**，当前用的是配置里第一个；启动横幅与状态栏都能看到当前 provider 与模型。`-p` 指定别名的路径仍会校验存在性）
- [x] tui → session → provider 链路贯通（验证：tmux 中发一条普通消息 → 收到流式回复，无异常；滚动回看可见用户消息与回复交替）
- [x] 流式逐字打印（验证：AC5 场景。**客观测量**：生成期间以 0.05 秒一次的频率采样 tmux 渲染区，20 秒内捕获到 **283 次内容变化**，相邻变化间隔中位数 0.066 秒——与采样间隔一致，即每个采样点内容都在变，重绘频率已高于测量精度（≥15 次/秒）。若仍是按帧率跳变（旧 `refresh_per_second=4`），可见更新会被卡在约 4 次/秒、大量采样点应保持不变，实测并非如此。单测另断言 refresh 次数 == 增量数）
- [x] 思考块样式区分（验证：AC13 场景，**已用真实服务验证**——DeepSeek 的 Anthropic 端点，`thinking: true`。提「农夫有 17 只羊，除了 9 只以外都跑了还剩几只」→ 思考内容先完整流式输出，正式回复紧随其后；用 `tmux capture-pane -e` 取转义序列比对：**思考行带 `<ESC>[2;3m`（dim + italic），正文行无任何 dim/italic 码**，与规格要求的「灰色斜体」一致。⚠️ **本条曾被我写错并已更正**：早先用一个平凡问题（「1+1」）测 `thinking: false`，得到 0 个思考增量，据此写下「开关确实被服务端尊重」——**这个推断是错的**。换成需要推理的问题再测，`thinking: false` 下有 146 个思考字符，说明思考照样发生。真正的结论见 AC14 条目）
- [x] 显式关闭思考后无思考块（验证：AC14 场景，**真机验证通过**。受控对比（DeepSeek 的 Anthropic 端点，同一道推理题）：`thinking_params: {type: disabled}` → **0 个思考字符、界面无思考块**；同题只写 `thinking: false` 时 187 个字符，对比成立。**本条曾不成立，两个原因都已修**（详见 task.md T14）：① `thinking: false` 的实现只是「不发参数」，服务端仍按自身默认值思考（DeepSeek 默认按需思考、Anthropic 官方 Opus 5 / Fable 5 默认开启），所以旧口径「thinking 未开启就无思考块」在真实服务上是错的；② 用户显式写的关闭参数会被静默丢弃（适配器只在开关为真时才看 `thinking_params`），导致根本没法表达「关闭」。修复后规则为「显式写了就无条件生效」，两条链路各补了一条回归用例。更早我还用一个平凡问题测出「0 个思考增量」并据此下了错误结论，也已更正——教训是验证开关类行为必须用能触发该行为的问题）
- [x] 多轮 thinking 回传不报错（验证：AC10 场景。真实服务上连续多轮对话，每轮都有思考块且回复正常；思考内容不入历史（D11），回传的历史里只有正文，因此不存在「思考块被当成对话内容发回去」的问题）
- [x] 多轮记忆（验证：AC9 场景，告诉 AI 个人信息 → 几轮无关对话后问回 → 答对）
- [x] Esc 截断语义完整（验证：AC8 场景，长回复生成中按 Esc → 立即停止、已输出保留、会话可继续。**两半的可信度不同，如实记录**：①「立即停止 + 半截保留」是实测的（让它从 1 数到 200，Esc 后停在 46 并回到提示符）；②「截断内容进入历史」由单测 `tests/test_repl.py::test_chat_keeps_truncated_text_as_reply` 断言（renderer 返回多少就入库多少）。而「让 AI 引用被截断内容」这条**在本机模型上没能演示成功**：问「刚才数到哪了」它答 19（应为 46），追问「复述上一条结尾」它又编了一段（真的结尾是 `,41,42,43,44,45,46,`）。判断是 `qwen2.5vl:7b` 这个 7B 视觉模型的回忆能力不足，而非链路问题（历史确实在 session 里，同一会话内它准确答出了三轮之前说的幸运数字）。换更强的模型后应复验这条）
- [x] provider 切换保留历史（验证：AC11 场景，实测跨协议切换后模型准确答出切换前问的「递归」；切到坏 provider 再切回同样保留）
- [x] 切到坏 provider 不崩且可恢复（验证：AC12 场景，切到错误地址的 provider 发消息 → 可读错误提示（含原因/建议，不含 api_key）→ 会话存活 → 切回正常后端继续对话）
- [x] 历史消息可翻阅（验证：AC6 场景，输入态按上键 → 回显上一条输入；实测 Ctrl+C 清行后按上键能取回该条）
- [x] 未知斜杠命令有提示不崩溃（验证：AC17 场景，输入 `/foo` → 提示「未知命令」并列出可用命令，会话继续可输入下一条）
- [x] 代码块富文本渲染（验证：AC7 场景，让模型写 Python 代码 → 代码块等宽高亮呈现）
- [x] 架构约束：对话层不碰 HTTP（验证：`grep -rn "http\|OpenAI(" qicode/tui/ qicode/session.py` 无 HTTP 客户端构造/请求代码；HTTP 只出现在 `qicode/provider/`）
- [x] 架构约束：新协议零侵入（验证：`grep -rn "openai" qicode/tui/ qicode/session.py qicode/cli.py` 无 openai 依赖引用——上层只依赖 `create_provider` 工厂）
- [x] 回归用例覆盖两处已修缺陷（验证：`tests/test_interrupt.py` 的「退出后不再抢占 stdin」用例、`tests/test_repl.py` 的「done 事件携带的 error 不被吞掉」用例）
- [x] 退出路径干净（验证：AC16 场景，`/exit`、`Ctrl+D`、连按两次 `Ctrl+C` 三条路径各自实测，均无异常堆栈，退出码均为 0；单次 Ctrl+C 只清输入行不退出，符合约定）
- [x] 未预期错误兜底（验证：用故障注入把 `ConfigLoader.load` 换成抛 `RuntimeError`——不带 `--debug` 时输出「未预期的错误，可用 --debug 查看详情」、无堆栈、退出码 2；带 `--debug` 时打印完整堆栈、退出码 2。两条路径都实测）
- [x] Anthropic 链路与 OpenAI 兼容链路行为一致（验证：AC18 场景，**已在真实服务上跑通**——DeepSeek 的 Anthropic 端点。列出/选中/多轮对话/思考开关/跨协议切换历史保留/Esc 打断/退出，表现与另一条链路无差别，退出码 0。**仍未验证的对象**：Anthropic **官方** Claude——手上没有官方 key，官方侧特有的行为（如 `refusal` 收尾、`display: summarized` 之外的可选值）仍只有单测覆盖。附带确认：我们发往 Anthropic 协议的默认 `thinking` 参数 `{type: adaptive, display: summarized}` 被兼容服务接受，且 `display` 确实生效（思考内容非空串））
- [x] 自定义 `thinking_params` 真的进了请求体（验证：AC19 场景，两条链路各有用例——自定义参数时请求体里是自定义那份、且是**整份替换而非合并**（旧模型用户能用它写回 `{type: enabled, budget_tokens: N}`）；未写时用适配器默认值。另有常量形状用例钉住 Anthropic 侧默认值必须是 `adaptive` 且带 `display: summarized`——前者改错会 400、后者改错界面看不到思考）

## 语法与测试

- [x] 所有模块无语法错误（验证：`python -m compileall qicode/` 无输出错误）
- [x] 全部单元测试通过（验证：`python -m pytest tests/ -v` 全绿）
- [x] 配置示例合法（验证：`python -c "from qicode.config import ConfigLoader; print(len(ConfigLoader.load('config.example.yaml')))"` 输出等于示例中**启用**的 provider 数量。示例里只有本地 Ollama 一条是启用的（唯一不需要密钥的一条），其余三条协议示例以注释给出，原因写在文件头）

## 界面呈现（T16，见 spec.md AC21–AC24）

> 这四条是 2026-09-22 补记的：原始需求是「仿 Claude Code 的 TUI」，但 F2 当初只约束了功能、
> 没约束外观，交付成了朴素的 `› ` 行式 REPL。补记后当日做完（T16），方案见 plan.md 模块四附。

- [x] 启动 banner 与「已进入对话」标识（验证：AC21。进对话时打印 logo（左）+ 程序名/版本/provider·model/思考开关/工作目录（右），末行「已进入对话 · /exit 退出 · Esc 打断」。窄终端降级实测：**40 列**时不画 logo、只打纯文字且不缩进；50 列时仍画 logo（阈值为 48 列）且信息行不折行、无错位）
- [x] 进入前擦干净整屏（验证：AC21 前半段。**不擦的话启动那行的 shell 提示符会留在横幅上方**，照 `(Qicode) wanzg …/Qicode main ❯ qicode` 这种长提示符，看着像「还没进去」——居居指出的。实测 100×30 tmux：启动后第 1 行就是 `Qicode 0.1.0`，启动行完全不见；`capture-pane -S -100`（含回滚缓冲）里 `python -m qicode`／`(Qicode)`／`wanzg` 均搜不到，**可见区与回滚缓冲都擦掉了**。序列 `ESC[2J ESC[3J ESC[H`（顺序有讲究，见 `_clear_screen` 与 tests/test_cli.py），非终端跳过不写控制序列。回归：`/exit` 退出码仍为 0，屏幕正常交还 shell）
- [x] 首 token 前的生成指示（验证：AC23。实测提交后立即出现 `⠙ 正在生成… 1s`，秒数递增（1s→2s），首个增量到达即切换为逐字正文；回复结束后另留一行 `✻ 想了 5.8 秒`。帧的推进靠 refresh_per_second 的兜底心跳——等待首 token 期间没有任何增量来触发重绘，这正是当初保留兜底帧率的用处）
- [x] 输入态底部状态栏（验证：AC22。实测底部显示 ` my-deepseek · deepseek-flash `；`/model my-ollama` 后立即变为 ` my-ollama · qwen2.5vl:7b `。生成期底栏不可见的原因见 plan.md 模块四附 ③。**实测复核**：底部 `▶▶ provider · model · /model 切换 · Esc 打断`；生成期间底栏下线（本项目输入态归 prompt_toolkit、生成态归 rich Live，工具栏只活在输入态）；**回滚不受影响**——往上翻 60 行仍能看到最早那轮对话，「钉住底栏」与「往上翻能看历史」同时成立，而 `▶▶` 在回滚里只出现 1 次、不污染对话记录。（此处曾把「钉底」误判为「必须全屏、会丢回滚」，已更正）
- [x] 输入框边框与提示符（验证：AC24。**输入区钉在屏幕底部**：分隔线 / `❯ ` / 状态行三行固定在最后三行（20 行终端实测落在第 18–20 行），上方用弹性占位撑开——与 Claude Code 同形制。实现是把 `PromptSession` 换成自搭的非全屏 `Application`（原因见 task.md 的重写记录）。横线**铺满整宽**（宽度取自 `app.output.get_size()`，不是 `shutil.get_terminal_size()`——后者会退回 80 列兜底）。输入区**不会留在对话记录里**（`erase_when_done=True`），每轮由 `_read_line` 补一条 `❯ 你问的话` 进对话流：实测连问两轮后，`▶▶` 状态行在整屏只出现 1 次、用户提问记录 2 条）**回归已跑**：Esc 打断实测有效（长散文生成中被切断、会话存活可继续）；`/exit`、`Ctrl+D`、`Ctrl+C×2` 三条退出路径退出码均为 0）

## 界面与选择时机（T17，见 spec.md AC25）

- [x] `/model` 交互式切换模型（验证：AC25。敲 `/model` 弹出面板：标题 + 说明 + 选项列表（`❯` 光标）+ 底部按键提示，列出全部 provider 的名称/模型/协议/思考开关；方向键移动光标、回车切换并提示「已切换到 x（对话历史保留）」、状态栏随之更新；**Esc 取消后 provider 不变**；面板选完被擦除、不留在对话记录里；**面板贴屏幕底部出现、不把上方内容顶上去**；`/model <name>` 直接切换、名字不存在时给提示不崩）
- [x] 启动不弹选择菜单（验证：双 provider 配置启动，菜单出现 0 次，直接进对话、默认用配置里第一个 provider `my-ollama`）

## 收尾回归（每轮界面改造后整条重跑）

> 界面这几轮（T16–T18）动的是**输入机制**与**渲染机制**——`tests/test_interrupt.py`
> 那个「监听线程抢 stdin」的旧缺陷、以及 Ctrl+C 处理，都在这片区域。所以每轮都整条
> 重跑，不挑着跑。

- [x] T16（banner / 生成指示 / 状态栏 / 输入框边框）完成后（验证：重点项逐条实测——AC5 逐字（每增量强制刷新，单测断言 refresh 次数 == 增量数）、AC8 Esc、AC16 三条退出路径、AC1 启动流程、AC11 跨协议切换与历史保留，均通过。其余条目本轮未受影响，T13 已全量复验过）
- [x] T17（去掉启动菜单 + `/model` 面板）完成后（验证：对话、上键历史、Esc 打断、`/exit`、`Ctrl+D`、`Ctrl+C×2` 退出码全 0；`/model` 面板与 `/model <name>` 直接切换均可用。另补一条跨协议历史保留：`my-ollama` 上让其记住 42 → 切到 `my-deepseek` → 正确答出 42）
- [x] T18（进入前擦屏）完成后（验证：对话正常、`/exit` 退出码 0、屏幕正常交还 shell，见上方 AC21 条目）

## 端到端场景

- [x] **场景 A（完整主流程）**：tmux 启动 → 选 provider → 三轮对话（含代码块回复）→ 上键翻历史 → `/model` 切换 → 再一轮对话 → `/clear` → 确认失忆 → `/exit`（验证：全程无异常，每步行为符合 spec；退出码 0）
- [x] **场景 B（思考 + 打断）**：真实服务上 thinking 开启 → 提推理问题看到思考块流式输出 → 再提一个长生成任务（数到 2000）中途按 Esc → **在思考阶段就被切断**并回到提示符 → 会话存活、继续正常对话（验证：AC13 + AC8 组合成立。首次尝试时生成太快（8 秒内就数完了）没打断到，换更长的任务后成功）
- [x] **场景 C（故障恢复）**：配置里含一个 base_url 故意写错的 provider（如 `http://127.0.0.1:9/v1`）→ 切换过去发消息 → 错误可读（含状态码与建议，不泄露 api_key）→ 切回 → 对话继续（验证：N2 全程不崩）
- [x] **场景 D（配置引导冷启动）**：`qicode -c <不存在的文件>` → 输出含模板、退出码 1 → **把错误信息里的模板原样抄成配置文件（零修改）** → `ConfigLoader.load` 直接加载成功，且真启动进入提示符并完成一轮真实对话（验证：AC2 完整闭环。这条能成立的直接原因是 T12.1 修掉了模板陷阱——未注释的那条 provider 不含 `${ENV_VAR}`，否则照抄会立刻撞上「环境变量未设置」）
- [x] **场景 E（双协议并存）**：配置里同时放 `openai_compatible`（Ollama）与 `anthropic`（DeepSeek 端点，真实服务）两个 provider → 启动正确列出各自 model 与 thinking 状态 → `/provider` 在两个方向来回切换均提示「对话历史保留」（此处 `/provider` 是当时的命令名，T17 已并入 `/model`，`/model <name>` 走的是同一条 `session.switch` 通路）→ 切回后对话照常、退出码 0（验证：AC18 的后半段已补齐。**一处如实记录**：切到 Ollama 后问「农夫有几只羊」，7B 的 `qwen2.5vl:7b` 答了 0（应为 9）——同一现象早先在 AC8 也出现过，判断是本机小模型的回忆能力不足，不是历史丢失；历史保留本身由切换提示与早先「切回后答对『递归』」的用例证实）
