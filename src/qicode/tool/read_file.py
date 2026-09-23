"""`read_file`：读文件，带行号（F2-读）。"""

import asyncio
from pathlib import Path
from typing import Any

from qicode.tool import Result, _parse_args, _require_str, _truncate

#: 最多回给模型多少行。
#:
#: 2000 行大约是「一个中等大小的源文件」，够它看懂上下文，又不至于一轮就把上下文塞满
#: （N5）。真需要看更大的文件，它应该用 grep 先定位再局部读——这也是它的常规打法。
MAX_LINES = 2000

#: 最多读多少**字符**（不是字节）。
#:
#: 这个上限同时是内存护栏：读的时候只读这么多，不把整个文件先吞进内存再回头截断。
MAX_CHARS = 256 * 1024


def _read_head(path: str) -> str:
    """读文件开头，最多 `MAX_CHARS + 1` 个字符。

    多读一个字符是为了区分「刚好读满」和「还有更多」——不差这一个字符。

    这是个**同步**函数，由调用方丢进工作线程跑（见 `execute`）。写成同步的而不是
    `async def` 是有意的：文件 IO 没有异步版本，硬套 `async` 只是把阻塞换个地方放。

    三处编码相关的选择，都是刻意的：

    - `encoding="utf-8"` 写死不跟随 locale。不写的话，在 `LC_ALL=C` 的环境里
      （容器、CI、cron 中很常见）读中文文件会整篇变成 `\\ufffd`。
    - 这里**保留** `errors="replace"`：读是只读操作，替换掉坏字节顶多让模型看到几个
      `\\ufffd`，磁盘上的数据一个字节都没动。`edit_file` 那边就不能这么宽容——它要
      写回去，被替换过的内容会**落盘**，所以那边是严格解码、解不开就拒绝。
    - `newline` 不指定，用默认的 universal newlines（`\\r\\n` → `\\n`）。这正是要给模型
      看的形状：它不必知道文件的换行风格，抄回来的 `old_string` 也统一是 `\\n`。
      把这件事翻译回真实字节是 `edit_file` 的责任。
    """
    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        return handle.read(MAX_CHARS + 1)


class ReadFileTool:
    """读取文件内容，返回带行号的文本。"""

    def name(self) -> str:
        return "read_file"

    def description(self) -> str:
        return (
            "读取指定路径的文件内容，返回带行号的文本（行号从 1 开始）。"
            "用于查看代码、配置或文档。文件很大时会截断并标注 [truncated]。"
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要读取的文件路径（相对当前工作目录，或绝对路径）",
                },
            },
            "required": ["path"],
        }

    async def execute(self, args: str) -> Result:
        data, err = _parse_args(args)
        if err:
            return Result(err, is_error=True)
        path, err = _require_str(data, "path")
        if err:
            return Result(err, is_error=True)

        try:
            # 整个「打开 + 读」扔进工作线程。**这不只是为了消掉 lint**：路径是模型给的，
            # 可能指向网络盘或 FIFO，`open()` / `read()` 都可能阻塞几秒甚至无限久。
            # 而工具是直接在事件循环上跑的，就地阻塞的代价是**整个界面冻住**（N2），
            # 连 Ctrl+C 都按不动。扔进线程只占一个线程，循环照转。
            #
            # 线程杀不掉：超时取消之后，这次读会做完再自行退出，结果被丢弃。
            # 对一次有上限的读来说，这个代价可以接受。
            text = await asyncio.to_thread(_read_head, path)
        # 目录要先判：`open()` 对目录抛的 `IsADirectoryError` 也是 OSError，
        # 会被下面接住，但那时只能说一句「读取失败」，不如直接说清楚是什么问题。
        except IsADirectoryError:
            return Result(f"{path} 是一个目录，不是文件", is_error=True)
        except FileNotFoundError:
            return Result(f"文件不存在: {path}", is_error=True)
        except OSError as exc:
            # 权限不足、路径里有一段不是目录、符号链接成环……都归到这里。
            return Result(f"读取失败: {path}: {exc}", is_error=True)

        lines = text.splitlines()
        numbered = "\n".join(f"{n:6d}\t{line}" for n, line in enumerate(lines, 1))
        return Result(_truncate(numbered, MAX_LINES, MAX_CHARS))
