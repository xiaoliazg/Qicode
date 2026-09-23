"""`qicode.tool` 的单元测试（AC1–AC6、N1、N4、N5）。

写法沿用 v1：同步 `def test_...` 里 `asyncio.run(...)`，**不引入 pytest-asyncio**
（`docs/v2/plan.md` 技术决策「异步测试怎么写」）。
"""

import asyncio
import json
import signal
import time
from typing import Any

import pytest

from qicode.tool import (
    DEFAULT_TIMEOUT,
    Registry,
    Result,
    Tool,
    _parse_args,
    _truncate,
    new_default_registry,
)
from qicode.tool.bash import BashTool
from qicode.tool.edit_file import EditFileTool
from qicode.tool.glob_tool import GlobTool
from qicode.tool.grep_tool import MAX_LINE_CHARS, GrepTool
from qicode.tool.read_file import ReadFileTool
from qicode.tool.write_file import WriteFileTool


def run(coro: Any) -> Any:
    """跑一个协程并等它出结果。

    每次调用 `asyncio.run` 都会新建一个 event loop，测试之间因此互不干扰——
    正是我们要的（v1 的测试也是这么做的）。
    """
    return asyncio.run(coro)


def args(**kwargs: Any) -> str:
    """把关键字参数拼成工具入参那样的 JSON 字符串。"""
    return json.dumps(kwargs)


# ────────────────────────── 测试替身 ──────────────────────────


class _EchoTool:
    """最小的假工具：把收到的入参原样回显。"""

    def name(self) -> str:
        return "echo"

    def description(self) -> str:
        return "把参数原样回显"

    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, args: str) -> Result:
        return Result(f"收到: {args}")


class _SlowTool:
    """永远不主动结束的假工具，用来触发超时。"""

    def name(self) -> str:
        return "slow"

    def description(self) -> str:
        return "很慢"

    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, args: str) -> Result:
        await asyncio.sleep(30)
        return Result("本不该跑到这里")


class _BoomTool:
    """执行时直接抛异常的假工具，用来验证 Registry 的兜底。"""

    def name(self) -> str:
        return "boom"

    def description(self) -> str:
        return "会炸"

    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, args: str) -> Result:
        raise RuntimeError("炸了")


# ────────────────────────── F1：注册中心 ──────────────────────────


def test_default_registry_exports_six_tools_in_registration_order() -> None:
    """六个工具，顺序稳定（AC1）。顺序即模型看到的排列，必须可预期。"""
    registry = new_default_registry()
    definitions = registry.definitions()

    assert [d.name for d in definitions] == [
        "read_file",
        "write_file",
        "edit_file",
        "bash",
        "glob",
        "grep",
    ]


def test_default_registry_tools_satisfy_protocol_and_carry_schema() -> None:
    """每个工具都符合 `Tool` Protocol，且描述了用法、给出了正经的 JSON Schema。"""
    registry = new_default_registry()

    for definition in registry.definitions():
        assert isinstance(registry.get(definition.name), Tool)
        # 描述是模型决定调不调这个工具的唯一依据，空描述等于没写。
        assert definition.description.strip()
        assert definition.input_schema["type"] == "object"
        assert definition.input_schema["required"]
        assert definition.input_schema["properties"]


def test_registry_get_hits_and_misses() -> None:
    registry = new_default_registry()

    assert registry.get("read_file") is not None
    assert registry.get("no_such_tool") is None


def test_registry_rejects_duplicate_name() -> None:
    """重名当场抛，不静默覆盖——六个工具一把注册，重名只可能是写错了名字。"""
    registry = Registry()
    registry.register(_EchoTool())

    with pytest.raises(ValueError, match="echo"):
        registry.register(_EchoTool())


def test_registry_execute_unknown_tool_returns_error_result() -> None:
    """未知工具是**结果**不是异常：模型偶尔会自己编一个工具名出来。"""
    result = run(Registry().execute("no_such_tool", "{}", DEFAULT_TIMEOUT))

    assert result.is_error
    assert "未知工具" in result.content


def test_registry_execute_passes_result_through() -> None:
    registry = Registry()
    registry.register(_EchoTool())

    result = run(registry.execute("echo", '{"a": 1}', DEFAULT_TIMEOUT))

    assert not result.is_error
    assert result.content == '收到: {"a": 1}'


def test_registry_execute_turns_timeout_into_error_result() -> None:
    """超时收成结果（N1），并且真的不再等下去。"""
    registry = Registry()
    registry.register(_SlowTool())

    result = run(registry.execute("slow", "{}", timeout=0.01))

    assert result.is_error
    assert "超时" in result.content


def test_registry_execute_catches_unexpected_exception() -> None:
    """工具本该自己返回 `Result`；万一没做到，也不能把整轮对话炸掉（N4）。"""
    registry = Registry()
    registry.register(_BoomTool())

    result = run(registry.execute("boom", "{}", DEFAULT_TIMEOUT))

    assert result.is_error
    assert "炸了" in result.content


# ────────────────────────── 入参解析与截断 ──────────────────────────


def test_parse_args_treats_empty_string_as_empty_object() -> None:
    """OpenAI 侧无参工具的 arguments 是空串而不是 `"{}"`，必须归一（plan「空参数归一」）。"""
    assert _parse_args("") == ({}, "")
    assert _parse_args("   ") == ({}, "")


@pytest.mark.parametrize("bad", ["不是 json", "[1, 2]", "null", "42"])
def test_parse_args_reports_non_object_json(bad: str) -> None:
    data, err = _parse_args(bad)

    assert data == {}
    assert err


def test_truncate_marks_line_overflow() -> None:
    text = "\n".join(f"line {n}" for n in range(3000))

    result = _truncate(text, max_lines=2000, max_chars=10**9)

    assert result.endswith("[truncated]")
    assert result.count("\n") == 2000  # 2000 行正文 + 1 行标记


def test_truncate_marks_char_overflow() -> None:
    result = _truncate("x" * 500, max_lines=10**9, max_chars=100)

    assert result == "x" * 100 + "\n[truncated]"


def test_truncate_leaves_short_text_alone() -> None:
    assert _truncate("短的", max_lines=10, max_chars=100) == "短的"


# ────────────────────────── F2-读：read_file ──────────────────────────


def test_read_file_numbers_lines(tmp_path: Any) -> None:
    target = tmp_path / "a.txt"
    target.write_text("第一行\n第二行\n")

    result = run(ReadFileTool().execute(args(path=str(target))))

    assert not result.is_error
    assert "     1\t第一行" in result.content
    assert "     2\t第二行" in result.content


def test_read_file_missing_file(tmp_path: Any) -> None:
    result = run(ReadFileTool().execute(args(path=str(tmp_path / "没有这个.txt"))))

    assert result.is_error
    assert "文件不存在" in result.content


def test_read_file_on_directory(tmp_path: Any) -> None:
    result = run(ReadFileTool().execute(args(path=str(tmp_path))))

    assert result.is_error
    assert "目录" in result.content


def test_read_file_truncates_long_file(tmp_path: Any) -> None:
    target = tmp_path / "long.txt"
    target.write_text("\n".join(f"line {n}" for n in range(3000)))

    result = run(ReadFileTool().execute(args(path=str(target))))

    assert "[truncated]" in result.content


@pytest.mark.parametrize("payload", ["", "{}", '{"path": ""}', "不是 json"])
def test_read_file_reports_bad_arguments(payload: str) -> None:
    result = run(ReadFileTool().execute(payload))

    assert result.is_error


# ────────────────────────── F2-写：write_file ──────────────────────────


def test_write_file_creates_nested_paths(tmp_path: Any) -> None:
    target = tmp_path / "a" / "b" / "c.txt"

    result = run(WriteFileTool().execute(args(path=str(target), content="内容")))

    assert not result.is_error
    assert target.read_text() == "内容"


def test_write_file_overwrites(tmp_path: Any) -> None:
    target = tmp_path / "a.txt"
    target.write_text("旧内容")

    run(WriteFileTool().execute(args(path=str(target), content="新内容")))

    assert target.read_text() == "新内容"


def test_write_file_allows_empty_content(tmp_path: Any) -> None:
    """空内容是**合法输入**（清空一个文件），不能被当成「缺参数」挡掉。"""
    target = tmp_path / "a.txt"
    target.write_text("旧内容")

    result = run(WriteFileTool().execute(args(path=str(target), content="")))

    assert not result.is_error
    assert target.read_text() == ""


def test_write_file_requires_content_key(tmp_path: Any) -> None:
    result = run(WriteFileTool().execute(args(path=str(tmp_path / "a.txt"))))

    assert result.is_error
    assert "content" in result.content


# ────────────────────────── F2-改：edit_file ──────────────────────────


def test_edit_file_replaces_unique_match(tmp_path: Any) -> None:
    target = tmp_path / "a.py"
    target.write_text("x = 1\ny = 2\n")

    result = run(
        EditFileTool().execute(
            args(path=str(target), old_string="y = 2", new_string="y = 3")
        )
    )

    assert not result.is_error
    assert target.read_text() == "x = 1\ny = 3\n"


@pytest.mark.parametrize(
    ("content", "old_string", "expect"),
    [
        ("x = 1\n", "y = 2", "未找到匹配"),
        ("abc\nabc\n", "abc", "匹配到 2 处"),
    ],
)
def test_edit_file_reports_distinguishable_errors(
    tmp_path: Any, content: str, old_string: str, expect: str
) -> None:
    """0 处和 >1 处是两种不同的问题，文案必须能区分（AC4）——模型据此做的调整完全不同。"""
    target = tmp_path / "a.txt"
    target.write_text(content)

    result = run(
        EditFileTool().execute(
            args(path=str(target), old_string=old_string, new_string="X")
        )
    )

    assert result.is_error
    assert expect in result.content
    # 出错时**一个字节都不许改**。
    assert target.read_text() == content


def test_edit_file_error_messages_differ_between_zero_and_many(tmp_path: Any) -> None:
    zero = tmp_path / "zero.txt"
    zero.write_text("hello\n")
    many = tmp_path / "many.txt"
    many.write_text("hello\nhello\n")

    zero_result = run(
        EditFileTool().execute(args(path=str(zero), old_string="hello", new_string="x"))
    )
    many_result = run(
        EditFileTool().execute(args(path=str(many), old_string="hello", new_string="x"))
    )

    assert zero_result.content != many_result.content


def test_edit_file_allows_deleting_text(tmp_path: Any) -> None:
    """`new_string` 可以是空串（删除一段），不能被当成缺参数。"""
    target = tmp_path / "a.txt"
    target.write_text("保留\n删掉\n")

    result = run(
        EditFileTool().execute(
            args(path=str(target), old_string="删掉\n", new_string="")
        )
    )

    assert not result.is_error
    assert target.read_text() == "保留\n"


def test_edit_file_rejects_empty_old_string(tmp_path: Any) -> None:
    """空 old_string 必须挡掉：`str.count("")` 返回「长度 + 1」，会报出一句莫名其妙的错误。"""
    target = tmp_path / "a.txt"
    target.write_text("abc")

    result = run(
        EditFileTool().execute(args(path=str(target), old_string="", new_string="x"))
    )

    assert result.is_error
    assert target.read_text() == "abc"


# ─────────────────── 字节保真：改文件不能碰别的部分 ───────────────────
#
# 这一组来自一次真实缺陷：`edit_file` 原来是 `read_text(errors="replace")` +
# `write_text(...)`，那两个默认值都会悄悄改动内容——`errors="replace"` 把非 UTF-8 的
# 字节换成 U+FFFD，universal newlines 把 `\r\n` 折叠成 `\n`。改一行，整个文件被重新
# 编码一遍，而返回的还是「已修改」，模型完全察觉不到。
#
# 所以下面一律断言**字节**而不是文本：`read_text()` 会替我们把 `\r\n` 归一成 `\n`，
# 正好把要验的东西遮掉。


def test_edit_file_keeps_crlf_line_endings(tmp_path: Any) -> None:
    """CRLF 文件改完之后仍然是 CRLF。"""
    target = tmp_path / "a.txt"
    target.write_bytes(b"alpha\r\nbeta\r\ngamma\r\n")

    result = run(
        EditFileTool().execute(
            args(path=str(target), old_string="beta", new_string="BETA")
        )
    )

    assert not result.is_error
    assert target.read_bytes() == b"alpha\r\nBETA\r\ngamma\r\n"


def test_edit_file_matches_lf_old_string_against_a_crlf_file(tmp_path: Any) -> None:
    """模型给的是 LF 换行的 `old_string`，文件是 CRLF——照样要匹配上。

    模型的 `old_string` 是从 read_file 抄的，而 read_file 走 universal newlines，
    给它的行尾一律是 `\\n`。这里要是不把 `\\n` 换成 `\\r\\n` 再试一次，模型在 CRLF
    项目里永远匹配不上跨行的原文，而它收到的错误提示（「必须逐字符一致」）会把它
    引向完全错误的方向。
    """
    target = tmp_path / "a.txt"
    target.write_bytes(b"alpha\r\nbeta\r\ngamma\r\n")

    result = run(
        EditFileTool().execute(
            args(path=str(target), old_string="beta\ngamma", new_string="BETA\nGAMMA")
        )
    )

    assert not result.is_error, result.content
    # 替换进去的那段也要跟着用 CRLF，否则文件就成了混合行尾。
    assert target.read_bytes() == b"alpha\r\nBETA\r\nGAMMA\r\n"


def test_edit_file_refuses_non_utf8_instead_of_replacing_bytes(tmp_path: Any) -> None:
    """非 UTF-8 的文件**拒绝修改**，一个字节都不动。

    原来的写法用 `errors="replace"` 读进来，于是每个非 UTF-8 字节都被换成 U+FFFD
    再写回去——**连没被碰的那几行一起毁掉**，返回的却是「已修改」。
    """
    target = tmp_path / "a.txt"
    before = b"caf\xe9\nhello\n"  # latin-1 的 é，不是合法 UTF-8
    target.write_bytes(before)

    result = run(
        EditFileTool().execute(
            args(path=str(target), old_string="hello", new_string="world")
        )
    )

    assert result.is_error
    assert "UTF-8" in result.content
    assert target.read_bytes() == before


def test_edit_file_leaves_untouched_lines_byte_identical(tmp_path: Any) -> None:
    """混合行尾的文件里只有**匹配到的那一段**该变，别处一个字节都不能动。

    这条是上面几条的兜底：它同时钉住「没做全文件换行归一」和「没有重新编码」。
    """
    target = tmp_path / "a.txt"
    target.write_bytes(b"a\r\nb\r\nc\nd\r\n")

    result = run(
        EditFileTool().execute(args(path=str(target), old_string="c", new_string="C"))
    )

    assert not result.is_error
    assert target.read_bytes() == b"a\r\nb\r\nC\nd\r\n"


def test_write_file_writes_utf8_regardless_of_locale(tmp_path: Any) -> None:
    """落盘编码写死 UTF-8，不跟随 locale。

    不写 `encoding=` 的话，在 `LC_ALL=C` 的环境（容器、CI、cron）里写中文会直接抛
    `'ascii' codec can't encode`。断言字节是最直接的验法——`read_text()` 会跟着
    locale 走，反而看不出问题。
    """
    target = tmp_path / "a.txt"
    content = "琪琪最喜欢居居"

    result = run(WriteFileTool().execute(args(path=str(target), content=content)))

    assert not result.is_error
    assert target.read_bytes() == content.encode("utf-8")
    # 「已写入 N 字节」里的 N 必须是 UTF-8 的字节数——落盘用的就是它，两个数字得是一回事。
    assert f"{len(content.encode('utf-8'))} 字节" in result.content


# ────────────────────────── F2-执行：bash ──────────────────────────


def test_bash_returns_stdout_and_zero_exit() -> None:
    result = run(BashTool().execute(args(command="echo hi")))

    assert not result.is_error
    assert "exit_code: 0" in result.content
    assert "hi" in result.content


def test_bash_nonzero_exit_is_error_but_keeps_output() -> None:
    """非零退出算失败（spec F9），但 stdout/stderr/退出码一个不少地留着。"""
    result = run(BashTool().execute(args(command="echo 出错了 >&2; exit 3")))

    assert result.is_error
    assert "exit_code: 3" in result.content
    assert "出错了" in result.content


def test_bash_timeout_is_reported_and_kills_the_process(tmp_path: Any) -> None:
    """超时要真的**把进程杀掉**，不能只是不等人。

    这里跑一个一直往文件里追加的循环：如果只是「不再等待」而没杀进程，
    循环会在后台继续写，文件就会一直长大。这是 T6 那处 `proc.kill()` 的回归测试。
    """
    marker = tmp_path / "tick.txt"
    command = f"while true; do echo tick >> {marker}; sleep 0.1; done"
    registry = Registry()
    registry.register(BashTool())

    result = run(registry.execute("bash", args(command=command), timeout=0.3))

    assert result.is_error
    assert "超时" in result.content

    # 等它把手上那一次循环写完，再取一个基准大小。
    run(asyncio.sleep(0.6))
    baseline = marker.stat().st_size if marker.exists() else 0
    run(asyncio.sleep(0.6))
    after = marker.stat().st_size if marker.exists() else 0

    assert after == baseline, "超时后子进程还在后台跑"


def test_bash_timeout_returns_promptly_for_pipelines_and_chained_commands() -> None:
    """超时之后**工具必须返回**，不能等孙子进程自然结束。

    这是一次真实缺陷的回归测试。根因在 `await proc.wait()`：它等的不只是进程退出，
    还有两个管道都 EOF；而 `sleep 300 | cat` 里的 `cat` 是 `sh` **fork 出来的后代**，
    只杀 `sh` 的话写端还攥在它手里，EOF 永远不来，`wait()` 就永远不返回。偏偏那句
    就待在 `except CancelledError` 里收尸——于是超时反而成了唯一出不去的地方。

    这里**必须用管道或串联命令**：单条命令会被 shell 用 `exec` 优化掉，`sh` 自己
    变成进程本体，只杀它就够了——那是唯一侥幸能过的形状。上面那条
    `test_bash_timeout_is_reported_and_kills_the_process` 用的循环恰好是单条命令，
    所以它在修复前也是绿的（它验的是「杀了进程没有」，不是「工具能不能返回」）。

    外面再套一层 `wait_for` 是为了让**回归时的表现是失败而不是挂死**——
    没有它，这条用例会跟着代码一起永远等下去。
    """
    registry = Registry()
    registry.register(BashTool())

    for command in ["sleep 300 | cat", "sleep 300; echo done", "cd /tmp && sleep 300"]:
        started = time.monotonic()
        result = run(
            asyncio.wait_for(
                registry.execute("bash", args(command=command), timeout=0.3),
                timeout=10,
            )
        )
        took = time.monotonic() - started

        assert result.is_error, command
        assert "超时" in result.content, command
        assert took < 5, f"{command} 超时后拖了 {took:.1f} 秒才返回"


def test_bash_timeout_kills_the_whole_process_tree(tmp_path: Any) -> None:
    """超时要杀掉**整棵进程树**，不只是 `sh` 自己。

    管道左边那个子 shell 是 `sh` 的后代，它活着就等于「命令还在跑」。所以这里让
    子 shell 不停地往文件里追加：只杀 `sh` 的话它会继续写下去，文件就一直长大。
    （上面那条用的是单条命令，`exec` 之后 `sh` 就是进程本体，盖不到这条路径。）
    """
    marker = tmp_path / "tick.txt"
    command = f"( while true; do echo tick >> {marker}; sleep 0.1; done ) | cat"
    registry = Registry()
    registry.register(BashTool())

    result = run(registry.execute("bash", args(command=command), timeout=0.3))

    assert result.is_error
    assert "超时" in result.content

    # 等它把手上那一次循环写完，再取一个基准大小。
    run(asyncio.sleep(0.6))
    baseline = marker.stat().st_size if marker.exists() else 0
    run(asyncio.sleep(0.6))
    after = marker.stat().st_size if marker.exists() else 0

    assert after == baseline, "超时后管道左侧的子 shell 还在后台跑（进程组没杀干净）"


# ────────────────────────── F2-找：glob ──────────────────────────


def test_glob_finds_files_recursively(tmp_path: Any) -> None:
    (tmp_path / "src" / "deep").mkdir(parents=True)
    (tmp_path / "src" / "top.py").write_text("")
    (tmp_path / "src" / "deep" / "bottom.py").write_text("")
    (tmp_path / "readme.md").write_text("")

    result = run(GlobTool().execute(args(pattern="**/*.py", path=str(tmp_path))))

    assert not result.is_error
    assert result.content.splitlines() == ["src/deep/bottom.py", "src/top.py"]


def test_glob_no_match_is_not_an_error(tmp_path: Any) -> None:
    """无匹配是**正常结果**，不是错误——标成错误模型会以为工具坏了然后反复重试。"""
    result = run(GlobTool().execute(args(pattern="**/*.zzz", path=str(tmp_path))))

    assert not result.is_error
    assert "无匹配" in result.content


def test_glob_rejects_non_directory(tmp_path: Any) -> None:
    target = tmp_path / "a.txt"
    target.write_text("")

    result = run(GlobTool().execute(args(pattern="*", path=str(target))))

    assert result.is_error


# ────────────────────────── F2-搜：grep ──────────────────────────


def test_grep_reports_file_line_and_content(tmp_path: Any) -> None:
    (tmp_path / "a.py").write_text("import os\n\ndef main():\n    pass\n")
    (tmp_path / "b.py").write_text("def other():\n    pass\n")

    result = run(GrepTool().execute(args(pattern=r"def \w+", path=str(tmp_path))))

    assert not result.is_error
    assert "a.py:3:def main():" in result.content
    assert "b.py:1:def other():" in result.content


def test_grep_honours_glob_filter(tmp_path: Any) -> None:
    (tmp_path / "a.py").write_text("needle\n")
    (tmp_path / "b.md").write_text("needle\n")

    result = run(
        GrepTool().execute(args(pattern="needle", path=str(tmp_path), glob="*.py"))
    )

    assert "a.py" in result.content
    assert "b.md" not in result.content


def test_grep_no_match_is_not_an_error(tmp_path: Any) -> None:
    (tmp_path / "a.py").write_text("hello\n")

    result = run(
        GrepTool().execute(args(pattern="绝不存在的字符串", path=str(tmp_path)))
    )

    assert not result.is_error
    assert "无命中" in result.content


def test_grep_rejects_invalid_regex(tmp_path: Any) -> None:
    result = run(GrepTool().execute(args(pattern="(未闭合", path=str(tmp_path))))

    assert result.is_error
    assert "正则" in result.content


def test_grep_reports_line_numbers_after_an_overlong_line(tmp_path: Any) -> None:
    """超长行之后的**行号不能错位**。

    超长行我们看不全，只能截断；但截断之后必须把这行的剩余部分丢掉，
    否则后半截会被当成新的一行，后面所有行号都往前错——错误的行号比没有行号更误导人。
    """
    target = tmp_path / "big.txt"
    # 第一行必须**真的**超过上限，否则这条测试什么也没验到。
    target.write_text("x" * (MAX_LINE_CHARS + 10) + "\n找我的\n")

    result = run(GrepTool().execute(args(pattern="找我的", path=str(tmp_path))))

    assert not result.is_error
    assert "big.txt:2:找我的" in result.content
    # 顺带确认「没看全」这件事被说出来了，而不是当成普通的「没命中」。
    assert "超长行" in result.content


# ────────────── 正则的时间预算（灾难性回溯不能冻死界面） ──────────────
#
# `(a+)+$` 这类嵌套量词是模型很容易写出来的形状，而 `re` 是回溯式引擎：实测输入
# 28 个字符要 14 秒、40 个字符要几个小时。`re.search` 又是同步 C 调用，卡住的时候
# 事件循环连取消回调都跑不了，`Registry` 那层 30 秒的 `wait_for` 形同虚设——用户
# 看到的是冻死的界面，连 Ctrl+C 都按不动，只能杀进程。
#
# 而且 `_sre` 匹配期间**一直持有 GIL**：实测拿 60 字符的输入跑，`wait_for(20s)` 不触发，
# 另起一个线程准备 25 秒后 `os._exit` 的看门狗也没能执行，进程只能 `kill -9`。
# 也就是说「丢线程 / 加超时」在这里统统无效。
#
# 所以 `grep` 自己用 `SIGALRM` 给每次匹配掐表（`grep_tool.MATCH_BUDGET`）。它**是唯一**
# 能真正中断的办法，靠的是 CPython 的 `_sre` 在匹配循环里会周期性调用
# `PyErr_CheckSignals()`——Ctrl+C 能打断一个跑疯了的正则，靠的就是它。信号处理器跑在
# 主线程上，不需要抢 GIL，所以它能在别人都饿死的时候插进来。


def test_grep_stops_a_catastrophic_regex_instead_of_freezing(tmp_path: Any) -> None:
    """灾难性回溯要被**掐断**，而不是算到天荒地老。

    这里输入 60 个字符——修复前这条正则要跑几个小时。用例盯的是**结果**：必须在预算
    附近返回一条结构化错误。外面再套一层 `wait_for`，是为了让回归时的表现是失败
    而不是挂死。
    """
    (tmp_path / "a.txt").write_text("a" * 60 + "!")
    registry = Registry()
    registry.register(GrepTool())

    started = time.monotonic()
    result = run(
        asyncio.wait_for(
            registry.execute(
                "grep", args(pattern="(a+)+$", path=str(tmp_path)), timeout=30
            ),
            timeout=10,
        )
    )
    took = time.monotonic() - started

    assert result.is_error
    # 错误里要**说清楚**：模型读到这句才知道下一轮把正则改具体些，
    # 而不是把「搜不动」当成「没有这个东西」。
    assert "灾难性回溯" in result.content
    assert "a.txt:1" in result.content
    assert took < 5, f"掐表没生效，跑了 {took:.1f} 秒"


def test_grep_still_matches_a_long_line_within_the_budget(tmp_path: Any) -> None:
    """掐表不能把**正常、但输入很长**的匹配误杀。

    上一条证明慢的会被掐，这一条证明快的不会被连坐——两条一起看，
    「预算」这件事才算验明白。
    """
    (tmp_path / "a.txt").write_text("x" * 5000 + "END\n")

    result = run(GrepTool().execute(args(pattern="^x+END$", path=str(tmp_path))))

    assert not result.is_error
    assert "a.txt:1:" in result.content


def test_grep_leaves_the_alarm_handler_as_it_found_it(tmp_path: Any) -> None:
    """搜完之后 `SIGALRM` 的处理器要还原。

    不还原的话，进程里别的代码会接到一个不该在的处理器——而 `SIGALRM` 的默认动作是
    **直接杀掉进程**，这种残留属于查不出来的那类 bug。
    """
    (tmp_path / "a.txt").write_text("hello\n")
    before = signal.getsignal(signal.SIGALRM)

    run(GrepTool().execute(args(pattern="hello", path=str(tmp_path))))

    assert signal.getsignal(signal.SIGALRM) is before
