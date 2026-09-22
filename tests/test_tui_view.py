"""`tui/view.py` 里那几个纯函数的单测（F8、F9、AC11）。

`view.py` 自己的模块文档写着「渲染规则可以脱离整个 App 单独测」，这个文件就是
兑现那句话的地方：这里不 import textual、不跑 App，只看函数返回了什么。
摆到界面上的效果由 `tests/test_tui_app.py` 负责验——两边分工，不互相替代。
"""

import io
import json

from rich.console import Console
from rich.text import Text

from qicode.agent import MAX_ARGS_PREVIEW
from qicode.llm import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    ROLE_USER,
    Message,
    ToolCall,
    ToolResult,
)
from qicode.tui.view import (
    DIM,
    ERROR_STYLE,
    MARKER,
    MAX_SUMMARY_LINES,
    RESULT_ELBOW,
    SPINNER_FRAMES,
    streaming_body,
    tool_call_line,
    tool_result_block,
    transcript,
)


def render_transcript(messages: list[Message]) -> str:
    """把回放结果渲染成纯文本，供断言。

    `transcript()` 返回的是 Rich 的 `Group`，直接断言它等于什么没有意义（那是一棵
    渲染树）。退出回放的**唯一去处是终端**，所以这里就按终端的口径来验：
    真渲染一遍，再把字符网格取回来。

    宽度给得**很宽**是有意的：折行是终端按自己窗口宽度做的事，不是这一层要验的。
    用 80 的话，一条稍长的调用行会被切成两三行，断言就得去迁就切在哪儿——
    那验的是 Rich 的折行算法，不是回放的内容。
    """
    console = Console(record=True, width=300, file=io.StringIO())
    console.print(transcript(messages))
    return console.export_text()


# ────────────────────────── 调用行 ──────────────────────────


def test_call_line_shows_name_and_args() -> None:
    assert tool_call_line("read_file", '{"path": "a.py"}').plain == (
        'read_file({"path": "a.py"})'
    )


def test_call_line_does_not_parse_markup() -> None:
    """参数里带 `[` 不能被当成 Rich 标记——轻则内容被吃掉，重则抛 `MarkupError`。

    参数是模型给的，写正则、写列表都会带方括号，这不是罕见输入。
    """
    line = tool_call_line("bash", '{"command": "ls [a-z]*"}')

    assert "[a-z]*" in line.plain
    assert line.style == "bold"


# ────────────────────────── 结果摘要 ──────────────────────────


def test_result_block_indents_under_the_call_line() -> None:
    block = tool_result_block("1→hello", is_error=False)

    assert block.plain == f"  {RESULT_ELBOW} 1→hello"
    assert block.style == DIM


def test_result_block_turns_red_on_error() -> None:
    """错误摘要整块转红（F9 的「UI 可区分」）。"""
    assert tool_result_block("未知工具: x", is_error=True).style == ERROR_STYLE


def test_result_block_keeps_multi_line_output_aligned() -> None:
    """多行结果的**续行**要跟第一行的正文对齐，不能顶格。

    顶格的话看起来像是两个平行的块，而不是「这次调用的输出」。
    """
    lines = tool_result_block("甲\n乙\n丙", is_error=False).plain.split("\n")

    assert lines[0] == f"  {RESULT_ELBOW} 甲"
    assert lines[1] == "    乙"
    assert lines[2] == "    丙"


def test_long_result_is_truncated_with_a_visible_count() -> None:
    """超长结果按 `MAX_SUMMARY_LINES` 截断，并且**明说**截了多少行。

    不标出来的话，用户看着就像工具只输出了这么多——而完整内容其实在历史里、
    也回灌给模型了，两边的认知会对不上。
    """
    total = MAX_SUMMARY_LINES + 5
    block = tool_result_block("\n".join(f"第 {i} 行" for i in range(total)), False)
    lines = block.plain.split("\n")

    # 8 行正文 + 1 行截断说明。
    assert len(lines) == MAX_SUMMARY_LINES + 1
    assert lines[-1].strip().endswith("还有 5 行（完整内容已回灌给模型）")
    assert f"第 {MAX_SUMMARY_LINES - 1} 行" in lines[-2]
    # 被截掉的那些**不该**出现。
    assert f"第 {MAX_SUMMARY_LINES} 行" not in block.plain


def test_exactly_max_lines_is_not_truncated() -> None:
    """刚好卡在界限上时不该多说一句「还有 0 行」。"""
    block = tool_result_block("\n".join("x" for _ in range(MAX_SUMMARY_LINES)), False)

    assert len(block.plain.split("\n")) == MAX_SUMMARY_LINES
    assert "还有" not in block.plain


def test_empty_output_still_draws_a_line() -> None:
    """没有输出也得画一行，否则这个块看起来像没跑完（只有调用行、没有结果行）。"""
    assert RESULT_ELBOW in tool_result_block("", is_error=False).plain
    assert RESULT_ELBOW in tool_result_block("   \n ", is_error=False).plain


# ────────────────────────── 正文三态 ──────────────────────────


def test_body_waits_with_a_spinner_before_the_first_token() -> None:
    body = streaming_body("", elapsed=0.0)

    assert isinstance(body, Text)
    assert body.plain == f"{SPINNER_FRAMES[0]} Imagining… (0s)"


def test_body_shows_the_reply_and_puts_the_timer_below() -> None:
    """已有正文时，计时降为**下面**一行次要信息——盖在正文里会挡住内容。"""
    body = streaming_body("你好", elapsed=1.2)

    # 1.2 秒 → `int(1.2 * 10) % 10` = 第 2 帧。
    assert body.plain == f"你好\n{SPINNER_FRAMES[2]} 1s"


def test_body_shows_the_running_tool_instead_of_the_reply() -> None:
    """跑工具时显示 `⠋ name(args) Running…`（N2 的进行中指示）。"""
    body = streaming_body("", elapsed=0.3, running='read_file({"path": "a.py"})')

    assert body.plain == f'{SPINNER_FRAMES[3]} read_file({{"path": "a.py"}}) Running…'


def test_spinner_advances_with_elapsed_time() -> None:
    """转轮由**已用秒数**推出来，不是靠自增计数器——刷新频率变了转速也不会乱。"""
    frames = {streaming_body("", elapsed=step / 10).plain[0] for step in range(10)}

    assert len(frames) == len(SPINNER_FRAMES)


# ────────────────────────── 退出回放（T17）──────────────────────────


def tool_round_messages() -> list[Message]:
    """一条完整工具轮的历史：提问 → 开场白+调用 → 结果 → 最终答复。"""
    return [
        Message(role=ROLE_USER, content="看看 a.py"),
        Message(
            role=ROLE_ASSISTANT,
            content="我读一下",
            tool_calls=[ToolCall(id="c1", name="read_file", input='{"path": "a.py"}')],
        ),
        Message(role=ROLE_TOOL, tool_results=[ToolResult("c1", "1→hello")]),
        Message(role=ROLE_ASSISTANT, content="a.py 里写着 hello"),
    ]


def test_transcript_replays_a_tool_round_in_order() -> None:
    """回放里的工具轮：提问 → 开场白 → 调用行 → 结果 → 最终答复。"""
    text = render_transcript(tool_round_messages())

    assert "● 看看 a.py" in text
    assert "我读一下" in text
    assert '● read_file({"path": "a.py"})' in text
    assert f"{RESULT_ELBOW} 1→hello" in text
    assert "a.py 里写着 hello" in text
    # 调用行在前、它自己的结果紧跟其后、最终答复在最后。
    assert (
        text.index("● read_file")
        < text.index(f"{RESULT_ELBOW} 1→hello")
        < text.index("a.py 里写着 hello")
    )


def test_transcript_draws_no_bare_marker_for_tool_messages() -> None:
    """工具结果回合**不再**被当成一条空正文，画成一个光秃秃的 `●`。

    这是 T17 修的毛病：`transcript` 原来只认 user / assistant，其余一律走
    `marked_markdown(msg.content)`；而 `ROLE_TOOL` 的 content 是空串，于是回放里
    多出一行只有一个圆点的东西（真机上就是这么看到的）。
    """
    text = render_transcript(tool_round_messages())

    assert not [line for line in text.splitlines() if line.strip() == MARKER]


def test_transcript_pairs_results_with_their_own_call_by_id() -> None:
    """一次调了两个工具时，结果要**按 id** 配到各自的调用行上。

    故意把结果**倒序**写：按顺序对也能过的话，这条用例就白写了——而协议保证的
    只有 id 配对（`docs/v2/plan.md`），顺序一致只是碰巧。
    """
    messages = [
        Message(
            role=ROLE_ASSISTANT,
            tool_calls=[
                ToolCall(id="a", name="read_file", input='{"path": "a.py"}'),
                ToolCall(id="b", name="bash", input='{"command": "ls"}'),
            ],
        ),
        Message(
            role=ROLE_TOOL,
            tool_results=[ToolResult("b", "b-result"), ToolResult("a", "a-result")],
        ),
    ]

    lines = [line for line in render_transcript(messages).splitlines() if line.strip()]

    assert lines == [
        '● read_file({"path": "a.py"})',
        f"  {RESULT_ELBOW} a-result",
        '● bash({"command": "ls"})',
        f"  {RESULT_ELBOW} b-result",
    ]


def test_transcript_truncates_long_tool_arguments() -> None:
    """回放里的参数也要截断。

    历史里存的是**完整**原文（那是给模型用的），界面上那份预览是 agent 另算的。
    回放不自己拦一道的话，一次 `write_file` 的调用行会把整个文件内容印进终端——
    偏偏这是用户按完 `/exit`、最没有防备的时候。
    """
    long_input = json.dumps({"path": "x" * 200})
    messages = [
        Message(
            role=ROLE_ASSISTANT,
            tool_calls=[ToolCall(id="c1", name="read_file", input=long_input)],
        )
    ]

    text = render_transcript(messages)

    assert long_input not in text
    assert long_input[:MAX_ARGS_PREVIEW] in text
    assert "…" in text


def test_transcript_decodes_unicode_escapes_in_tool_args() -> None:
    """回放里的工具行同样要还原转义——它跟对话区共用 `preview_args`。

    回放是用户按完 `/exit` 之后看的最后一眼（也是唯一一眼），那儿印一串 `\\u742a`
    比对话区里更难受：旁边没有工具结果行可以拿来对照。
    """
    messages = [
        Message(
            role=ROLE_ASSISTANT,
            tool_calls=[
                ToolCall(
                    id="c1",
                    name="write_file",
                    input=json.dumps({"path": "琪琪作业/快排.txt"}),
                )
            ],
        )
    ]

    text = render_transcript(messages)

    assert "琪琪作业/快排.txt" in text
    assert "\\u742a" not in text


def test_transcript_still_shows_a_call_that_never_got_a_result() -> None:
    """取消发生在工具执行期间时，那次调用只留下了 assistant 那半条。

    照样要画：用户看到的是「这个调用发生过」，而不是一个干净的、好像什么都没
    发生的回放。配不上的结果同样要画（下面是另一条用例）。
    """
    messages = [
        Message(role=ROLE_USER, content="看看 a.py"),
        Message(
            role=ROLE_ASSISTANT,
            content="我读一下",
            tool_calls=[ToolCall(id="c1", name="read_file", input='{"path": "a.py"}')],
        ),
    ]

    text = render_transcript(messages)

    assert '● read_file({"path": "a.py"})' in text
    assert RESULT_ELBOW not in text


def test_transcript_still_shows_a_result_whose_call_is_missing() -> None:
    """反过来：配不上调用的结果也要画。

    这条是防御性的（agent 不会写出这种历史），但宁可多一行没有出处的 `⎿`，
    也不要让模型真的拿到的输出在回放里凭空消失。
    """
    messages = [Message(role=ROLE_TOOL, tool_results=[ToolResult("gone", "孤儿结果")])]

    text = render_transcript(messages)

    assert f"{RESULT_ELBOW} 孤儿结果" in text
