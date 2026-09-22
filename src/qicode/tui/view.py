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
from textual.message import Message
from textual.widgets import TextArea

from qicode.llm import Provider

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


class PromptArea(TextArea):
    """带提交语义的输入框。

    Textual 8.x 的 `TextArea` 是**纯编辑器**，不是输入框：它的 `_on_key` 里写死了
    `insert_values = {"enter": "\\n"}`，并且对 enter 调用 `event.stop()` +
    `event.prevent_default()`。后果是——键盘事件当场被它吃掉，App 层挂
    `("enter", "submit")` 这类 binding **根本收不到**。
    （这正是 `docs/v1/task.md` T10 里那条写法在 Textual 8.x 上不成立的原因，
    那个写法的年代 TextArea 还没有吞掉 enter。）

    所以提交语义只能在这一层自己接：

    - `Enter`     → 发 `Submitted` 消息，**不**插换行
    - `Alt+Enter` → 插换行（F9 要求的多行编辑）

    实测（Textual 8.2.8）：`enter` / `alt+enter` / `shift+enter` 是三个互不相同的
    键名，且只有 `enter` 会被基类插成换行。这给了我们一个干净的接管点。
    """

    class Submitted(Message):
        """用户按 Enter 提交了输入。"""

        def __init__(self, value: str) -> None:
            # 消息自带内容，handler 不必再去 widget 里捞——这样提交之后
            # 清空输入框的顺序就跟消息内容无关了。
            self.value = value
            super().__init__()

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "alt+enter":
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


def error_block(err: Exception) -> Text:
    """错误块（F11）。红色，与正常内容一眼可分，且**不退出**。"""
    return Text(f"{MARKER} {err}", style=ERROR_STYLE)


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
