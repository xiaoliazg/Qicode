"""`grep`：搜文件内容（F2-搜）。"""

import asyncio
import contextlib
import os
import re
import signal
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO

from qicode.tool import Result, _parse_args, _require_str

#: 最多回多少条命中。``file:line:content`` 一行就占不少地方，100 条已经能说明问题（N5）。
MAX_HITS = 100

#: 单行最多看多少字符。超过就当它「没看全」并标注出来。
MAX_LINE_CHARS = 1_000_000

#: 单次正则匹配的时间预算（秒）。
#:
#: 为什么需要它：`re` 的正则引擎是**回溯式**的，`(a+)+$` 这类嵌套量词在长行上会指数级
#: 爆炸——实测输入 28 个字符要 14 秒，40 个字符要几个小时。而 `re.search` 是**同步 C
#: 调用**，它卡住的时候事件循环连取消回调都跑不了，`Registry` 那层 30 秒的 `wait_for`
#: 形同虚设：界面直接冻死，连 Ctrl+C 都按不动，用户只能杀进程。
#:
#: 更狠的是 `_sre` 匹配期间**一直持有 GIL**（纯 C 代码，不像 `time.sleep` 那样会主动
#: 让出）。实测拿一个 60 字符的输入跑：`asyncio.wait_for(20s)` 不触发，另起一个线程
#: 准备 25 秒后 `os._exit` 的看门狗也没能执行——整个进程只剩这一个 C 调用在转，
#: 别的线程全饿死，最后只能 `kill -9`。所以「丢线程 / 加超时」这类办法在这里全都无效，
#: 唯一能真正中断它的就是信号（靠 `_sre` 自己会调 `PyErr_CheckSignals()`）。
#:
#: 正常匹配是微秒级的，1 秒绝对够；真撞上灾难性回溯，它把「几小时」变成「1 秒」。
MATCH_BUDGET = 1.0


class _MatchTimeout(Exception):
    """一次正则匹配超出了 `MATCH_BUDGET`——多半是灾难性回溯。"""


def _on_alarm(signum: int, frame: Any) -> None:
    raise _MatchTimeout


@contextlib.contextmanager
def _alarm_armed() -> Iterator[bool]:
    """搜索期间把 `SIGALRM` 接到 `_MatchTimeout` 上，出来时还原。

    **为什么信号能打断一个跑疯了的正则**：CPython 的 `_sre` 在匹配循环里会周期性调用
    `PyErr_CheckSignals()`——Ctrl+C 之所以能中断灾难性回溯，靠的就是它。既然它会检查
    信号，处理器里抛出的异常就能从匹配内部**真正中断**它。这跟「丢进线程然后不再等」
    有本质区别：线程那条路里，那个 CPU 还在烧，烧几个小时。

    yield 的布尔值表示**这一次究竟装上了没有**，调用方据此决定要不要 `setitimer`。
    没装上还去设的话，`SIGALRM` 会走系统默认动作——那是**直接杀掉进程**，
    比「慢一点」严重得多。

    **只对主线程有效**，这正是 `grep` 与 `read_file` 做法不同的原因：`read_file` 把整个
    读丢进工作线程（`_run_blocking`），`grep` 不能——`signal.signal` 在非主线程直接抛
    `ValueError`，而这层保护恰恰是它最需要的东西。

    Windows 没有 `SIGALRM`、非主线程装不上，两种情况都降级成「不掐表」：灾难性回溯
    照旧会把那一行算完，但工具本身还能用，不会一上来就报错。
    """
    if (
        not hasattr(signal, "setitimer")
        or threading.current_thread() is not threading.main_thread()
    ):
        yield False
        return

    previous = signal.signal(signal.SIGALRM, _on_alarm)
    try:
        yield True
    finally:
        # 顺序不能反：先把表摘掉，再还原处理器。反过来写的话，在两步之间到期的
        # 那一次会打到**别人的**处理器上去。
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _search_line(
    regex: re.Pattern[str], line: str, armed: bool
) -> re.Match[str] | None:
    """在时间预算内匹配一行；超预算就抛 `_MatchTimeout`。

    每次匹配掐一次表。正常匹配是微秒级的，这两下 `setitimer` 的开销完全可以忽略，
    换来的却是「撞上灾难性回溯时最多卡 1 秒」。
    """
    if not armed:
        return regex.search(line)
    signal.setitimer(signal.ITIMER_REAL, MATCH_BUDGET)
    try:
        return regex.search(line)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def _iter_lines(handle: TextIO, max_line: int) -> Iterator[tuple[str, bool]]:
    """逐行读，产出 `(行内容, 这一行是否被截断)`。

    **为什么不用 `for line in handle`**（那个写法更短）：它是先把整行读进内存再交给你，
    碰上「整个文件就一行」的输入——压缩过的 js、单行 JSON——会直接把内存吃光。
    改用 `readline(上限)`：它在换行处或读满上限处停下，内存有界；返回值本身还能
    告诉我们「这一行到底读完了没有」。

    被截断的行必须让调用方知道。搜不到关键字时，它得能区分「这行里没有」和
    「这行我们压根没看全」——后者会让模型得出「这里没有我要找的东西」这个错误结论。
    """
    while True:
        line = handle.readline(max_line + 1)
        if not line:
            return
        if line.endswith("\n"):
            yield line, False
            continue
        if len(line) <= max_line:
            # 没换行、也没到上限 → 文件的最后一行（末尾没有换行符）。
            yield line, False
            return
        # 读满上限还没等到换行：这是一条超长行。先交出前面一段，
        # 然后**把这行的剩余部分整个丢掉**——不丢的话，剩下那半截会被当成新的一行，
        # 后面所有行号都会往前错，而错误的行号比没有行号更误导人。
        yield line, True
        while True:
            rest = handle.readline(max_line + 1)
            if not rest or rest.endswith("\n"):
                break


class GrepTool:
    """在文件内容里搜正则。"""

    def name(self) -> str:
        return "grep"

    def description(self) -> str:
        return (
            "在文件内容里搜索正则表达式，返回 `文件:行号:内容` 形式的命中列表。"
            "适合定位「某个函数/变量/字符串在哪定义、在哪用到」。"
            "`pattern` 是 Python 正则，普通关键字直接写即可。"
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "要搜索的 Python 正则表达式，例如 `def main` 或 `class \\w+Tool`",
                },
                "path": {
                    "type": "string",
                    "description": "搜索的根目录，默认当前工作目录",
                },
                "glob": {
                    "type": "string",
                    "description": "只搜文件名匹配该 glob 的文件，例如 `*.py`，默认搜全部",
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, args: str) -> Result:
        data, err = _parse_args(args)
        if err:
            return Result(err, is_error=True)
        pattern, err = _require_str(data, "pattern")
        if err:
            return Result(err, is_error=True)

        try:
            regex = re.compile(pattern)
        except re.error as exc:
            # 正则写错是模型自己的问题，说清楚哪里错，它下次就能改对。
            return Result(f"正则表达式非法: {exc}", is_error=True)

        root = Path(data.get("path") or ".")
        if not root.is_dir():
            return Result(f"路径不是目录: {root}", is_error=True)

        name_filter = data.get("glob") or "*"
        try:
            hits, truncated, err = await self._search(root, name_filter, regex)
        except (OSError, ValueError) as exc:
            return Result(f"搜索失败: {exc}", is_error=True)
        # 搜索被中途叫停（正则太慢）。跟参数错误一样走**结果**通道——这是一条要回灌
        # 给模型的话，它读到之后下一轮就会把 pattern 改对。
        if err:
            return Result(err, is_error=True)

        if not hits:
            # 同 glob：**无命中不是错误**（`docs/v2/spec.md` F9）。
            return Result(f"无命中：没有任何文件内容匹配 `{pattern}`")

        body = "\n".join(hits)
        if truncated:
            body += (
                f"\n…（命中太多，只显示前 {MAX_HITS} 条。"
                f"请把 pattern 写得更具体，或用 glob 限定文件范围）"
            )
        return Result(body)

    async def _search(
        self, root: Path, name_filter: str, regex: re.Pattern[str]
    ) -> tuple[list[str], bool, str]:
        """扫描根目录下的文件，返回 `(命中列表, 是否因超限而提前停下, 错误说明)`。

        三元组跟 `_parse_args` / `_require_str` 是同一个套路：错误走返回值、不走异常。
        「正则太慢」和「路径不是目录」一样，都是要回灌给模型的**结果**。
        """
        hits: list[str] = []
        truncated = False

        # 整个搜索期间把 SIGALRM 接上。`armed` 说的是这一次究竟装没装上——
        # 没装上就不能 `setitimer`，那样会走系统默认动作把进程杀掉（见 `_alarm_armed`）。
        with _alarm_armed() as armed:
            for path in root.rglob(name_filter):
                # 每进一个文件就让出一次 event loop：一次跨仓库的搜索可能扫几千个文件，
                # 一直不放会把界面冻住（N2）。
                #
                # 这一句也是 `grep` 不能像 `read_file` 那样整个丢进线程的另一半理由：
                # 丢进去了，这个「让出」就失去意义，而信号那层保护还会整个失效。
                await asyncio.sleep(0)
                if len(hits) >= MAX_HITS:
                    truncated = True
                    break
                if not path.is_file():
                    continue

                try:
                    display = os.path.relpath(path, root)
                    overlong = False
                    # `encoding="utf-8"` 写死，理由同 `read_file._read_head`：不写就跟随
                    # locale，在 `LC_ALL=C` 的环境里搜中文等于什么都搜不到——每个非 ASCII
                    # 字节都被换成了 U+FFFD。
                    with path.open("r", encoding="utf-8", errors="replace") as handle:
                        lines = enumerate(_iter_lines(handle, MAX_LINE_CHARS), 1)
                        for lineno, (line, was_cut) in lines:
                            if was_cut:
                                overlong = True
                                continue
                            try:
                                matched = _search_line(regex, line, armed)
                            except _MatchTimeout:
                                # 一行就吃掉了整个预算，说明问题出在**这个正则**上。
                                # 继续往下搜只会一行一行地再撞一遍，所以直接中止整次搜索；
                                # 已经找到的那几条也不要了——正则本身是错的，半份结果
                                # 只会让模型以为「就这么多」。
                                #
                                # 文案单独拎出来赋值再返回，是因为元组里的隐式字符串拼接
                                # 容易被误读成「漏了逗号」（ruff ISC004）。
                                reason = (
                                    f"正则 `{regex.pattern}` 在 {display}:{lineno} 上匹配超过 "
                                    f"{MATCH_BUDGET:g} 秒仍未结束，已中止搜索。这通常是嵌套"
                                    "量词（如 `(a+)+`）在长行上引发的灾难性回溯，"
                                    "请改写 pattern 让它更具体。"
                                )
                                return [], False, reason
                            if matched:
                                hits.append(f"{display}:{lineno}:{line.rstrip()}")
                                if len(hits) >= MAX_HITS:
                                    truncated = True
                                    break
                    if overlong:
                        # 每个文件只提一次，不然一个大压缩文件就能把 100 条额度全占了。
                        hits.append(
                            f"{display}: [该文件含超长行（超过 {MAX_LINE_CHARS} 字符），未完整搜索]"
                        )
                except OSError:
                    # 权限不足、读到一半文件被删、路径是个设备文件……跳过这个文件接着搜。
                    # 一个文件读不了不该让整次搜索失败。
                    continue

        return hits, truncated, ""
