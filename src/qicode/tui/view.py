"""渲染拼装与自定义输入框（F7、F8、F9、F11、F12）。

这一层是**纯函数 + 一个 widget**，不持有任何状态：界面长什么样在这里定死，
什么时候重绘由 `app` 决定。这样渲染规则可以脱离整个 App 单独测。

一条贯穿全篇的约定：**所有这些函数都返回 `Text` / `Group`,不返回字符串。**
原因是模型回复里出现 `[` 是很常见的事（写代码、写列表、写注释都可能），
而任何交给 `markup=True` 的 widget 的字符串都会被当成 Rich 标记去解析——
轻则把内容吃掉，重则抛 `MarkupError` 把界面打崩。`Text` 对象是已经解析好的，
再交给 widget 不会被二次解析，这条坑就绕过去了。
"""

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text
from textual import events

# Textual 的消息基类跟对话消息重名。这个文件通篇讲的是「对话消息怎么画」，
# 所以 `Message` 留给 `qicode.llm` 那个，Textual 的这个起个明确的别名。
from textual.message import Message as TextualMessage
from textual.widgets import TextArea

from qicode.llm import Message, Provider

# 消息行首的圆点，用户输入、助手回复、错误三处共用，只靠颜色和排版区分。
MARKER = "●"
MARKER_STYLE = "bold"
ERROR_STYLE = "bold red"
DIM = "dim"

# 「处理中」指示的动画帧（N2 要求带动画）。
#
# 用盲文点阵字符：它们在等宽字体里的显示宽度稳定是 1 列。这跟
# `docs/v1/plan.md` 里像素画放弃 `█` 是同一个取舍——宽度会飘的字符，
# 每 0.1 秒换一帧就会把整行顶得左右乱跳。
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

#: 在输入框里按下就换行（而不是提交）的键名。理由见 `PromptArea` 的类文档。
NEWLINE_KEYS = frozenset({"alt+enter", "shift+enter", "ctrl+j"})


class PromptArea(TextArea):
    """带提交语义的输入框。

    Textual 8.x 的 `TextArea` 是**纯编辑器**，不是输入框：它的 `_on_key` 里写死了
    `insert_values = {"enter": "\\n"}`，并且对 enter 调用 `event.stop()` +
    `event.prevent_default()`。后果是——键盘事件当场被它吃掉，App 层挂
    `("enter", "submit")` 这类 binding **根本收不到**。
    （这正是 `docs/v1/task.md` T10 里那条写法在 Textual 8.x 上不成立的原因，
    那个写法的年代 TextArea 还没有吞掉 enter。）

    所以提交语义只能在这一层自己接：

    - `Enter` → 发 `Submitted` 消息，**不**插换行
    - `Alt+Enter` / `Shift+Enter` / `Ctrl+J` → 插换行（F9 要求的多行编辑）

    实测（Textual 8.2.8）：`enter` / `alt+enter` / `shift+enter` 是三个互不相同的
    键名，且只有 `enter` 会被基类插成换行。这给了我们一个干净的接管点。

    **换行为什么要给三个键**：单个都不够可靠，各自覆盖一类终端。

    - `Alt+Enter` 是 spec（AC9）写的那个。但绝大多数终端给它发的是 `ESC CR`，
      而 Textual 的解析器在 `_xterm_parser.py` 里只在键名长度为 1 时才补 `alt+`
      前缀（`if len(name) == 1 and alt`），`\\r` 的键名是五字符的 `enter`，
      于是 **alt 被丢掉、退化成普通 Enter**——按下去直接就把消息发出去了。
      只有实现了 kitty 键盘协议 / xterm modifyOtherKeys 的终端（Kitty、WezTerm、
      Ghostty、开了对应选项的 iTerm2……）才会把它发成 `CSI 13;3u`，Textual 认作
      `alt+enter`。
    - `Ctrl+J` 是 `LF`（0x0A）。终端在 raw mode 下把 Ctrl+J 原样送出去，不经过
      任何翻译，**哪个终端都一样**。所以它是那个「一定能用」的换行键。
    - `Shift+Enter` 同 Alt+Enter，靠扩展键盘协议才有独立键名；顺手接上，
      免得用户在支持的终端里按了却换不了行。
    """

    class Submitted(TextualMessage):
        """用户按 Enter 提交了输入。"""

        def __init__(self, value: str) -> None:
            # 消息自带内容，handler 不必再去 widget 里捞——这样提交之后
            # 清空输入框的顺序就跟消息内容无关了。
            self.value = value
            super().__init__()

    async def _on_key(self, event: events.Key) -> None:
        if event.key in NEWLINE_KEYS:
            event.stop()
            event.prevent_default()
            # 走公开的 insert()，不用基类那个私有的 _replace_via_keyboard()。
            self.insert("\n")
            return

        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self.text))
            return

        # 其余按键（含普通字符、退格、方向键）原样交给基类。
        await super()._on_key(event)


def user_block(text: str) -> Text:
    """用户输入块。

    不做 `You:` 之类的文字标签（`docs/v1/spec.md` F7 只要求「可区分」），
    靠行首圆点加粗来区分。
    """
    return Text.assemble((f"{MARKER} ", MARKER_STYLE), (text, ""))


def error_block(message: str) -> Text:
    """错误块（F11）。红色，与正常内容一眼可分，且**不退出**。

    收的是**已经拼好的字符串**，不是异常对象：上游异常的原文可能夹带密钥，
    必须先过一遍 `qicode.redact` 再送到这里（那一层在 `app.py` 里做，
    因为只有它同时握着异常和 provider 配置）。这一层只管画。
    """
    return Text(f"{MARKER} {message}", style=ERROR_STYLE)


def assistant_block(reply: str, elapsed: float) -> RenderableType:
    """助手回复的定型块（F8、F12）。

    流式期间是纯文本，结束后整段交给 Rich 的 `Markdown` 重新渲染——
    代码块、列表、强调才正确。末尾附本轮总耗时（F12 后半句）。
    """
    return Group(
        Text(MARKER, style=MARKER_STYLE),
        Markdown(reply),
        Text(f"{elapsed:.1f}s", style=DIM),
    )


def streaming_view(reply: str, elapsed: float) -> Text:
    """流式中的动态区内容（F8、F12、N2）。

    两种形态：
    - 还没拿到第一个增量 → 只显示 `⠋ Imagining… (Ns)`
    - 已有增量           → 显示正文，计时降为一行次要信息挂在下面

    动画帧由**已用秒数**推出来，而不是靠一个每次都 +1 的计数器。
    这样刷新频率变了（比如以后从 0.1s 调到 0.05s），转速也不会跟着乱。
    """
    frame = SPINNER_FRAMES[int(elapsed * 10) % len(SPINNER_FRAMES)]

    if not reply:
        return Text(f"{frame} Imagining… ({elapsed:.0f}s)", style=DIM)

    return Text.assemble(
        (f"{MARKER} ", MARKER_STYLE),
        (reply, ""),
        "\n",
        (f"{frame} {elapsed:.0f}s", DIM),
    )


def status_bar(provider: Provider | None) -> RenderableType:
    """底部状态栏：左边 provider 名，右边模型名（F7(e)、AC2）。

    用 `Table.grid(expand=True)` 做两端对齐——比手工算空格可靠，
    终端宽度一变也不会错位（N6）。
    """
    if provider is None:
        return Text("未选择 provider", style=DIM)

    grid = Table.grid(expand=True)
    grid.add_column(justify="left")
    grid.add_column(justify="right")
    grid.add_row(Text(provider.name), Text(provider.model, style=DIM))
    return grid


def transcript(messages: list[Message]) -> Group:
    """把一次会话的历史拼成可直接打印到终端的块。

    这是给**退出之后**用的（`docs/v1/checklist.md` 的 scrollback 那条要求
    「退出后内容保留在终端历史中」）。

    为什么要专门做这件事：Textual 跑在**备用屏幕**上（Linux 驱动启动时写
    `\\x1b[?1049h`，退出时写 `\\x1b[?1049l`）。备用屏幕没有回滚缓冲，
    退出的一瞬间整屏内容**连同它的滚动历史一起消失**——用户按完 /exit，
    刚才聊的东西一点不剩。`RichLog` 能滚动，但只在进程活着的时候算数。

    所以退出时把历史重新打一遍到主屏幕。这不是把界面内容「复制」出来
    （那样会带上边框、状态栏这些只在交互时有意义的东西），而是按对话
    本身重放一遍：用户说的、模型答的。

    耗时不重放：那是「这一轮等了多久」，事后回看没有意义，只会干扰阅读。
    """
    blocks: list[RenderableType] = []
    for msg in messages:
        if msg.role == "user":
            blocks.append(user_block(msg.content))
        else:
            # 助手回复按 markdown 重新渲染，代码块和列表才对（F8 的同一个理由）。
            blocks.append(
                Group(Text(MARKER, style=MARKER_STYLE), Markdown(msg.content))
            )
    return Group(*blocks)
