"""`grep`：搜文件内容（F2-搜）。"""

import asyncio
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO

from qicode.tool import Result, _parse_args, _require_str

#: 最多回多少条命中。``file:line:content`` 一行就占不少地方，100 条已经能说明问题（N5）。
MAX_HITS = 100

#: 单行最多看多少字符。超过就当它「没看全」并标注出来。
MAX_LINE_CHARS = 1_000_000


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
            hits, truncated = await self._search(root, name_filter, regex)
        except (OSError, ValueError) as exc:
            return Result(f"搜索失败: {exc}", is_error=True)

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
    ) -> tuple[list[str], bool]:
        """扫描根目录下的文件，返回 `(命中列表, 是否因超限而提前停下)`。"""
        hits: list[str] = []
        truncated = False

        for path in root.rglob(name_filter):
            # 每进一个文件就让出一次 event loop：一次跨仓库的搜索可能扫几千个文件，
            # 一直不放会把界面冻住（N2）。
            await asyncio.sleep(0)
            if len(hits) >= MAX_HITS:
                truncated = True
                break
            if not path.is_file():
                continue

            try:
                display = os.path.relpath(path, root)
                overlong = False
                with path.open("r", errors="replace") as handle:
                    lines = enumerate(_iter_lines(handle, MAX_LINE_CHARS), 1)
                    for lineno, (line, was_cut) in lines:
                        if was_cut:
                            overlong = True
                            continue
                        if regex.search(line):
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

        return hits, truncated
