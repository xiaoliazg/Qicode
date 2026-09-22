"""`QicodeApp`：界面装配、状态机与消息处理（F2、F7、F9、F10、F11、F12；N1、N7）。

分工：这个文件只管「界面怎么摆、状态怎么转、消息怎么接」，
纯逻辑分别放在 `view.py`（渲染）、`stream.py`（流式驱动）、`select.py`（选择映射）。
"""

import asyncio
import os
import time
from enum import Enum
from typing import ClassVar

from rich.console import RenderableType
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import OptionList, Static

from qicode import __version__
from qicode.config import ProviderConfig
from qicode.conversation import Conversation
from qicode.llm import Provider, new_provider
from qicode.redact import redact
from qicode.tui.select import build_options, pick
from qicode.tui.stream import TICK_INTERVAL, consume
from qicode.tui.view import (
    MascotBanner,
    PromptArea,
    ReplyBlock,
    status_bar,
    user_block,
)

EXIT_COMMAND = "/exit"


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

    def __init__(self, providers: list[ProviderConfig]) -> None:
        super().__init__()
        if not providers:
            # `config.load` 已经挡过一道（providers 非空），但 QicodeApp 是公开类，
            # 手工构造也能走到这。零配置进来会得到一个没有任何条目、也退不出去的
            # 选择界面，与其那样卡住，不如当场说清楚。
            raise ValueError("至少要有一个 provider 配置")

        self.providers = providers
        self.provider: Provider | None = None
        self.conv = Conversation()
        self.cur_reply = ""
        self.turn_start = 0.0
        #: 本轮回复块（对话区里正在被流式写入的那个子节点）。本轮结束后置回 None。
        self._streaming: ReplyBlock | None = None
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
            yield PromptArea(id="input", placeholder="Send a message...")

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

        if self.state is SessionState.IDLE:
            # 必须显式给焦点：Textual 默认把焦点给第一个可聚焦的控件，而对话区
            # 那个 `VerticalScroll` 就是可聚焦的（`can_focus = True`）——那样用户
            # 一进来敲字是敲不到输入框里的。
            self.query_one("#input", PromptArea).focus()

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

    def _start_reply_block(self) -> None:
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
        """
        widget = ReplyBlock()
        self._streaming = widget
        self.query_one("#log", VerticalScroll).mount(widget)

    # ────────────────────────── 选择 provider ──────────────────────────

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """选定 provider，进入对话（F2、AC2）。"""
        event.stop()
        self.provider = new_provider(pick(self.providers, event.option.id))
        self.state = SessionState.IDLE
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

        # 起一个独立 task 跑流式：界面事件循环不被 await 卡住，
        # 等待期间照样能滚动、能重绘（N1、AC13）。
        self._stream_task = asyncio.create_task(self._consume_stream())
        self._timer = self.set_interval(TICK_INTERVAL, self._tick)

    async def _consume_stream(self) -> None:
        provider = self.provider
        if provider is None:
            # 状态机不该允许这种组合；真出现也照样走错误路径，不抛。
            self._finish_with_error(RuntimeError("还没有选定 provider"))
            return

        await consume(
            provider,
            self.conv.messages(),
            on_text=self._on_delta,
            on_done=self._finish_with_assistant,
            on_error=self._finish_with_error,
        )

    def _on_delta(self, text: str) -> None:
        """收到一段正文增量：累加并立刻重绘（F8 的「逐字」观感）。"""
        self.cur_reply += text
        self._refresh_streaming()

    def _tick(self) -> None:
        """定时刷新计时与转轮（F12、N2）。"""
        if self.state is not SessionState.STREAMING:
            return
        self._refresh_streaming()

    def _refresh_streaming(self) -> None:
        if self._streaming is None:
            return
        self._streaming.show_streaming(
            self.cur_reply, time.monotonic() - self.turn_start
        )
        # 回复块每长一行都可能把内容顶出视口——那是「开始跟随」的临界点。
        self._follow_tail()

    def _finish_with_assistant(self) -> None:
        """本轮正常结束：定型渲染 + 入历史（F8、F12、F6）。"""
        elapsed = time.monotonic() - self.turn_start
        reply = self.cur_reply
        block = self._streaming

        if reply.strip():
            if block is not None:
                block.show_reply(reply, elapsed)
            self.conv.add_assistant(reply)
        elif block is not None:
            # 空回复**不进历史**。Anthropic 对 content 为空的消息直接返回 400，
            # 把它存进去会让**下一轮**莫名其妙地失败，而用户完全看不出这跟上一轮有关。
            # 与其埋一颗这种雷，不如当场把这一轮标成失败。
            block.show_error("模型返回了空回复")

        self._end_turn()

    def _finish_with_error(self, err: Exception) -> None:
        """本轮失败：对话区标红，**不退出**（F11、AC11）。"""
        if self._streaming is not None:
            self._streaming.show_error(self._safe_message(err))
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
        """收尾：停表、放掉回复块、回空闲。

        这里**不清空**回复块——它已经被 `_finish_*` 换成定型内容了，从此就是
        对话历史的一部分。所以只是把引用放开，让下一轮去 mount 新的。
        """
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._stream_task = None
        self._streaming = None
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
