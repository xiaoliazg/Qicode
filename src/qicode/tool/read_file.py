"""`read_file`：读文件，带行号（F2-读）。"""

from pathlib import Path
from typing import Any

from qicode.tool import (
    Result,
    _NotRegularFile,
    _parse_args,
    _refuse_if_special,
    _require_str,
    _run_blocking,
    _truncate,
)

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

    **开读之前先 `stat` 看一眼 inode 类型**（`_refuse_if_special`），非普通文件直接拒绝。
    不放行让 `open()` 去碰运气，是因为那几种路径的失败方式都不体面：没有写端的 FIFO 上
    `open()` **永久阻塞**（实测：工具如实报了超时，进程却再也退不出去）；`/dev/zero` 会
    一直吐零；`/dev/random` 收不住。而 `read_file` 的语义是「读一个文件的**内容**」——
    管道和设备没有「内容」这个概念，读出来是什么取决于另一端有没有人在写。所以这里
    不是「读失败」，是「这东西不该用 read_file 读」。

    这一步**必须在线程里**做：`stat()` 自己碰上网盘挂死一样会阻塞，放事件循环上等于
    把刚绕开的坑换个地方挖。目录故意**不在这里拦**——留给 `open()` 抛
    `IsADirectoryError`，那句「是一个目录」的文案已经有人认了，不折腾。

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
    target = Path(path)
    _refuse_if_special(target)
    with target.open("r", encoding="utf-8", errors="replace") as handle:
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
            # 整个「打开 + 读」扔进**守护线程**（`_run_blocking`）。路径是模型给的，可能
            # 指向网络盘，`stat()` / `open()` / `read()` 都可能阻塞几秒甚至无限久，而工具
            # 是直接在事件循环上跑的，就地阻塞的代价是**整个界面冻住**（N2），连 Ctrl+C
            # 都按不动。扔进线程只占一个线程，循环照转。
            #
            # 用守护线程而不是 `asyncio.to_thread`：后者跑在默认线程池里，进程收尾时会被
            # join，一个卡住的读就能让 Qicode **关不掉**（实测形状见 `_run_blocking`）。
            text = await _run_blocking(_read_head, path)
        except _NotRegularFile as exc:
            # 文案只陈述事实，**不给「改用 bash 试试」之类的建议**：FIFO 上没有写端时
            # `cat` 一样会卡住，`/dev/random` 更是永远读不完——那种建议是把模型从一个
            # 坑引到另一个坑。让它停在「这条路走不通」上，比给它一条假出路好。
            return Result(
                f"{path} {exc.reason}，read_file 只能读普通文件", is_error=True
            )
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
