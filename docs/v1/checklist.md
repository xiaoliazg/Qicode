# 多协议 LLM 终端对话客户端 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为；括号内为验证方式。
>
> **勾选规则**：只有拿到证据的才勾。证据分三类——
> ① `pytest` 用例（自动化，可复跑）；② 真终端实跑（tmux，本轮 2026-09-22 重跑过）；
> ③ 两者都有。**需要 anthropic 真密钥的条目一律留空**，并在后面写清楚为什么没验——
> 本项目当前手上只有 openai 兼容侧（DeepSeek）的密钥，勾了就是撒谎。

## 实现完整性
- [x] 配置加载：合法 `.qicode/config.yaml` 能解析出 providers 列表（证据：`tests/test_config.py` 30 条 + 真机启动进入对话）。(AC1/F1)
- [x] 配置校验：缺密钥 / 非法 protocol / 文件缺失 / YAML 语法错，各自给出一行可读错误、
  退出码 1、无 traceback（证据：四种坏配置各跑一次 `python -m qicode`，输出形如
  `qicode: providers[0].api_key 不能为空`；`tests/test_config.py`）。(AC1/N4)
- [x] 单 provider 直进：仅一条配置时启动直接进入对话（证据：真机单条 deepseek 配置）。(AC2/F2)
- [x] 多 provider 选择：多条配置时出现方向键 `OptionList`，选定后进入对话，状态栏显示其
  name / model（证据：真机两条配置，↓ 一次即走到第二条——说明 `highlighted` 预置生效，
  按一下就有反应；Enter 后状态栏变为 `deepseek-reasoner / deepseek-reasoner`）。(AC2/F2)
- [x] system prompt 带本次的接入点名与模型名（证据：真机问「你是什么模型」，openai 侧答
  「我是 deepseek-chat，跑在「deepseek」这个接入点上」，不是「我不掌握这个信息」；
  两适配器注入位置各有 `tests/test_llm_providers.py` 覆盖）。(AC4/F4)
- [ ] thinking：anthropic 配 `thinking: true` 时启用，且界面不出现任何思考文本。
  **未验：需要 anthropic 真密钥。** 间接证据：`tests/test_llm_providers.py` 覆盖了
  「thinking_delta 被丢弃、不混进正文」这条翻译逻辑。(AC5/F5)
- [x] 流式逐字：回复以纯文本逐字出现（证据：真机长回复肉眼可见逐步输出）。(AC5/F8)
- [x] markdown 定型：回复结束后整段以 markdown 渲染（证据：真机含 `-` 列表 → 渲染成
  `•`；含 python 代码块 → 带代码面板背景 `48;2;18;18;18`；纯中文长段按宽度折行）。(AC8/F8)
- [x] `●` 与正文首行同行（证据：真机对话区里 `● 我是 deepseek-chat，跑在…`；
  下方是代码块时标记对齐整块第一行；回归用例 `tests/test_tui_app.py`）。(AC8/F8)
- [x] 多行输入：**Ctrl+J** 换行、Enter 提交、提交后输入框清空（证据：真机 `C-j` 后输入框
  自动长到两行，Enter 提交，两行作为**同一条**消息发出，输入框清空）。(AC9/F9)
- [ ] Alt+Enter / Shift+Enter 换行。**未验：tmux 发不出这两个键的扩展序列。**
  它们靠 kitty 键盘协议 / xterm modifyOtherKeys 才有独立键名，需要 Kitty / WezTerm /
  Ghostty / 开了对应选项的 iTerm2 手动按一次。代码路径与 Ctrl+J 同一条
  （`PromptArea.NEWLINE_KEYS`），单测覆盖键名集合。(AC9/F9)
- [x] 响应计时：自提交即显示 `Imagining… (Ns)` 且秒数递增，结束后显示总耗时
  （证据：真机连续采样到 `⠴ Imagining… (2s)` → `⠋ Imagining… (3s)`；定型后显示 `0.7s`）。(AC12/F12)
- [x] 错误反馈：错误 key 时，错误在对话区以**红色**（`38;2;255;0;0`）显示、程序不退出、
  可继续下一轮（证据：真机 401 两次都正常显示，第二轮照常发出）。(AC11/F11)
- [x] 退出：`/exit` 与 Ctrl+C 均能安全退出，终端恢复正常、无残留 raw mode
  （证据：真机两种方式各一次，退出后回到 zsh 提示符，输入回显正常）。(AC10/F10/N7)
- [x] 界面布局：启动含吉祥物 banner + 名称版本 + cwd + 就绪提示行 + 输入框（含 `❯` 与
  `Send a message...` 占位符）+ 状态栏（左 name 右 model）（证据：真机启动画面逐行比对）。(AC7/F7)
- [x] 吉祥物常驻轻蹦：每隔约 4 秒往上抬一格再落回；**动的时候横幅高度与右侧那列文字
  纹丝不动**；滚出视野后不再重绘（证据见下方「本轮新增的验收项」）。(AC14/F7)

## 集成
- [x] TUI 通过统一 `Provider` Protocol 驱动协议，切换协议不改变上层交互
  （证据：真机 openai 兼容侧全流程跑通；两适配器翻译逻辑由 `tests/test_llm_providers.py`
  用假 SDK 客户端覆盖）。**anthropic 侧真机未验**（无密钥）。(AC3/N3)
- [x] 多轮上下文携带：先告知信息、后追问，模型能正确引用前文（证据：真机「记住 4271」
  → 隔两轮问「是多少」→ 答 `4271`）；退出再启动后历史为空（真机重启后对话区为空）。(AC6/F6)
- [x] 流式不阻塞：等待/流式期间界面仍响应、不冻结（证据：等待期间每隔 0.1 秒都在重绘——
  转轮帧号与秒数持续推进）。(AC13/N1)
- [x] 对话区可回看 + 退出后内容保留：完成的消息按顺序摞在对话区，内容溢出后自动跟随到底；
  退出后整段对话重放到主屏幕、落进终端回滚缓冲（证据：真机 `/exit` 后 `capture-pane -S`
  能翻到全部 8 轮问答，且不带动画与耗时标记之外的界面元素）。
  **注意**：失败的那一轮不进历史，因此**不会**被回放（只有屏幕上出现过）。
- [x] base_url 覆盖：为 provider 配自定义 `base_url`（兼容端点）可正常收发
  （证据：真机全部对话都是走 `https://api.deepseek.com/v1` 打通的）。(F3)
- [x] 窗口自适应：缩放终端宽度后输入框/状态栏/markdown 不错版
  （证据：真机 58 列时提示行折到第二行、输入框与状态栏按新宽度重排；140 列时正常拉伸）。(N6)
- [x] 会话不满一屏时**不**贴底、内容溢出后才跟随（证据：真机启动时 banner 停在顶部，
  不是悬在屏幕下半截；聊到内容超过一屏后自动跟到底）。(N1)

## 本轮新增的验收项
- [x] 蹦跳不引起回流：真终端逐帧抓 ANSI 还原色块，图案在**框内**上移一行再落回，
  而 50 帧内版本号恒在第 2 行、提示行恒在第 5 行——横幅高度与右列文字都没动。(AC14/F7)
- [x] 滚出视野即停：把横幅彻底顶出视口后连续抓 50 帧（> 一个完整周期），
  带橙色像素（`48;2;255;140;66`）的帧数为 **0**，画面只在输入光标闪烁处有两态。(AC14/F7)
- [x] 工作目录名含 `[` 时原样显示、不崩：`/private/tmp/qicode_a[b]c` 原样渲染成
  `/private/tmp/qicode_a[b]c`（`[b]` 没被当成标签吃掉）；更狠的 `/private/tmp/qicode_x[/]`
  也照常启动、不抛 `MarkupError`。回归用例 `tests/test_prompt.py`。(AC15/N6)
- [x] 错误原文里夹带的密钥不会显示（证据：`tests/test_redact.py` 边界穷举 +
  `tests/test_tui_app.py` 两条界面级用例，一条断言 `api_key` 不出现在屏幕上、
  一条断言错误文本里的密钥被换成 `***`。真机里 DeepSeek 自己把返回的 key 掩成了
  `****beef`，全 key 未出现）。(AC11/N5)

## 编译与测试
- [x] `python -m qicode` 能正常启动（在合法配置下进入 TUI）。
- [x] `ruff check .` 无告警。
- [x] `ruff format --check .` 通过（27 files already formatted）。
- [x] `pytest` 通过：**118 passed**（`test_cli` 6 / `test_config` 30 / `test_conversation` 6 /
  `test_llm_providers` 19 / `test_prompt` 9 / `test_redact` 8 / `test_tui_app` 28 /
  `test_tui_select` 7 / `test_tui_stream` 5）。
- [x] `mypy src/qicode` 通过（15 source files，no issues）。
- [x] 密钥不回显/不打印：对话区与任何输出均不出现 `api_key`（证据：界面级用例 + 真机通读）。(N5)

## 端到端场景
- [ ] 场景 1（anthropic 多轮）：**未验——需要 anthropic 真密钥。**
- [x] 场景 2（openai 流式）：openai 兼容配置（DeepSeek）→ 多轮对话、流式逐字、计时、
  含代码块与列表的 markdown 正确渲染。
- [x] 场景 3（多 provider 选择）：两条配置 → 启动出现列表 → ↓ 选第二条 → 状态栏显示其
  name/model → 正常对话。
- [x] 场景 4（错误恢复）：错误 key 触发 401 → 对话区红色错误、程序不退出 → 再发一条仍正常响应。

## 已知未覆盖
- **anthropic 真机**：`thinking: true` 是否真的开出思考、思考增量是否真的被丢掉，
  只有单测（假 SDK 客户端）覆盖，没有对着真服务跑过。
- **扩展键盘协议的换行键**：Alt+Enter / Shift+Enter 的键名解析依赖终端能力，
  tmux 里发不出来，需要支持 Kitty 键盘协议的终端手动验一次。
- **鼠标/滚轮回看**：对话区内容溢出与自动跟随都验过，但「用户主动往回滚」这一步
  只在 `tests/test_tui_app.py` 里用 `run_test()` 驱动过，没有在真终端里用鼠标滚过。
