"""启动横幅与蹦跳节奏的纯函数用例（F7）。

`render_banner` / `bounce_offset` 是**无依赖的纯函数**（`qicode/prompt.py` 那一层
不碰网络、不碰界面），所以这里不需要起 App，直接喂参数看返回的字符串就行。

放在这个层级测是有意的：蹦跳最容易出的岔子——「某一帧比别的帧高一行」——在这一层
一眼就能验出来，而且验得**穷尽**（把每个 offset 都试一遍）。等到 App 那一层再验，
就得跟布局、计时器纠缠，还只能看到当时那一帧。
"""

import io

from rich.console import Console
from rich.text import Text

from qicode.prompt import (
    BANNER_GAP,
    BOUNCE_FRAMES,
    BOUNCE_HEADROOM,
    BOUNCE_PERIOD_FRAMES,
    MASCOT_ART,
    MASCOT_BANNER,
    bounce_offset,
    render_banner,
)

#: 图案占的列数。
#:
#: **必须从原始字符画 `MASCOT_ART` 上量，不能拿 `MASCOT_BANNER` 去 `len()`**——
#: 后者是 markup，一个格子是一串 `[on #xxxxxx] [/]`（16 个字符），字符串长度跟
#: 屏幕上的列数差着十几倍。这个坑两边都踩过：`render_banner` 里补占位空格时踩了
#: 一次（右侧文字被推出屏幕），这个文件里取列号时又踩了一次。
ART_WIDTH = len(MASCOT_ART.splitlines()[0])

#: 图案右侧那一列文字的起始列。
RIGHT_COLUMN_AT = ART_WIDTH + BANNER_GAP

#: 把每个可能的抬起高度都试一遍（含超出上限的，看它会不会被老老实实夹住）。
ALL_OFFSETS = range(BOUNCE_HEADROOM + 3)


def plain(line: str) -> str:
    """一行的**屏幕字符**。markup 解析掉之后，一格就是一个字符。

    这里所有的字符（图案的色块、间隔、右侧中英文）宽度都是 1 列，所以直接拿
    `plain` 当列坐标用是准的。
    """
    return Text.from_markup(line).plain


def right_column(banner: str) -> list[str]:
    """从横幅里切出右侧文字那一列，去掉图案和它俩之间的间隔。"""
    return [plain(line)[RIGHT_COLUMN_AT:] for line in banner.splitlines()]


def art_shape(line: str) -> list[str]:
    """一行里属于图案的那部分，按「第几格到第几格染了什么色」抽出来。

    图案整个是**带背景色的空格**，纯文本里全是空白、根本看不出形状（当初为了截
    个图验证它，只能去抓带 ANSI 的 `tmux capture-pane -e`）。样式区间才是它的
    形状——`span` 的起止列直接说明了哪几格是什么颜色。
    """
    return [
        f"{start}-{end}:{style}"
        for start, end, style in Text.from_markup(line).spans
        if end <= ART_WIDTH
    ]


# ────────────────────────── 高度必须恒定 ──────────────────────────


def test_banner_height_never_changes_while_bouncing() -> None:
    """任何抬起高度下，横幅返回的行数都一样。

    **这条是蹦跳能成立的前提。** 横幅是对话区的第一个子节点，它高一行矮一行，
    下面所有内容都会跟着回流——那正是 `app._follow_tail` 刚修掉的「跳版」。
    图案往上挪一格时如果整个横幅也跟着矮一行，每 4 秒就会看见整个对话区抖一下。
    """
    heights = [
        len(render_banner("0.1.0", "/tmp/x", off).splitlines()) for off in ALL_OFFSETS
    ]

    assert len(set(heights)) == 1, f"不同抬起高度下横幅行数不一致：{heights}"


def test_banner_right_column_never_moves_while_bouncing() -> None:
    """图案蹦的时候，右边那列文字**一行都不许动**。

    只验总行数是不够的：图案整体上移、文字跟着上移，总行数照样不变，但用户看到
    的是版本号在跳。所以这里逐行比对文字的落点。
    """
    resting = right_column(render_banner("0.1.0", "/tmp/x", 0))

    for off in ALL_OFFSETS:
        assert right_column(render_banner("0.1.0", "/tmp/x", off)) == resting, (
            f"抬起 {off} 行时右侧文字挪位了"
        )


def test_the_art_actually_moves() -> None:
    """台子得真的动——否则上面两条「纹丝不动」的用例，用一个死掉的横幅也能过。"""
    assert render_banner("0.1.0", "/tmp/x", 1) != render_banner("0.1.0", "/tmp/x", 0)


def test_the_whole_mascot_is_still_drawn_at_the_top_of_the_bounce() -> None:
    """蹦到最高点时，图案的每一行都还在、顺序没乱、也没被框裁掉。

    图案抬到顶时正好落在框的最上面 `len(图案)` 行里，所以直接比这几行就行。
    框要是矮了一行（比如谁把 `BOUNCE_FRAMES` 改成 `(2, 2, 0, 0)` 而没同步加高
    框），最后一行图案会整个消失，这里就红。
    """
    box = render_banner("0.1.0", "/tmp/x", BOUNCE_HEADROOM).splitlines()
    art = MASCOT_BANNER.splitlines()

    assert [art_shape(line) for line in box[: len(art)]] == [
        art_shape(row) for row in art
    ]


def test_every_scrap_of_the_mascot_is_inside_the_banner() -> None:
    """图案的色块必须全落在图案占的那几列里，一格都不许伸到文字那边去。

    这条钉住的是「占位行的宽度」。`render_banner` 里有一处很容易写错：补占位空格
    时的宽度得从**原始字符画** `MASCOT_ART` 上量。按 `MASCOT_BANNER` 的字符串长度
    去算的话，一串 markup `[on #xxxxxx] [/]` 有 16 个字符却只占 1 列，占位行会被
    补宽十几倍——那一行的右侧文字就被推到屏幕外面去了（`ALL_OFFSETS` 特意取到
    上限之外，正是为了让占位行和右侧文字凑到同一行上，把这种错露出来）。
    """
    banner = render_banner("0.1.0", "/tmp/x", BOUNCE_HEADROOM)

    for line in banner.splitlines():
        for _, end, style in Text.from_markup(line).spans:
            if "on " not in str(style):
                continue  # 不是图案的色块（间隔和右侧文字都是无色样式）
            assert end <= ART_WIDTH, f"图案伸出了 {ART_WIDTH} 列：{line!r}"


# ────────────────────────── 蹦跳节奏 ──────────────────────────


def test_bounce_offset_returns_to_rest_and_stays_there() -> None:
    """一个周期里绝大多数帧是静止的。

    蹦是**偶发**的：`bounce_offset` 一直在抖的话，吉祥物会像个一直在挣扎的
    状态指示器，比不动更烦人。
    """
    period = [bounce_offset(i) for i in range(BOUNCE_PERIOD_FRAMES)]

    raised = sum(1 for off in BOUNCE_FRAMES if off)
    assert raised == sum(1 for off in period if off), "抬起帧数对不上"
    assert period[-1] == 0, "周期末尾没落回静止"
    # 静止的帧要占绝大多数。
    assert period.count(0) > len(period) // 2


def test_bounce_offset_is_periodic() -> None:
    """帧号一直涨，但高度按周期循环——定时器不用自己去取模。"""
    for i in range(BOUNCE_PERIOD_FRAMES * 2):
        assert bounce_offset(i) == bounce_offset(i + BOUNCE_PERIOD_FRAMES)


def test_bounce_never_lifts_higher_than_the_headroom() -> None:
    """抬起高度不能超过图案上方留出的空间。

    超了的话图案头顶那一行会被框裁掉——看起来像是头发被削了一块。
    """
    for i in range(BOUNCE_PERIOD_FRAMES * 2):
        assert 0 <= bounce_offset(i) <= BOUNCE_HEADROOM


# ────────────────────────── 目录名里的方括号 ──────────────────────────


def test_cwd_with_markup_characters_renders_literally() -> None:
    """目录名里的 `[` 必须原样显示，不能当成 Rich 标记。

    这个返回值是交给 `markup=True` 的 `Static` 去解析的，而 cwd 是用户机器上的
    字符串、我们说了不算。实测三种坏法（都试过）：

    - `/tmp/a[b]c` → 渲染成 `/tmp/ac`，**路径被悄悄吃掉一截**（不认得的标签直接丢）
    - `/tmp/[bold]x` → 后面的字全变粗体，样式被目录名劫持
    - `/tmp/x[/]` → `MarkupError`，**启动当场崩掉**
    """
    console_out = io.StringIO()
    console = Console(file=console_out, width=200)
    console.print(render_banner("0.1.0", "/tmp/a[b]c"))  # 不转义的话这里会抛

    assert "/tmp/a[b]c" in console_out.getvalue()
    # 顺带把那两种更狠的一起钉上。
    for cwd in ("/tmp/[bold]x", "/tmp/x[/]"):
        Console(file=io.StringIO(), width=200).print(render_banner("0.1.0", cwd))
