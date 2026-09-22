"""内置系统提示词与启动横幅（F7、F9）。

这一层没有任何依赖，也不碰网络和界面：只产出两段文本，谁需要谁来取。
"""

from rich.markup import escape

MASCOT_ART = """\
.HH......HH.
.HHHHHHHHHH.
HHHHHHHHHHHH
.HFFFFFFFFH.
.HFFEFFEFFH.
..FBFFFFBF..
"""

# 图案里每个字母对应一种颜色。'.' 不在表里，渲染成透明空格——
# 背景留白，轮廓才在深浅两种终端主题下都看得出来。
MASCOT_COLORS = {
    "H": "#ff8c42",  # 橙色头发（最上面两行是猫耳）
    "F": "#ffd9b3",  # 肤色
    "E": "#3b2b20",  # 眼睛
    "B": "#ff9aa2",  # 腮红
}


def _render_pixel_art(art: str, colors: dict[str, str]) -> str:
    """把字符图案翻成「带背景色的空格」拼出的 Rich markup。

    为什么不用实心块字符 `█` 直接拼：`█` 的显示宽度在不同终端、不同字体下会在
    1 格和 2 格之间摇摆，一旦判成 2 格，整幅图就斜掉了。**空格的宽度恒为 1 列**，
    任何终端都一样——用背景色把它染上色，就得到了一个宽度绝对可靠的像素。
    """
    lines = []
    for row in art.splitlines():
        cells = []
        for char in row:
            color = colors.get(char)
            # 认不出的字符（含 '.'）一律留白：图案里可以用任意符号做占位。
            cells.append(f"[on {color}] [/]" if color else " ")
        lines.append("".join(cells))
    return "\n".join(lines)


# 吉祥物图案，已是可直接嵌入 Rich 输出的 markup。
MASCOT_BANNER: str = _render_pixel_art(MASCOT_ART, MASCOT_COLORS)


def system_prompt(provider_name: str, model: str) -> str:
    """按当前接入点拼出 system prompt。

    **为什么要把 provider / model 写进去。** 这两个值只有我们这边知道——它们是
    本地配置里的字段，模型自己看不到。不告诉它，用户问「你是什么模型」它就只能
    答「我不掌握这个信息」（这是实测的原话），再不然就凭训练数据编一个。
    与其让它猜，不如把**已知的事实**交给它。

    措辞上有意分开两件事：

    - 「你是谁、跑在什么模型上」——我们知道，要求它照实说；
    - 「这个模型是哪家公司训练的」——我们不知道，就明确说不知道。

    否则一句笼统的「不要编造」会让它对**两者**都用同一句「我不知道」搪塞过去，
    而那正是要修的毛病。
    """
    return f"""\
你是 Qicode，一个运行在用户终端里的 AI 助手。

本次会话由接入点「{provider_name}」上的模型 {model} 生成回复。
用户问起你是谁、用的是什么模型时，就照这两条如实回答——不要推说不知道。
至于这个模型是哪家公司训练的，本地配置里没有这个信息，那一条就说不知道。

- 回答直接、准确，不要寒暄和客套。
- 涉及代码时用 markdown 代码块，并标注语言。
- 拿不准的事就说拿不准，不要编造。
"""


#: 吉祥物和右侧文字之间留几格。太挤的话文字像是贴在脸上。
BANNER_GAP = 4

#: 图案上方留出的**起跳空间**（行）。蹦起来时图案要往上占一行，没有这块
#: 空间就会被裁掉，所以横幅的高度按 `图案行数 + BOUNCE_HEADROOM` 算。
BOUNCE_HEADROOM = 1

#: 蹦一下的逐帧高度：每一帧图案抬起几行（0 = 坐在框底）。前两帧抬着，
#: 后两帧落回——合起来约 0.4 秒，就是「轻轻一下」。
BOUNCE_FRAMES = (1, 1, 0, 0)

#: 一个完整周期多少帧（含蹦完之后静置的部分）。40 帧 × 0.1 秒 = 每 4 秒蹦一下。
BOUNCE_PERIOD_FRAMES = 40

#: 动画帧间隔（秒）。
FRAME_INTERVAL = 0.1


def bounce_offset(frame: int) -> int:
    """第 `frame` 帧时，图案该抬起几行。

    绝大多数帧返回 0——蹦是**偶发**的，一个周期里只有头几帧抬起来。这样比
    一直来回晃更像「活着」，也不会一直在那儿抢注意力。
    """
    phase = frame % BOUNCE_PERIOD_FRAMES
    if phase < len(BOUNCE_FRAMES):
        return BOUNCE_FRAMES[phase]
    return 0


def render_banner(version: str, cwd: str, art_offset: int = 0) -> str:
    """拼出启动横幅：吉祥物 + 版本号 + 当前工作目录 + 就绪提示。

    左边是吉祥物，右边一列文字，右侧文字跟图案垂直居中。

    `art_offset` 是**图案抬起几行**（0 = 静止）。它是这个函数唯一的动态输入，
    由 `bounce_offset` 按帧给出。

    有一条约束必须守住：**任何 `art_offset` 下，这个函数返回的行数都一样**。
    横幅在对话区里，高度一变整块内容就会回流、下面的东西跟着跳——那正是
    `app._follow_tail` 刚修掉的那个毛病。所以图案外面套了一个固定高度的**框**
    （`图案行数 + BOUNCE_HEADROOM`），图案在框里挪，框本身不动。

    同样为了这个，右侧文字的落点按**框**高居中、跟 `art_offset` 无关：
    图案蹦的时候，文字必须纹丝不动。

    底部留一行空白（见文件末尾），别跟第一条消息贴在一起。
    """
    right_column = [
        f"[bold]Qicode[/] [dim]v{version}[/]",
        # 目录名是**用户那边的字符串**，里面完全可能有 `[`。这个返回值会当成
        # Rich 标记解析（`Static(markup=True)`），不转义的话三种坏法都实测过：
        # `/tmp/a[b]c` 渲染成 `/tmp/ac`（不认得的标签直接丢，路径被悄悄吃掉一截）；
        # `/tmp/[bold]x` 让后面的字全变粗体（样式被目录名劫持）；`/tmp/x[/]` 直接
        # 抛 `MarkupError`，**启动当场崩掉**。转义只影响那一个字符，正常路径看不出来。
        f"[dim]{escape(cwd)}[/]",
        "",
        "[dim]输入消息开始对话，Alt+Enter 或 Ctrl+J 换行，/exit 退出[/]",
    ]

    art_rows = MASCOT_BANNER.splitlines()
    # 图案的**显示宽度**要从原始字符画上量。`MASCOT_BANNER` 是 markup，一个格子是
    # 一串 `[on #xxxxxx] [/]`，它的字符串长度跟屏幕上的列数根本不是一回事——
    # 拿它去补空格，占位行会把右侧文字推出屏幕。
    art_width = len(MASCOT_ART.splitlines()[0])
    box_height = len(art_rows) + BOUNCE_HEADROOM

    # 文字按**框**高居中，不按图案高——图案蹦的时候文字不能跟着动。
    text_offset = (box_height - len(right_column)) // 2
    # 图案坐在框底，`art_offset` 是往上抬几行。
    art_top = box_height - len(art_rows) - art_offset

    lines = []
    for index in range(box_height):
        art_index = index - art_top
        # 图案够不着的那几行要用**等宽的空格**占位，否则右侧文字会左移。
        art = art_rows[art_index] if 0 <= art_index < len(art_rows) else " " * art_width

        right_index = index - text_offset
        right = (
            right_column[right_index] if 0 <= right_index < len(right_column) else ""
        )

        lines.append(f"{art}{' ' * BANNER_GAP}{right}".rstrip())
    lines.append("")  # 底部留白，别跟第一条消息贴在一起

    return "\n".join(lines)
