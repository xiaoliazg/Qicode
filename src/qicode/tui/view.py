"""渲染拼装与自定义控件（F7、F8、F9、F11、F12）。

这一层是**纯函数 + 三个 widget**，不持有任何状态：界面长什么样在这里定死，
什么时候重绘由 `app` 决定。这样渲染规则可以脱离整个 App 单独测。

一条贯穿全篇的约定：**所有这些函数都返回 `Text` / `Group` / `Markdown`，
不返回裸字符串。** 原因是模型回复里出现 `[` 是很常见的事（写代码、写列表、
写注释都可能），而任何交给 `markup=True` 的 widget 的字符串都会被当成 Rich
标记去解析——轻则把内容吃掉，重则抛 `MarkupError` 把界面打崩。`Text` 对象
是已经解析好的，再交给 widget 不会被二次解析，这条坑就绕过去了。
"""

import re
from typing import Any

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.segment import Segment
from rich.style import Style
from rich.table import Table
from rich.text import Text
from textual import events
from textual.containers import Horizontal

# Textual 的消息基类跟对话消息重名。这个文件通篇讲的是「对话消息怎么画」，
# 所以 `Message` 留给 `qicode.llm` 那个，Textual 的这个起个明确的别名。
from textual.message import Message as TextualMessage
from textual.strip import Strip
from textual.widgets import Static, TextArea

from qicode.agent import preview_args
from qicode.llm import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    Message,
    Provider,
    ToolCall,
)
from qicode.prompt import FRAME_INTERVAL, bounce_offset, render_banner

# 消息行首的圆点，用户输入、助手回复、错误三处共用，只靠颜色和排版区分。
MARKER = "●"
MARKER_STYLE = "bold"
ERROR_STYLE = "bold red"
DIM = "dim"

# 工具调用那一块的圆点换个颜色，跟助手回复一眼分开（F9 的「可区分」）。
# 为什么是青色：助手正文是白/默认色、错误是红、用户输入是加粗，剩下没被占用的
# 显眼颜色里青色在深浅两种终端主题下都够亮。
TOOL_STYLE = "bold cyan"

#: 调用行下面那个「接续」符号，表示这一行是从属于上面那次调用。
RESULT_ELBOW = "⎿"

#: 工具结果摘要最多显示几行（AC11）。
#:
#: 完整内容一个字都没丢——它已经原样回灌给模型、也留在 `Conversation` 里了，
#: 这里截断纯粹是为了别让一次 `cat` 把整屏刷掉。
MAX_SUMMARY_LINES = 8

# 「处理中」指示的动画帧（N2 要求带动画）。
#
# 用盲文点阵字符：它们在等宽字体里的显示宽度稳定是 1 列。这跟
# `docs/v1/plan.md` 里像素画放弃 `█` 是同一个取舍——宽度会飘的字符，
# 每 0.1 秒换一帧就会把整行顶得左右乱跳。
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

#: 在输入框里按下就换行（而不是提交）的键名。理由见 `PromptArea` 的类文档。
NEWLINE_KEYS = frozenset({"alt+enter", "shift+enter", "ctrl+j"})

#: 光标画成一根细竖线用的字符：`▏`（U+258F，左八分之一块）。
#:
#: **为什么必须是「块元素」而不是 `|` 或 `│`。** Textual 是「一格一个字符」的
#: 网格模型，画不出真正的 1px 竖线（那是终端自带光标的特权——它画在**格子的边界**
#: 上，不占格子）。能在格子里表达「细竖线」的只有左八分之一块这类元素：它自己就
#: 占满一格，但左边的八分之一有墨、右边透明，看过去就是一根贴左边缘的竖线。
#:
#: 不用 `|` 是因为它上下留白，多行时会断成一截一截；不用 `▌`（左半块）是因为太粗，
#: 看着就是个方块。宽度都得是 1，否则会把整行顶歪（同 `SPINNER_FRAMES` 那条取舍）。
CURSOR_BAR = "▏"


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

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        # 光标**不许闪**。
        #
        # Textual 的 `cursor_blink` 默认是开的，而它闪的方式是整格反色方块
        # 「有 / 无」地切换。实测（每 0.25 秒采样 8 次）结果是四条白块、四条
        # **完全看不到光标**——用户的感觉就是「这个输入框总是不聚焦」。
        # 闪烁本来是用来在满屏光标里指出「键盘现在打给谁」，而 Qicode 只有一个
        # 输入框、位置固定，闪动带来的只有干扰。
        self.cursor_blink = False

        # 关掉 Textual 内置的「光标所在整行加亮」。
        #
        # `textual/widgets/_text_area.py` 的 DEFAULT_CSS 里有一条
        # `& .text-area--cursor-line { background: $boost; }`，而 `$boost` 是
        # `#FFFFFF0A`——**带 alpha 的** 4% 白。alpha 色要落地就得找个底色去叠，
        # 而 `#input` 的 `background` 是 `transparent`（见 `QicodeApp.CSS`），
        # 于是它叠在了「透明黑」上：`255 × 10/255 = 10`，合出来正好是 `#0A0A0A`。
        # 也就是说，**有字之后整条输入行会被涂上一层近黑色**，在浅色终端上就是
        # 居居说的那个「自己搞的黑框框」。（空输入时看不见，是因为占位符那条
        # 渲染路径把它盖住了，所以这个框只在打字时才冒出来。）
        self.highlight_cursor_line = False

    def render_line(self, y: int) -> Strip:
        """在光标那一格画一根细竖线（`CURSOR_BAR`）。

        基类的绘制方式已经在 `QicodeApp.CSS` 里改过了（橙色下划线），但那是
        「一格一个字符」的极限——**光标压在某个字上时**，下划线画在字底下、
        字还在，可它就是不如一根竖线像光标。而光标停在空白格上时（空输入、
        或者光标在行尾，也就是打字时绝大多数时刻）那一格本来就没内容，
        换成 `▏` **一个字符都不会丢**，能画出跟终端原生竖线光标一样的观感。

        所以这里只在「那一格是空白」时动手，其余情况原样返回、交给 CSS：

        - 空输入（占位符）→ 画 `▏`（占位符首字符那个位置，见下面占位符里的说明）
        - 光标在行尾 → 画 `▏`
        - 光标压在字上 → 不画，CSS 的橙色下划线接手
        """
        strip = super().render_line(y)

        # 失焦时不画。基类也是这个规矩（失焦连方块光标都不画），保持一致。
        if not self.has_focus:
            return strip

        row, col = self.cursor_location
        # 只处理光标所在那一行。`scroll_offset` 是**内容**偏移，减掉才换算成
        # 「当前视口里的第几行」——`y` 是这个坐标系里的值。
        if y != row - self.scroll_offset.y:
            return strip
        visible_col = col - self.scroll_offset.x
        if not 0 <= visible_col < strip.cell_length:
            return strip

        # 这一格有没有东西？`crop` 按**显示宽度**切，宽字符、Tab 都不用自己算。
        # 空格也算「没有东西」——把空格换成 `▏` 不丢任何可见内容。
        cell = strip.crop(visible_col, visible_col + 1)
        if cell.text.strip():
            return strip

        # 那一格是空的，换成竖线。左边原样保留，右边从「下一格」接上。
        # 注意 `crop` 的右端是**开区间**，所以这里是 `visible_col + 1`。
        accent = self.app.get_css_variables().get("accent", "#ffa62b")
        bar = Strip([Segment(CURSOR_BAR, Style(color=accent))], 1)
        before = strip.crop(0, visible_col)
        after = strip.crop(visible_col + 1, strip.cell_length)
        return before + bar + after

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


class MascotBanner(Static):
    """启动横幅。左边的吉祥物每隔几秒轻轻蹦一下。

    **为什么做成控件而不是拼好的字符串。** 换帧就得有东西记住「现在是第几帧」
    并且定时重绘；`Static` 加一个计时器正好，不必再写一个更重的 `Widget` 子类。

    有一条线**不能碰**：横幅的高度任何时刻都得一样。它是对话区的第一个子节点，
    高一行矮一行，下面所有内容都会跟着回流——那正是 `app._follow_tail` 刚修掉的
    「跳版」。所以蹦跳整个做在 `render_banner` 内部（图案在固定高度的框里往上挪
    一格再落回来，框本身不动，返回的行数因而恒定），这一层只负责把新的一帧交出去。

    滚出视野就不刷了：横幅聊两轮就滚上去，看不见的东西没有重绘的理由。
    """

    def __init__(self, version: str, cwd: str) -> None:
        super().__init__(render_banner(version, cwd), expand=True)
        self._version = version
        self._cwd = cwd
        #: 当前帧号，也是 `bounce_offset` 的输入。
        self._frame = 0
        #: 此刻画着的抬起高度。**用来跳过无变化的重绘**——一个周期 40 帧里只有
        #: 4 帧图案不一样，剩下 36 帧再 `update()` 一次纯属白刷（`Static.update`
        #: 会带 `layout=True` 标脏布局）。
        self._offset = 0

    def on_mount(self) -> None:
        self.set_interval(FRAME_INTERVAL, self._tick)

    def _tick(self) -> None:
        if not self._in_view():
            return
        self._frame += 1
        offset = bounce_offset(self._frame)
        if offset == self._offset:
            return
        self._offset = offset
        self.update(render_banner(self._version, self._cwd, offset))

    def _in_view(self) -> bool:
        """横幅还有没有一部分露在容器视口里。

        `region` 和 `container_viewport` 都是**屏幕坐标**（Textual 8.2.8 实测：
        容器往下挪 4 行，横幅的 `region.y` 就从 0 变成 4；容器再滚动 30 行，
        它从 4 变成 -26）。同一条坐标系里直接比就行，不用自己去减 `scroll_y`。

        还没上屏时两个都是空区域（`bottom == 0`），这里判成「看不见」而跳过——
        正是想要的结果。
        """
        region = self.region
        view = self.container_viewport
        return region.bottom > view.y and region.y < view.bottom


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


def streaming_body(reply: str, elapsed: float, running: str = "") -> Text:
    """流式中的正文（**不含**行首圆点——那个在左列）。

    三种形态（F8、F12、N2）：

    - 正在跑工具（`running` 非空）→ `⠋ name(args) Running…`
    - 还没拿到第一个增量          → `⠋ Imagining… (Ns)`
    - 已有增量                    → 正文，计时降为一行次要信息挂在下面

    三种形态放在同一个函数里，是因为它们本来就是同一个问题的三个答案——「此刻这个
    块里该画什么」。三者互斥，共用一个转轮，调用方只管把已知的东西交给它。
    工具的 `reply` 那一格传空串即可：`running` 非空时它就是被忽略的那个。

    动画帧由**已用秒数**推出来，而不是靠一个每次都 +1 的计数器。
    这样刷新频率变了（比如以后从 0.1s 调到 0.05s），转速也不会跟着乱。
    """
    frame = SPINNER_FRAMES[int(elapsed * 10) % len(SPINNER_FRAMES)]

    if running:
        return Text.assemble((f"{frame} ", DIM), (f"{running} Running…", ""))

    if not reply:
        return Text(f"{frame} Imagining… ({elapsed:.0f}s)", style=DIM)

    return Text.assemble((reply, ""), "\n", (f"{frame} {elapsed:.0f}s", DIM))


def tool_call_line(name: str, args: str) -> Text:
    """工具的调用行：`read_file({"path": "a.py"})`，加粗（F8）。

    `args` 是**预览**（`qicode.agent.MAX_ARGS_PREVIEW` 截断过），不是执行时用的那份
    完整参数。两者在这里合成一行只是为了让人看清「模型在调什么」；真正的参数在
    `Conversation` 里，界面不经手。

    返回 `Text` 而不是字符串：参数是模型给的，里面完全可能有 `[`（写正则、写列表
    都可能），当 markup 解析会吃掉内容甚至抛 `MarkupError`。这个文件开头的约定。
    """
    return Text(f"{name}({args})", style=MARKER_STYLE)


def tool_result_block(content: str, is_error: bool) -> Text:
    """工具结果摘要：调用行下面缩进的若干行（AC11）。

    摘要 `dim`、错误 `red`——「这次调用成没成」要能一眼看出来（F9），
    而不用去读内容本身。

    缩进四格（`  ⎿ `）是有意的：正文块之间是平级的，只有这一块跟它的调用行是
    从属关系，缩进是唯一能表达这件事的排版手段。
    """
    lines = content.splitlines()
    if not content.strip():
        # 空输出（或只有空白）也得画一行，否则这个块看起来像是没跑完。
        lines = ["(无输出)"]

    # `max(0, ...)` 不是多余的：内容不长时 `len - MAX` 是负数，
    # 那句「还有 -3 行」就是这么来的。
    dropped = max(0, len(lines) - MAX_SUMMARY_LINES)
    shown = lines[:MAX_SUMMARY_LINES]

    body = "\n".join(
        f"  {RESULT_ELBOW} {line}" if index == 0 else f"    {line}"
        for index, line in enumerate(shown)
    )
    if dropped:
        # 截断要**说出来**，跟工具那边给模型的 `[truncated]` 是同一条原则：
        # 看着像是在读一段完整的输出、其实后面还有，是最容易误导人的一种残缺。
        body += f"\n    … 还有 {dropped} 行（完整内容已回灌给模型）"

    return Text(body, style=ERROR_STYLE if is_error else DIM)


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


class ToolBlock(Horizontal):
    """对话区里的一条工具调用：**左边一个圆点，右边「调用行 + 结果行」**。

    版式跟 `ReplyBlock` 完全同构——两列的理由（表格里的 Markdown 不折行、把 `● ` 拼进
    markdown 源码会毁掉纯中文长段）在那个类的文档里，这里照抄结论，不重走一遍。
    只有两点不同：

    - 圆点是 `bold cyan` 而不是 `bold`，跟助手正文分开（F9）。
    - 正文是**两行**：先画调用行；执行完在下面挂一行缩进的结果摘要（AC11）。
      执行期间调用行前面叠一个转轮，那就是 N2 要的「进行中指示」。

    这一块**画完就不再改**：`show_result` 之后下一条正文增量会另开一个新的
    `ReplyBlock`。所以执行中的样例（`_call`）在定型时就清掉了，它不会被后来的
    转轮帧覆写回去。
    """

    DEFAULT_CSS = """
    ToolBlock {
        height: auto;
        /* 圆点要对齐调用行，不是垂直居中。 */
        align-vertical: top;
    }

    ToolBlock > .tool-marker {
        width: auto;
        height: auto;
        /* 右边留一格，就是 `●` 和调用行之间的那个空格。 */
        padding: 0 1 0 0;
    }

    ToolBlock > .tool-body {
        width: 1fr;
        height: auto;
    }
    """

    def __init__(self) -> None:
        marker = Static(Text(MARKER, style=TOOL_STYLE), classes="tool-marker")
        body = Static("", classes="tool-body", expand=True)
        super().__init__(marker, body)
        self._marker = marker
        self._body = body
        #: 正在执行的那次调用的 `(name, args)`；执行完置回 None。
        self._call: tuple[str, str] | None = None

    def show_running(self, name: str, args: str) -> None:
        """执行中：`● name(args)`（加粗）。

        这是块挂上去时画的**第一帧**，不带转轮——那时计时器还没走过一格。
        转轮从下一个 tick（`TICK_INTERVAL` 之后）开始由 `show_progress` 接管。
        """
        self._call = (name, args)
        self._body.update(tool_call_line(name, args))

    def show_progress(self, elapsed: float) -> None:
        """执行中的后续帧：调用行前面叠一个转轮（F12、N2）。

        只对**正在跑**的块有效。定型之后 `_call` 是 None，这里直接跳过——
        不然一个早就跑完的块会被后来的帧重新画成「还在跑」。
        """
        if self._call is None:
            return
        name, args = self._call
        self._body.update(streaming_body("", elapsed, running=f"{name}({args})"))

    def show_result(self, name: str, args: str, summary: str, is_error: bool) -> None:
        """执行完：调用行下面挂结果摘要，这一块就此定型（AC11）。

        END 帧也把 `name` / `args` 带过来，于是这里整行重画而不是沿用 `_call` 存的那份：
        「画什么」完全由传进来的东西决定，重画一次的结果跟第一次一模一样，
        这一块内部不必为用户看不见的隐式状态负责。

        失败时圆点跟摘要一起转红（同 `ReplyBlock.show_error`）——整块是一个视觉单元，
        留个青圆点配红字反而别扭。
        """
        self._call = None
        self._marker.update(Text(MARKER, style=ERROR_STYLE if is_error else TOOL_STYLE))
        self._body.update(
            Group(tool_call_line(name, args), tool_result_block(summary, is_error))
        )


#: markdown 里能开启一个「块」的行首写法。`marked_markdown` 遇到这些时不能往前拼前缀。
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


def tool_call_replay(call: ToolCall) -> Text:
    """回放时的一行工具调用：`● read_file({"path": "a.py"})`。

    配色和参数截断规则都跟对话区里 `ToolBlock` 的调用行**共用**（`tool_call_line`
    和 `agent.preview_args`），回放出来才跟刚才在界面上看到的是一个东西。

    参数必须在这里再截一次：`ToolCall.input` 存的是**完整**原文（历史要给模型用），
    界面上那份预览是 agent 另算的。不截的话，一次 `write_file` 的调用行会把整个
    文件内容印进终端——而这一步恰恰是用户按完 `/exit` 之后最没防备的时候。

    跟 `marked_markdown` 同一个取舍：纯 Rich 环境摆不出两列，圆点只能拼进同一段文本。
    """
    return Text.assemble(
        (f"{MARKER} ", TOOL_STYLE),
        tool_call_line(call.name, preview_args(call.input)),
    )


def transcript(messages: list[Message]) -> Group:
    """把一次会话的历史拼成可直接打印到终端的块。

    这是给**退出之后**用的（`docs/v1/checklist.md` 的 scrollback 那条要求
    「退出后内容保留在终端历史中」）。

    为什么要专门做这件事：Textual 跑在**备用屏幕**上（Linux 驱动启动时写
    `\\x1b[?1049h`，退出时写 `\\x1b[?1049l`）。备用屏幕没有回滚缓冲，
    退出的一瞬间整屏内容**连同它的滚动历史一起消失**——用户按完 /exit，
    刚才聊的东西一点不剩。对话区自己能滚，但那只在进程活着的时候算数。

    所以退出时把历史重新打一遍到主屏幕。这不是把界面内容「复制」出来
    （那样会带上边框、状态栏这些只在交互时有意义的东西），而是按对话
    本身重放一遍：用户说的、模型答的、模型调过什么工具。

    **工具轮怎么重放。** 历史里一次工具调用是**两条**消息：assistant 那条只带
    `tool_calls`（没有工具名，工具名在它自己的 `ToolCall` 里），紧接着 `ROLE_TOOL`
    那条只带结果（只有 `tool_call_id`）。所以这里的做法是先记下 assistant 那批调用、
    等结果那条消息来了再按 **id** 配对——协议本来就是靠 id 配对的
    （`docs/v2/plan.md`），按顺序对只能在「两边顺序碰巧一致」时成立。
    这样配出来的版式跟对话区里一样：调用行下面紧跟它自己的结果行。

    耗时不重放：那是「这一轮等了多久」，事后回看没有意义，只会干扰阅读。
    """
    blocks: list[RenderableType] = []
    #: 还没等到结果的调用，key 是 `ToolCall.id`。用 dict 而不是 list：配对靠的就是
    #: id 查找；而 dict 的**插入顺序**正好就是调用的先后，下面按它输出。
    pending: dict[str, ToolCall] = {}

    for msg in messages:
        if msg.role == ROLE_TOOL:
            by_id = {r.tool_call_id: r for r in msg.tool_results}
            # 按**调用**的顺序输出，不按结果的顺序。发起调用的顺序是模型自己的思路，
            # 结果到达的顺序则取决于执行快慢；两者现在恰好一致（agent 是顺序执行的），
            # 但那是实现的巧合，哪天工具并发跑起来，这里就会变成随机排列。
            for call_id in list(pending):
                result = by_id.pop(call_id, None)
                if result is None:
                    continue
                call = pending.pop(call_id)
                blocks.append(tool_call_replay(call))
                blocks.append(tool_result_block(result.content, result.is_error))
            # 配不上调用的结果也要画：宁可多一行没有出处的 `⎿`，
            # 也不要让模型真的拿到的输出在回放里凭空消失。
            for result in by_id.values():
                blocks.append(tool_result_block(result.content, result.is_error))
            continue

        if msg.role == ROLE_ASSISTANT:
            # 只调工具、不说话的那条 preamble 是空串，画出来就是一个空行。
            if msg.content:
                # 助手回复按 markdown 重新渲染，代码块和列表才对（F8 的同一个理由）。
                # 版式跟对话区里保持一致，免得回放出来是另一个样子。
                blocks.append(marked_markdown(msg.content))
            for call in msg.tool_calls:
                pending[call.id] = call
            continue

        blocks.append(user_block(msg.content))

    # 没人来配对的调用：取消就发生在工具执行期间（那时 assistant 那条已经入历史了，
    # 结果永远等不到）。也要画出来——一次真的发生过的调用不该在回放里凭空消失。
    for call in pending.values():
        blocks.append(tool_call_replay(call))

    return Group(*blocks)
