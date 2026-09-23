"""`QicodeApp`：界面装配、状态机与消息处理（F2、F7、F9、F10、F11、F12；N1、N7）。

分工：这个文件只管「界面怎么摆、状态怎么转、事件怎么接」，纯逻辑分别放在
`view.py`（渲染）、`select.py`（选择映射）、`qicode.agent`（单轮闭环：调不调工具、
工具怎么执行）里。

v2 起界面**不再写历史**：`Conversation` 由 `qicode.agent` 独家负责，这一层退化成
纯渲染器（`docs/v2/plan.md`「历史由谁写」）。理由是一轮里有 preamble / 工具结果 /
最终答复三条要按**各自的协议格式**入历史，散在渲染层必然写乱。
"""

import asyncio
import os
import time
from enum import Enum
from typing import ClassVar

from rich.console import RenderableType
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import OptionList, Static

from qicode import __version__
from qicode.agent import Agent, Event, Phase, ToolEvent
from qicode.config import ProviderConfig
from qicode.conversation import Conversation
from qicode.llm import Provider, new_provider
from qicode.redact import redact
from qicode.tool import Registry
from qicode.tui.select import build_options, pick
from qicode.tui.view import (
    DIM,
    MascotBanner,
    PromptArea,
    ReplyBlock,
    ToolBlock,
    status_bar,
    user_block,
)

EXIT_COMMAND = "/exit"

#: 计时刷新频率（秒）。0.1s = 10fps：秒数看着是连续跳的，「处理中」的转轮动画
#: 也够顺，又不会把每一帧都变成一次界面重排。
#:
#: 它从 v1 的 `tui/stream.py` 搬来这里——那边整个模块被 `Agent` 取代了，而这一条
#: 讲的纯粹是**界面多久重画一次**，本来就不属于流式那一层。
TICK_INTERVAL = 0.1

#: `supports_tools` 为假时，在对话区打的那一行提示。
#:
#: 必须有这一行：那种接入点压根收不到工具定义，用户让它读文件只会得到一句
#: 「我做不到」，从界面上完全看不出是配置的原因。原因（本阶段 thinking 与工具
#: 互斥）见 `docs/v2/plan.md`。
TOOLS_UNAVAILABLE = "当前配置开启了 thinking，本阶段工具暂不可用"


class SessionState(Enum):
    """会话状态机（`docs/v1/plan.md`）。

    - `SELECTING` —— 多份配置时的选择界面
    - `IDLE`      —— 等用户输入
    - `STREAMING` —— 正在等/收模型的流
    """

    SELECTING = "selecting"
    IDLE = "idle"
    STREAMING = "streaming"


class QicodeApp(App[None]):
    """Qicode 的终端界面。"""

    TITLE = "Qicode"

    # 「默认背景 / 默认前景」原样交给终端，不折算成主题里的具体颜色。
    #
    # 不开这个开关（Textual 的默认值）时，`Color(0, 0, 0, ansi=-1)` 这个「终端默认
    # 背景」会被解析成主题写死的 `$background`——深色主题里就是 `#121212`，于是
    # Qicode 在自己的黑底上画整个界面。终端背景一换（浅色主题、半透明、带图片的），
    # 那块黑就跟周围对不上，看着像「自己搞了个黑框框」。
    #
    # 实测：开关一翻，`App.screen.styles.background` 从 `Color(18, 18, 18)` 变成
    # `Color(0, 0, 0, ansi=-1)`；tmux 抓转义，界面里**一个背景转义都没有**了
    # （对照组：纯文本捕获本来就没有转义），也就是真的用上了终端自己的背景。
    ansi_color = True

    # Ctrl+C 必须**自己抢**，而且必须 `priority=True`。
    #
    # Textual 8.x 的默认行为跟 F10 的期望不一样：App 把 ctrl+c 绑成 `help_quit`
    # （只是弹个提示让你去按 ctrl+q），Screen 绑了 `ctrl+c -> copy_text`，
    # TextArea 还绑了 `ctrl+c -> copy`。也就是说输入框一聚焦，Ctrl+C 就被「复制」
    # 吃掉，AC10 要求的「Ctrl+C 安全退出」直接失效。
    #
    # priority 的含义是：这条 binding 在**焦点控件自己的 binding 之前**被检查，
    # 所以能压过上面那些。代价是输入框里不能再按 Ctrl+C 复制——
    # spec 明确要求 Ctrl+C 退出，这里以 spec 为准（macOS 上仍可用 Cmd+C）。
    # 标注成 ClassVar[list[BindingType]] 有两个原因：一是默认规则 RUF012 会把可变的
    # 类属性当隐患报出来，二是 `App.BINDINGS` 本身就是这个类型，照抄能避开
    # 「list 不变型」引发的类型不兼容（BindingType 是 Textual 导出的联合别名）。
    # 全部按键就这一条：**没有「打断这一轮」的绑定**（Esc 什么也没接），
    # 本阶段不支持中途取消（`docs/v2/spec.md`「不做的事」）。Ctrl+C 是「退出
    # Qicode」，退出时顺带取消正在跑的那一轮——两件事别混为一谈（见 `_quit`）。
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "quit", "退出", priority=True, show=False),
    ]

    CSS = """
    #log {
        height: 1fr;
        padding: 0 1;
        background: transparent;
    }

    /* 对话区里直接 mount 的成品块（启动横幅 / 用户输入 / 错误）。
       宽度撑满容器，长内容才会在正确的宽度上折行。
       助手回复不在此列——它是个 `ReplyBlock`，自带一份 DEFAULT_CSS。 */
    #log > Static {
        width: 1fr;
        height: auto;
    }

    #input-row {
        height: auto;
        max-height: 10;
        border: round $accent;
        padding: 0 1;
    }

    #prompt-symbol {
        width: auto;
        padding: 0 1 0 0;
        color: $accent;
    }

    #input {
        /* TextArea 自带的 DEFAULT_CSS 是 width:1fr; height:1fr —— 那是「填满
           整块编辑器」的取向，直接放进来会把对话区挤没。这里按内容自适应，
           并去掉自带边框（边框由外层 #input-row 承担）。 */
        height: auto;
        max-height: 8;
        border: none;
        padding: 0;
        background: transparent;
    }

    /* 光标压在**字**上的那一格：橙色下划线。
       光标停在**空白格**上时由 `PromptArea.render_line` 画一根竖线 `▏`，
       两条规则各管一半，见那个方法的文档。

       为什么要自己写：Textual 默认在聚焦时把光标画成**反色方块**
       （`textual/widgets/_text_area.py` 的 `&:focus .text-area--cursor`：
       `color: $input-cursor-foreground; background: $input-cursor-background;
       text-style: reverse`），一格白底盖住一个字符，正是居居截图里那个白块。

       `background: ansi_default` 是配套 `ansi_color` 的：让这一格**不要**背景色，
       露出终端自己的底，否则方块光标去掉反色后还会剩一块主题色。
       选择器照抄框架自己的写法（`TextArea .text-area--cursor`）——组件类样式
       就长这样，换成 `#input .text-area--cursor` 不一定能被匹配上。 */
    TextArea .text-area--cursor {
        color: $accent;
        text-style: underline;
        background: ansi_default;
    }

    #statusbar {
        height: 1;
        padding: 0 1;
    }

    #select {
        height: auto;
        max-height: 12;
        border: round $accent;
        margin: 1 2;
    }
    """

    # 状态用 reactive：`watch_state` 会在状态**真的变了**的时候被调用，
    # 显隐和状态栏的同步只需要写一处，散在各处的状态赋值不必各自记得刷界面。
    state: reactive[SessionState] = reactive(SessionState.IDLE, init=False)

    def __init__(self, providers: list[ProviderConfig], registry: Registry) -> None:
        super().__init__()
        if not providers:
            # `config.load` 已经挡过一道（providers 非空），但 QicodeApp 是公开类，
            # 手工构造也能走到这。零配置进来会得到一个没有任何条目、也退不出去的
            # 选择界面，与其那样卡住，不如当场说清楚。
            raise ValueError("至少要有一个 provider 配置")

        self.providers = providers
        #: 工具注册中心。界面**不直接用它**——它只是 `Agent` 的构造参数之一，
        #: 存着是为了每轮新建 agent 时不用再问外面要一遍。
        #:
        #: 名字不叫 `_registry`：`App` 自己有一个同名的 `WeakSet`（存活控件的登记表，
        #: 见 `App._close_all`），盖上去会把框架的内部状态弄坏——mypy 当场就能看出
        #: 类型对不上，但那已经是在替框架报错了，不如一开始就换个名字。
        self.tool_registry = registry
        self.provider: Provider | None = None
        self.conv = Conversation()
        self.cur_reply = ""
        self.turn_start = 0.0
        #: 本轮回复块（对话区里正在被流式写入的那个子节点）。本轮结束后置回 None。
        self._streaming: ReplyBlock | None = None
        #: 本轮**正在执行**的那个工具块。`ev.tool` 的 START / END 两帧靠它配对；
        #: 执行完立刻置回 None（那一块就此定型）。
        self._cur_tool: ToolBlock | None = None
        self._stream_task: asyncio.Task[None] | None = None
        self._timer: Timer | None = None

    # ────────────────────────── 装配 ──────────────────────────

    def compose(self) -> ComposeResult:
        # 对话区：一块一块往下摞，可用滚轮回看（Claude Code 风格）。
        #
        # 这里用的是 `VerticalScroll` 而不是 `RichLog`，是个**刻意的选择**，理由在
        # `_start_reply_block` 的注释里——简单说：正在流式的那块必须是这个容器的
        # 最后一个子节点，否则回复定型时会在屏幕上「跳」一下。
        yield VerticalScroll(id="log")

        # provider 选择列表：先建好，靠显隐切换，不动态 mount/remove
        # （compose 只在启动时跑一次，动态增删反而更绕）。
        yield OptionList(id="select")

        with Horizontal(id="input-row"):
            yield Static("❯", id="prompt-symbol")
            # 占位符开头**故意留一个空格**。
            #
            # 空输入时光标停在第 0 格，而 `PromptArea.render_line` 会把「光标那格
            # 是空白」的情况画成一根竖线 `▏`——不留这个空格的话，被换掉的会是
            # 占位符的第一个字符，屏幕上显示成 `❯▏end a message...`，像是把字吃掉
            # 了。留一格当「光标位」，就变成 `❯▏Send a message...`，一个字不少。
            yield PromptArea(id="input", placeholder=" Send a message...")

        yield Static("", id="statusbar")

    def on_mount(self) -> None:
        # 启动横幅。它不走 `_append`——那一条是给「一次性定型的内容」用的，
        # 而横幅是个会自己换帧的控件（见 `MascotBanner`）。
        self._mount_in_log(MascotBanner(__version__, os.getcwd()))

        if len(self.providers) == 1:
            # 单条配置直进对话（F2、AC1）。
            self.provider = new_provider(self.providers[0])
            self.state = SessionState.IDLE
        else:
            # 多条配置先进选择界面（F2、AC2）。
            self.state = SessionState.SELECTING
            option_list = self.query_one("#select", OptionList)
            option_list.add_options(build_options(self.providers))
            # 先把光标落在第一条上。
            #
            # `OptionList.highlighted` 初值是 `None`（Textual 8.x `_option_list.py`
            # 里写死的 reactive 默认值），而 `action_cursor_down` 在无高亮时是
            # 「移到第一个可选条目」——于是用户第一次按 ↓ 会觉得**没反应**
            # （光标只是从「没有」变成了第一条），要按第二下才真的往下走。
            # 顺手也会让 Enter 直接可用（选中第一条），跟绝大多数选择界面一致。
            option_list.highlighted = 0
            option_list.focus()

        # `state` 用 init=False 且单 provider 时值没变（本来就是 IDLE），
        # watcher 不会触发，所以这里必须显式同步一次。
        self._sync_chrome()
        self._warn_if_tools_unavailable()

        if self.state is SessionState.IDLE:
            # 必须显式给焦点：Textual 默认把焦点给第一个可聚焦的控件，而对话区
            # 那个 `VerticalScroll` 就是可聚焦的（`can_focus = True`）——那样用户
            # 一进来敲字是敲不到输入框里的。
            self.query_one("#input", PromptArea).focus()

    def _warn_if_tools_unavailable(self) -> None:
        """工具不可用时，在对话区明说一句（`TOOLS_UNAVAILABLE`）。

        两处调用：`on_mount`（单条配置直接进对话）和 `_select_provider`（多条的选完之后）。
        只看 `on_mount` 是不够的——多配置时那一刻 `self.provider` 还是 None，
        等用户选完才有值，提示就漏了。
        """
        if self.provider is not None and not self.provider.supports_tools:
            self._append(Text(TOOLS_UNAVAILABLE, style=DIM))

    def watch_state(self, _old: SessionState, _new: SessionState) -> None:
        self._sync_chrome()

    def _sync_chrome(self) -> None:
        """按当前状态切换各区显隐，并刷新状态栏。"""
        selecting = self.state is SessionState.SELECTING

        # 选择阶段只留列表，其余全收起来（docs/v1/task.md T11）。
        for widget_id in ("#log", "#input-row", "#statusbar"):
            self.query_one(widget_id).display = not selecting
        self.query_one("#select").display = selecting

        self.query_one("#statusbar", Static).update(status_bar(self.provider))

    # ────────────────────────── 对话区 ──────────────────────────

    def _append(self, block: RenderableType) -> None:
        """往对话区末尾追加一块已经定型的内容。

        **这里不 `await`**：`Widget.mount()` 内部当场就把控件注册进父节点了
        （见 Textual 源码 `widget.py` 的 `mount()`），返回的 `AwaitMount` 只是用来
        等布局完成的。我们不等——反正下一帧就会画出来，而 `submit()` 是同步的。
        """
        # `expand=True` 是必须的，不是随手加的：不展开的话 Static 会按内容的
        # **自然宽度**渲染，一段长中文就不会在容器宽度上折行，而是直挺挺地伸出屏幕。
        # （助手回复不走这里，它是个 `ReplyBlock`，那份 expand 在 `view.py` 里。）
        self._mount_in_log(Static(block, expand=True))

    def _mount_in_log(self, widget: Widget) -> None:
        """把控件挂到对话区末尾，并决定要不要开始跟随。"""
        self.query_one("#log", VerticalScroll).mount(widget)
        self._follow_tail()

    def _follow_tail(self) -> None:
        """内容**真的超出视口之后**，让对话区自动跟到底。

        为什么不干脆在 `on_mount` 里 `anchor()` 完事——因为 Textual 的锚定是
        **无条件贴底**的。`_compositor.py` 每次布局都算

            new_scroll_y = 内容底部 - 容器高

        然后 `set_reactive` 直接写进去（绕过了 `validate_scroll_y` 的 clamp）。
        内容比视口矮时这个值是**负数**，于是整块内容被往下推、贴着底排：

        - 启动时 banner 悬在屏幕下半截，上面空一大片；
        - 更要命的是，最后一块只要长高一行，**上面所有内容都被顶上去一行**。
          流式正文和定型后的 markdown 折行高度经常不一样，所以这一步就会看见「跳」。

        所以锚定要等到 `max_scroll_y > 0`（即内容真的溢出）再挂。那之后贴底本来
        就是想要的行为：来新内容自动跟到底，而用户往上滚时 `_anchor_released`
        会让它自动让位（`Widget.anchor()` 的原话是 "until the user moves the
        scroll position"），滚回底部又自动恢复——正是终端 scrollback 的手感。

        挂上之后 `_engage_tail_anchor` 每次都只做一次判断就返回，代价可以忽略。
        """
        # 立刻判一次：多数时候布局已经跑过，这一下就定下来了。
        self._engage_tail_anchor()
        # 再补一次「下次布局之后」的检查。`mount()` / `update()` 只是把重绘排进
        # 队列，内容刚变的那一刻 `max_scroll_y` 还是旧值（多半还是 0）。整段回复
        # **一口气**到达时中间没有别的帧，只有这一下能把它接住。
        self.call_after_refresh(self._engage_tail_anchor)

    def _engage_tail_anchor(self) -> None:
        """内容真的溢出视口了就把锚定挂上；已挂上、或还没溢出，都什么都不做。"""
        log = self.query_one("#log", VerticalScroll)
        if log.is_anchored or log.max_scroll_y <= 0:
            return
        log.anchor()

    def _start_reply_block(self) -> ReplyBlock:
        """开一块空的回复块，作为流式文字的落点。

        这是整个「不跳版」的关键，值得说清楚。

        以前对话区是 `RichLog`，流式文字画在它**下面**另一个独立的 `#streaming`
        Static 上。于是有个很怪的观感：回复生成时是贴着输入框的，一旦定型写进
        `RichLog`，**整段会跳到上面去**——因为历史不满一屏时 `RichLog` 是从头顶
        开始排的。

        现在回复块本身就是对话区的**最后一个子节点**，跟历史在同一条流里。
        流式时刷它、定型时也刷它，控件自始至终没挪过窝，位置自然不变。

        （版式为什么要做成两列，见 `ReplyBlock` 的类文档——那是踩了两次坑
        之后才定下来的。）

        返回值就是刚建好的那块，省得调用方再 `self._streaming` 一次——
        `_finish_with_error` 要按需补块，拿得到手更顺。
        """
        widget = ReplyBlock()
        self._streaming = widget
        self.query_one("#log", VerticalScroll).mount(widget)
        return widget

    def _start_tool_block(self) -> ToolBlock:
        """开一块工具块，作为这次调用在对话区里的落点。

        跟 `_start_reply_block` 同一个套路（直接 `mount()`、不 `await`，
        理由见 `_append`）：挂上去之后它就是对话区最后一个子节点，
        后面的增量自然会排在它下面。
        """
        widget = ToolBlock()
        self._cur_tool = widget
        self.query_one("#log", VerticalScroll).mount(widget)
        return widget

    # ────────────────────────── 选择 provider ──────────────────────────

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """选定 provider，进入对话（F2、AC2）。"""
        event.stop()
        self.provider = new_provider(pick(self.providers, event.option.id))
        self.state = SessionState.IDLE
        self._warn_if_tools_unavailable()
        self.query_one("#input", PromptArea).focus()

    # ────────────────────────── 提交与流式 ──────────────────────────

    def on_prompt_area_submitted(self, event: PromptArea.Submitted) -> None:
        event.stop()
        self.submit(event.value)

    def submit(self, text: str) -> None:
        """处理一次提交（F9）。"""
        # 流式期间不接受新提交（F9）。这里直接返回、**不清输入框**——
        # 用户要是手快在等待时又敲了一行并按了 Enter，内容得给他留着。
        if self.state is not SessionState.IDLE:
            return

        if text.strip() == EXIT_COMMAND:
            self._quit()
            return

        if not text.strip():
            # 空输入不发。发出去只会从对端换回一个 400，不如原地忽略。
            return

        self.conv.add_user(text)
        self._append(user_block(text))
        self.query_one("#input", PromptArea).clear()

        self.cur_reply = ""
        self.turn_start = time.monotonic()
        self.state = SessionState.STREAMING

        # 先摆好回复块，再立刻画一帧「等待中」，别等定时器。
        # `set_interval` 的第一次触发要等满一个 TICK_INTERVAL（0.1s），光靠它的话
        # 用户按下 Enter 之后会有最多 0.1 秒**屏幕上什么都没变**——如果对端首字
        # 又来得慢，这段空白就会被读成「卡住了」。
        self._start_reply_block()
        self._refresh_streaming()

        # 起一个独立 task 跑这一轮：界面事件循环不被 await 卡住，
        # 等待期间照样能滚动、能重绘（N1、AC13）。取消也取消这个 task
        # （见 `_quit`），`async for` 会把 CancelledError 一路送进适配器。
        self._stream_task = asyncio.create_task(self._consume_agent_events())
        self._timer = self.set_interval(TICK_INTERVAL, self._tick)

    async def _consume_agent_events(self) -> None:
        """跑完一轮闭环，把 `Agent` 的事件流翻译成界面动作（F8、F9、F11）。

        **这一层不写历史**——`Agent` 已经写过了（`docs/v2/plan.md`「历史由谁写」）。
        v1 在这里既收流又 `conv.add_assistant(...)`，v2 只负责画。
        """
        provider = self.provider
        if provider is None:
            # 状态机不该允许这种组合；真出现也照样走错误路径，不抛。
            self._finish_with_error(RuntimeError("还没有选定 provider"))
            return

        try:
            async for event in Agent(provider, self.tool_registry).run(self.conv):
                self._on_agent_event(event)
        except asyncio.CancelledError:
            # 走到这里只有一条路：`_quit` 里的 `_cancel_stream()`（也就是用户按了
            # Ctrl+C 要退出）。**没有「打断这一轮」的按键**——Esc 没有绑定，本阶段
            # 不支持中途取消（`docs/v2/spec.md`「不做的事」）。所以别把这里读成
            # 「用户想中断当前回复」：他想要的其实是关掉 Qicode。
            #
            # 但取消仍然要靠异常传播，**吞掉就等于把取消吞掉了**（`agent.Agent.run`
            # 的文档），这里只负责让它继续往上走——`_quit` 后面那句 `exit()` 还等着
            # 一个干净的收尾。
            raise
        except Exception as exc:  # noqa: BLE001
            # agent 内部已经把适配器失败和工具失败都翻成了事件，能到这里的是**我们
            # 没料到的**异常（多半是界面自己抛的）。同样翻成错误收尾，不让它冒出去
            # 打崩 App——v1 的 `stream.consume` 也是这么兜的，这条边界不能因为换了一层
            # 就撤掉。
            self._finish_with_error(exc)

    def _on_agent_event(self, event: Event) -> None:
        """按 `Event` 里**非 None 的那个字段**分派（`docs/v2/plan.md` 的分派表）。

        四态互斥，顺序无所谓，但工具那一支要走两条路（START / END），所以单独拎出来。
        """
        if event.tool is not None:
            if event.tool.phase is Phase.START:
                self._begin_tool(event.tool)
            else:
                self._finish_tool(event.tool)
        elif event.text:
            if self._streaming is None:
                # 两种时候会走到这：本轮的**第一段**正文（`submit` 已经开过块了，
                # 所以只有刚跑完工具时才会为空），以及工具之后的新一段。
                self._start_reply_block()
            self.cur_reply += event.text
            self._refresh_streaming()
        elif event.done:
            self._finish_with_assistant()
        elif event.err is not None:
            self._finish_with_error(event.err)

    def _begin_tool(self, tool: ToolEvent) -> None:
        """一个工具开始执行：先把当前回复块**定型**，再挂上工具块（F8）。

        定型这一步是 preamble 的落点：模型常常先说一句「我读一下 a.py」再去调工具，
        那句话属于这一轮的开场白，得留在对话区，不能等最终答复出来时被冲掉。

        反过来，preamble 是**空的**时候要把那个块摘掉——它是 `submit` 时开的
        （为了在首个增量到达前显示转轮），模型一个字没说就直接调了工具，
        留着就是一个孤零零的圆点。
        """
        block = self._streaming
        self._streaming = None
        if block is not None:
            if self.cur_reply.strip():
                block.show_reply(self.cur_reply, time.monotonic() - self.turn_start)
            else:
                # 不 await：`remove()` 把摘除动作排给下一条消息，这里不等它。
                # 摘除和下面这次 mount 会落在同一帧里（中间没有一次重绘），
                # 所以不会看见「空块和工具块同时在场」的中间态。
                block.remove()
        self.cur_reply = ""

        self._start_tool_block().show_running(tool.name, tool.args)
        self._follow_tail()

    def _finish_tool(self, tool: ToolEvent) -> None:
        """一个工具执行完：把结果摘要挂到它那一块下面，这一块就此定型（AC11）。

        后面要是还有正文，`_on_agent_event` 会另开一个新的回复块——所以这里
        除了把 `_cur_tool` 放掉，不需要再做别的。
        """
        if self._cur_tool is None:
            # 没经过 START 的 END 不该出现（agent 保证成对）；真出现也不崩。
            return
        self._cur_tool.show_result(tool.name, tool.args, tool.result, tool.is_error)
        self._cur_tool = None

    def _tick(self) -> None:
        """定时刷新计时与转轮（F12、N2）。"""
        if self.state is not SessionState.STREAMING:
            return
        self._refresh_streaming()

    def _refresh_streaming(self) -> None:
        """把「此刻正在进行的那件事」重画一帧（F12、N2）。

        两种落点：正在跑工具就刷工具块，否则刷回复块。两者不会同时存在——
        `_begin_tool` 定型回复块之后才开工具块，`_finish_tool` 之后下一次正文增量
        才会再开回复块。

        转轮那一帧只对**还没定型**的块有效（`ToolBlock.show_progress` 自己会跳过），
        所以这里不需要再判一次。
        """
        elapsed = time.monotonic() - self.turn_start

        if self._cur_tool is not None:
            self._cur_tool.show_progress(elapsed)
            return

        if self._streaming is None:
            return
        self._streaming.show_streaming(self.cur_reply, elapsed)
        # 回复块每长一行都可能把内容顶出视口——那是「开始跟随」的临界点。
        self._follow_tail()

    def _finish_with_assistant(self) -> None:
        """本轮正常结束：定型渲染（F8、F12）。

        **这里不写历史**（v1 在这个函数里 `self.conv.add_assistant(reply)`）：
        agent 已经写过了，两边都写的话同一句答复会在历史里出现两次，
        下一轮请求就会带着重复的上下文发出去。

        空回复的处理也交给了 agent：它压根不会发 `done`，而是发一条带
        `EMPTY_REPLY` / `TOOL_LIMIT` 的 `err` 事件，走 `_finish_with_error`。
        所以走到这里时 `cur_reply` 必定非空。
        """
        block = self._streaming
        if block is not None:
            block.show_reply(self.cur_reply, time.monotonic() - self.turn_start)
        self._end_turn()

    def _finish_with_error(self, err: Exception) -> None:
        """本轮失败：对话区标红，**不退出**（F11、AC11）。

        没有正在写的回复块时（比如工具跑完、模型却没给最终答复）得**现开一块**——
        否则这条错误提示没有落点，用户只会看到一个跑完的工具块然后界面回到空闲，
        完全不知道刚才发生了什么。
        """
        block = self._streaming
        if block is None:
            block = self._start_reply_block()
        block.show_error(self._safe_message(err))
        self._end_turn()

    def _safe_message(self, err: Exception) -> str:
        """把异常原文里可能夹带的密钥抹掉，再交给界面（N5）。

        **必须在这里做，不能更晚**：这句文字会被画进对话区，是用户肉眼可见的
        一屏内容——截屏、录屏、共享终端都带得走。上游异常里夹带的凭据不该
        出现在那儿，这一点本身就够了。

        （顺带记一笔边界：失败的那一轮**不会**被 `cli._replay_transcript` 回放，
        因为回放的是 `conv` 里的消息，而错误的轮次压根不入历史。所以这道防线
        守的是屏幕，不是终端的回滚缓冲。哪天让错误也进历史或回放，这里已经挡住了。）

        抹的是**当前配置里所有 provider 的 api_key**，不止当前这个：出错时用户
        多半正要切到另一家去试，那时候的错误信息同样不该带出别家的密钥。
        """
        return redact(str(err), [cfg.api_key for cfg in self.providers])

    def _end_turn(self) -> None:
        """收尾：停表、放掉各块的引用、回空闲。

        这里**不清空**已经定型的块——它们已经被 `show_reply` / `show_result` 换成
        最终内容了，从此就是对话历史的一部分。所以只是把引用放开，让下一轮去 mount 新的。
        """
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._stream_task = None
        self._streaming = None
        self._cur_tool = None
        self.cur_reply = ""
        self.state = SessionState.IDLE
        # 定型后的 markdown 折行高度可能跟流式正文不一样，有可能**就在这一刻**
        # 才第一次顶出视口。再判一次，免得恰好卡在临界点的那一轮不跟到底。
        self._follow_tail()

    # ────────────────────────── 退出 ──────────────────────────

    async def action_quit(self) -> None:
        self._quit()

    def _quit(self) -> None:
        """安全退出（F10、N7）。

        正在流式时先取消那个 task，让 `async for` 抛 CancelledError 走正常收尾、
        关掉 HTTP 流；随后 `exit()` 会让 Textual 还原终端 raw mode。
        """
        self._cancel_stream()
        self.exit()

    def _cancel_stream(self) -> None:
        if self._stream_task is not None:
            self._stream_task.cancel()
