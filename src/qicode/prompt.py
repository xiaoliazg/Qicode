"""内置系统提示词与启动横幅（F7、F9）。

这一层没有任何依赖，也不碰网络和界面：只产出两段文本，谁需要谁来取。
"""

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

SYSTEM_PROMPT = """\
你是 Qicode，一个运行在用户终端里的 AI 助手。

- 回答直接、准确，不要寒暄和客套。
- 涉及代码时用 markdown 代码块，并标注语言。
- 拿不准的事就说拿不准，不要编造。
"""


#: 吉祥物和右侧文字之间留几格。太挤的话文字像是贴在脸上。
BANNER_GAP = 4


def render_banner(version: str, cwd: str) -> str:
    """拼出启动横幅：吉祥物 + 版本号 + 当前工作目录 + 就绪提示。

    左边是吉祥物，右边一列文字，右侧文字整体下移让它跟图案垂直居中。

    **上下各留一行空白**（`_PAD`）。横幅是整屏的第一块，贴着终端顶边会显得
    局促；底下那一行更必要——不留的话第一条消息紧贴着腮红那行，整个 banner
    跟对话黏成一坨。
    """
    right_column = [
        f"[bold]Qicode[/] [dim]v{version}[/]",
        f"[dim]{cwd}[/]",
        "",
        "[dim]输入消息开始对话，Alt+Enter 或 Ctrl+J 换行，/exit 退出[/]",
    ]

    rows = MASCOT_BANNER.splitlines()
    # 右侧文字整体下移，让它大致落在图案的中段而不是顶着耳朵。
    offset = (len(rows) - len(right_column)) // 2

    lines = [""]  # 顶部留白
    for index, row in enumerate(rows):
        right_index = index - offset
        right = ""
        if 0 <= right_index < len(right_column):
            right = right_column[right_index]
        # 图案每行都是固定列数，宽度一致，所以直接拼、不用补空格对齐。
        lines.append(f"{row}{' ' * BANNER_GAP}{right}".rstrip())
    lines.append("")  # 底部留白，别跟第一条消息贴在一起

    return "\n".join(lines)
