"""`bash`：执行 shell 命令（F2-执行）。"""

import asyncio
import contextlib
from typing import Any

from qicode.tool import Result, _parse_args, _require_str, _truncate

#: 给模型看的输出上限。**退出码排在整个正文的最前面**，所以无论怎么截，
#: 它都不会被截掉——「命令成没成」是模型最先要判断的事。
MAX_OUTPUT_LINES = 10_000
MAX_OUTPUT_CHARS = 30_000

#: 单个管道最多往内存里收多少字节。
#:
#: 这个数比上面那两个大一个量级，是有意的：它管的是**内存**，不是可读性。
#: `yes` 跑 30 秒能吐出好几个 G，全收进内存就为了回头截断，纯属白给（N5 要的是
#: 「有上限」，不是「有上限地浪费」）。收到这个量就停手，剩下的交给 `_truncate` 标注。
_MAX_PIPE_BYTES = 256 * 1024


class BashTool:
    """在当前工作目录下执行一条 shell 命令。"""

    def name(self) -> str:
        return "bash"

    def description(self) -> str:
        return (
            "在当前工作目录下执行一条 shell 命令，返回退出码、标准输出与标准错误。"
            "适合跑测试、看 git 状态、列目录、统计行数这类事。"
            "命令会**真的被执行**，请只运行你确实需要、且不会破坏环境的命令。"
            "超过 30 秒未结束的命令会被终止。"
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令，例如 `pytest -q` 或 `git status --short`",
                },
            },
            "required": ["command"],
        }

    async def execute(self, args: str) -> Result:
        data, err = _parse_args(args)
        if err:
            return Result(err, is_error=True)
        command, err = _require_str(data, "command")
        if err:
            return Result(err, is_error=True)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return Result(f"命令启动失败: {exc}", is_error=True)

        async def slurp(stream: asyncio.StreamReader) -> bytes:
            """把一个管道读到 EOF，或者读满 `_MAX_PIPE_BYTES` 就停手。

            读满时**顺手把进程杀掉**——这不是顺手，是必须的。stdout 读满之后我们
            不再读它，管道缓冲区（通常 64KB）很快就填满，子进程随即**阻塞在写 stdout 上**；
            于是 stderr 那头永远等不到 EOF，`gather` 永远不返回，整个工具卡死到超时。
            杀掉进程是解开这个死锁的唯一办法。
            """
            buffer = bytearray()
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    return bytes(buffer)
                room = _MAX_PIPE_BYTES - len(buffer)
                if len(chunk) >= room:
                    buffer.extend(chunk[:room])
                    proc.kill()
                    return bytes(buffer)
                buffer.extend(chunk)

        try:
            # 上面两个参数都传了 `PIPE`，所以这两条管道必然不是 None——类型标注里的
            # `| None` 是「没传 PIPE 时才会是 None」，我们传了。这一句是给类型检查器的交代。
            assert proc.stdout is not None
            assert proc.stderr is not None
            # 两个管道**必须并发读**。顺序读会死锁：先读 stdout 读到 EOF 意味着等命令
            # 结束，而命令可能正卡在「stderr 写满了没人读」上。
            stdout_bytes, stderr_bytes = await asyncio.gather(
                slurp(proc.stdout), slurp(proc.stderr)
            )
            await proc.wait()
        except asyncio.CancelledError:
            # 走到这儿有两类原因：Registry 那层 `wait_for` 超时取消了，或者用户退出。
            # **必须自己动手杀**——`wait_for` 取消的只是「等待」这个动作，子进程还在
            # 后台跑着，一条 `sleep 300` 会一直挂着不放。
            proc.kill()
            # 杀完还要**收尸**：等它真的退出，transport 才会把管道关掉。
            # 不收的话进程是死了，可 `BaseSubprocessTransport` 还挂在事件循环上，
            # 循环一关就在 GC 里冒一句 `RuntimeError: Event loop is closed`
            # （单测里实测到了）。收尸这件事本身就是「不许抛」的，所以包一层。
            with contextlib.suppress(Exception):
                await proc.wait()
            raise

        stdout = stdout_bytes.decode(errors="replace")
        stderr = stderr_bytes.decode(errors="replace")
        body = f"exit_code: {proc.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"
        # 非零退出算**错误**（`docs/v2/spec.md` F9 把「命令超时 / 非零退出」与
        # 「文件不存在」并列成执行失败）：模型自己挑的命令跑挂了，就该一眼看出「这条没成」，
        # 而不是从一大段输出里自己琢磨。细节一个不少地留着，它照样能判断原因。
        #
        # 已知边界：这里杀的是 `sh` 本身。如果命令是管道（`a | b`），某些平台上
        # `b` 可能变成孤儿继续跑。本阶段不引入进程组管理（spec「不做的事」）。
        return Result(
            _truncate(body, MAX_OUTPUT_LINES, MAX_OUTPUT_CHARS),
            is_error=proc.returncode != 0,
        )
