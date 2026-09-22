"""渲染拼装与自定义控件（F7、F8、F9、F11、F12）。

这一层是**纯函数 + 两个 widget**，不持有任何状态：界面长什么样在这里定死，
什么时候重绘由 `app` 决定。这样渲染规则可以脱离整个 App 单独测。

一条贯穿全篇的约定：**所有这些函数都返回 `Text` / `Group` / `Markdown`，
不返回裸字符串。** 原因是模型回复里出现 `[` 是很常见的事（写代码、写列表、
写注释都可能），而任何交给 `markup=True` 的 widget 的字符串都会被当成 Rich
标记去解析——轻则把内容吃掉，重则抛 `MarkupError` 把界面打崩。`Text` 对象
是已经解析好的，再交给 widget 不会被二次解析，这条坑就绕过去了。
"""

import re

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text
from textual import events
from textual.containers import Horizontal

# Textual 的消息基类跟对话消息重名。这个文件通篇讲的是「对话消息怎么画」，
# 所以 `Message` 留给 `qicode.llm` 那个，Textual 的这个起个明确的别名。
from textual.message import Message as TextualMessage
from textual.widgets import Static, TextArea

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
    """错误文本（F11）。整块红色，与正常内容一眼可分。

    收的是**已经拼好的字符串**，不是异常对象：上游异常的原文可能夹带密钥，
    必须先过一遍 `qicode.redact` 再送到这里（那一层在 `app.py` 里做，
    因为只有它同时握着异常和 provider 配置）。这一层只管画。
    """
    return Text(message, style=ERROR_STYLE)


def streaming_body(reply: str, elapsed: float) -> Text:
    """流式中的正文（**不含**行首圆点——那个在 `ReplyBlock` 的左列）。

    两种形态：
    - 还没拿到第一个增量 → 只显示 `⠋ Imagining… (Ns)`
    - 已有增量           → 正文，计时降为一行次要信息挂在下面

    动画帧由**已用秒数**推出来，而不是靠一个每次都 +1 的计数器。
    这样刷新频率变了（比如以后从 0.1s 调到 0.05s），转速也不会跟着乱。
    """
    frame = SPINNER_FRAMES[int(elapsed * 10) % len(SPINNER_FRAMES)]

    if not reply:
        return Text(f"{frame} Imagining… ({elapsed:.0f}s)", style=DIM)

    return Text.assemble((reply, ""), "\n", (f"{frame} {elapsed:.0f}s", DIM))


class ReplyBlock(Horizontal):
    """对话区里的一条助手回复：**左边一个圆点，右边正文**。

    做成两列是有原因的——「让 `●` 跟正文同行」这件事，前两条路都实测撞过墙：

    1. **Rich 的 `Table.grid`**（左列圆点、右列 markdown）：表是排出来了，但
       **单元格里的 `Markdown` 根本不折行**，整段被压成一行、末尾加省略号截断。
       `expand=True`、`ratio=1`、`padding` 各种组合都试过，毫无区别；而同样这段
       文字直接交给 `Markdown` 是折得好好的。所以问题不在宽度，在 Rich 对表格
       单元格里的 Markdown 不做折行。此路不通。
    2. **把 `● ` 拼进 markdown 源码**：**纯中文长段会被搞砸**。Rich 的折行
       （`rich/_wrap.py` 的 `divide_line`）按空白分词，一整段不含空格的中文就是
       一个「词」；词比整行还宽时，它**先换行再硬折**——于是已经占着第一行的
       `● ` 被晾在那儿，正文从第二行顶格开始。中英混排反而没事（空格把长段切
       成了短词），而纯中文恰恰是最常见的情况。

    分成两列这些都不存在：宽度由 Textual 的布局算准，markdown 在一个确定的
    宽度里渲染，圆点和正文互不干扰。左列宽度 `auto`，所以终端把这个圆点判成
    1 列还是 2 列都不影响布局（`docs/v1/plan.md` 里对宽度漂移的顾虑）。

    顺带还解决了一件事：流式期间和定型之后，圆点都是同一个控件，位置从来
    没变过——回复定型时不会横着跳一下。
    """

    DEFAULT_CSS = """
    ReplyBlock {
        height: auto;
        /* 圆点要对齐正文的第一行，不是垂直居中。 */
        align-vertical: top;
    }

    ReplyBlock > .reply-marker {
        width: auto;
        height: auto;
        /* 右边留一格，就是 `●` 和正文之间的那个空格。 */
        padding: 0 1 0 0;
    }

    ReplyBlock > .reply-body {
        width: 1fr;
        height: auto;
    }
    """

    def __init__(self) -> None:
        marker = Static(Text(MARKER, style=MARKER_STYLE), classes="reply-marker")
        # `expand=True` 是必须的：不展开的话 Static 按内容的**自然宽度**渲染，
        # 正文就不会在容器宽度上折行。
        body = Static("", classes="reply-body", expand=True)
        super().__init__(marker, body)
        self._marker = marker
        self._body = body

    def show_streaming(self, reply: str, elapsed: float) -> None:
        """流式中：反复刷这一块（F8、F12）。"""
        self._body.update(streaming_body(reply, elapsed))

    def show_reply(self, reply: str, elapsed: float) -> None:
        """定型：整段交给 Rich 的 `Markdown` 重新渲染，代码块、列表、强调才正确（F8）。

        末尾附本轮总耗时（F12）。
        """
        self._body.update(Group(Markdown(reply), Text(f"{elapsed:.1f}s", style=DIM)))

    def show_error(self, message: str) -> None:
        """这一轮失败了：整块转红（F11）。

        圆点也跟着变红——错误块是整体一个视觉单元，留着黑圆点加红字反而别扭。
        """
        self._marker.update(Text(MARKER, style=ERROR_STYLE))
        self._body.update(error_block(message))


#: markdown 里能开启一个「块」的行首写法。`marked_markdown` 遇到这些时不能往前拼前缀。
#:
#: 拼接是在**源码**层面做的，而 markdown 的块级语法只看行首。往 `# 标题` 前面塞
#: 两个字符，它就不再是标题，退化成一整段普通文字；往 `- 第一点` 前面塞，
#: 前面那行会变成一个段落、跟后面的列表拆成两块。这几种情况退化成「圆点独占
#: 一行 + 原文照旧」——排版难看一点，但不篡改语义。
_BLOCK_START = re.compile(r"\s*(?:#{1,6}\s|[-*+]\s|>\s?|\d+[.)]\s|`{3,}|\|)")


def marked_markdown(text: str) -> RenderableType:
    """`●` 与一段 markdown 排成一行。

    **只给退出回放的 `transcript()` 用**，对话区里走 `ReplyBlock`——那条路更稳
    （理由见 `ReplyBlock` 的类文档；这里是纯 Rich 环境，摆不出两列，只能拼）。
    代价是纯中文长段下沉时 `●` 会独占一行，回放场景可以接受。
    """
    if _BLOCK_START.match(text):
        return Group(Text(MARKER, style=MARKER_STYLE), Markdown(text))
    return Markdown(f"{MARKER} {text}")


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
            # 版式跟对话区里保持一致，免得回放出来是另一个样子。
            blocks.append(marked_markdown(msg.content))
    return Group(*blocks)
