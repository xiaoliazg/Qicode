"""`bash`：执行 shell 命令（F2-执行）。"""

import asyncio
import contextlib
import os
import signal
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

#: 上面那个上限的 KB 说法。只出现在**给模型看的文案**里——模型要的是一个能写进
#: 它自己推理的量，`262144` 不如 `256KB` 好使。
_MAX_PIPE_KB = _MAX_PIPE_BYTES // 1024


def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    """杀掉**整个进程组**，不只是 `sh` 自己。

    为什么不能只杀 `sh`：`create_subprocess_shell` 起的是 `sh -c "..."`，命令行里的
    管道和多条命令都是 `sh` **再 fork 出来的后代**。只杀 `sh` 的话，`sleep 300 | cat`
    里的 `cat` 还活着，而它正攥着我们那根 stdout 管道的写端。

    这一点直接决定超时能不能生效，所以值得说清楚：`await proc.wait()` 等的不只是
    进程退出，还有**所有管道都断开**——`asyncio` 的 `BaseSubprocessTransport` 就是这么
    实现的（`_try_finish` 要 `_pipes` 全空才唤醒等待者，而 pipe 只在读到 EOF 时才
    `connection_lost`）。写端没人放，`wait()` 就永远不返回。而它偏偏待在
    `except asyncio.CancelledError` 里收尸——于是 30 秒的 `wait_for` 永远等不到自己的
    超时，工具行一直转圈，界面卡死到只能杀进程。

    `start_new_session=True`（见 `execute`）让 `sh` 成为新进程组的组长，后代全都继承
    同一个 pgid，所以这里一个 `killpg` 就能把整棵树收掉。
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        # 进程组已经没了（它跑得比我们快），或者权限不够（理论上不会，是我们自己起的）。
        # 退回杀单个进程，聊胜于无。
        with contextlib.suppress(ProcessLookupError):
            proc.kill()


class BashTool:
    """在当前工作目录下执行一条 shell 命令。"""

    def name(self) -> str:
        return "bash"

    def description(self) -> str:
        return (
            "在当前工作目录下执行一条 shell 命令，返回退出码、标准输出与标准错误。"
            "适合跑测试、看 git 状态、列目录、统计行数这类事。"
            "命令会**真的被执行**，请只运行你确实需要、且不会破坏环境的命令。"
            "超过 30 秒未结束的命令会被终止；"
            f"输出超过 {_MAX_PIPE_KB}KB 的命令也会被终止——"
            "**那条命令的退出码会显示成 -9，那是 Qicode 发的，不是命令自己崩了**，"
            "这种情况请缩小命令范围后重试。"
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
                # 子进程**不能**继承我们的 stdin。Textual 把终端设成 raw 模式、自己接管了
                # fd 0，子进程跟着抢的话，`cat`、`grep needle`（模型漏写路径时很常见）这类
                # 要读 stdin 的命令会把用户敲进输入框的字吃掉——用户完全不知道字去哪了，
                # 而且还要白白空转到 30 秒超时。
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # 让 sh 成为新会话的组长：它 fork 出来的**所有**后代都继承同一个进程组，
                # 超时的时候一个 killpg 就能把整棵进程树收掉（理由详见 `_kill_tree`）。
                #
                # 副作用是这些进程不再属于终端的前台进程组，因此收不到终端的 Ctrl+C——
                # 这正合我们的意：TUI 的 Ctrl+C 是「退出 Qicode」，没有理由顺手把用户
                # 上一个问题里跑着的命令一起杀掉。
                start_new_session=True,
            )
        except OSError as exc:
            return Result(f"命令启动失败: {exc}", is_error=True)

        async def slurp(stream: asyncio.StreamReader) -> tuple[bytes, bool]:
            """把一个管道读到 EOF，或者读满 `_MAX_PIPE_BYTES` 就停手。

            读满时**顺手把命令停掉**——这不是顺手，是必须的。stdout 读满之后我们
            不再读它，管道缓冲区（通常 64KB）很快就填满，子进程随即**阻塞在写 stdout 上**；
            于是 stderr 那头永远等不到 EOF，`gather` 永远不返回，整个工具卡死到超时。
            停掉整棵进程树是解开这个死锁的唯一办法。

            返回 `(内容, 是不是因为读满上限才停的)`。第二个值要一路带到结果正文里去：
            读满时我们**已经发过 SIGKILL**，`returncode` 会变成 `-9`——不说清楚的话，
            模型只看到一个它没见过的信号退出码，然后开始瞎猜命令为什么崩，而真相是
            「我们嫌它话太多，把它掐了」。
            """
            buffer = bytearray()
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    return bytes(buffer), False
                room = _MAX_PIPE_BYTES - len(buffer)
                if len(chunk) >= room:
                    buffer.extend(chunk[:room])
                    _kill_tree(proc)
                    return bytes(buffer), True
                buffer.extend(chunk)

        try:
            # 上面两个参数都传了 `PIPE`，所以这两条管道必然不是 None——类型标注里的
            # `| None` 是「没传 PIPE 时才会是 None」，我们传了。这一句是给类型检查器的交代。
            assert proc.stdout is not None
            assert proc.stderr is not None
            # 两个管道**必须并发读**。顺序读会死锁：先读 stdout 读到 EOF 意味着等命令
            # 结束，而命令可能正卡在「stderr 写满了没人读」上。
            (
                (stdout_bytes, stdout_capped),
                (stderr_bytes, stderr_capped),
            ) = await asyncio.gather(slurp(proc.stdout), slurp(proc.stderr))
            await proc.wait()
        except asyncio.CancelledError:
            # 走到这儿有两类原因：Registry 那层 `wait_for` 超时取消了，或者用户退出。
            # **必须自己动手杀**——`wait_for` 取消的只是「等待」这个动作，子进程还在
            # 后台跑着，一条 `sleep 300` 会一直挂着不放。
            _kill_tree(proc)
            # 杀完还要**收尸**：等它真的退出，transport 才会把管道关掉。
            # 不收的话进程是死了，可 `BaseSubprocessTransport` 还挂在事件循环上，
            # 循环一关就在 GC 里冒一句 `RuntimeError: Event loop is closed`
            # （单测里实测到了）。收尸这件事本身就是「不许抛」的，所以包一层。
            #
            # 再套一层 `wait_for` 是防「收尸本身变成新的挂死点」：`wait()` 等的是
            # 进程退出**加上**所有管道 EOF，万一还有哪个进程逃出了进程组、攥着写端不放，
            # 这里就会跟从前一样永远等下去——那等于把刚修好的超时又还回去了。
            # 给 1 秒，收不干净就认了，反正 `_kill_tree` 已经把能杀的都杀了。
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), 1.0)
            raise

        stdout = stdout_bytes.decode(errors="replace")
        stderr = stderr_bytes.decode(errors="replace")
        # 读满上限时我们**发过 SIGKILL**，`returncode` 因此是 `-9`。这件事必须由我们
        # 说出来：不说的话，模型看到的只是一个自己没发过的信号退出码，只能瞎猜命令为什么崩，
        # 而真相是「它输出太多，被我们掐了」。它会去改命令的逻辑，而该改的是命令的**范围**。
        #
        # 位置很要紧：整体排在 `exit_code` 紧后面。`_truncate` 是**从尾部**切的
        # （`text[:max_chars]`），写在前面的东西无论输出多长都不会被截掉——这条说明
        # 恰恰是最需要在的时候在场的那种。
        if stdout_capped or stderr_capped:
            # 说清楚三件事：谁干的、为什么、接着怎么办。少了第三件，模型只知道
            # 「失败了」却不知道出路，多半会把同一条命令原样再跑一遍。
            cap_note = (
                f"[输出超限] 命令的输出超过了 {_MAX_PIPE_KB}KB 上限，"
                "已被 Qicode 主动终止。**上面那个退出码是我们发的 SIGKILL，"
                "不是命令自己崩溃。**"
                f"已保留前 {_MAX_PIPE_KB}KB，其余丢弃；"
                "要拿到完整结果，请把命令的范围缩小，例如加 `| head -100` 或先 `grep` 过滤。\n"
            )
        else:
            cap_note = ""
        body = f"exit_code: {proc.returncode}\n{cap_note}stdout:\n{stdout}\nstderr:\n{stderr}"
        # 非零退出算**错误**（`docs/v2/spec.md` F9 把「命令超时 / 非零退出」与
        # 「文件不存在」并列成执行失败）：模型自己挑的命令跑挂了，就该一眼看出「这条没成」，
        # 而不是从一大段输出里自己琢磨。细节一个不少地留着，它照样能判断原因。
        #
        # 被我们掐掉的也算错误，尽管它未必是「命令写错了」——它没跑完，我们不能假装
        # 拿到了一份完整的输出。`is_error` 说的是「这个结果不可全信」，正是这里的情况。
        return Result(
            _truncate(body, MAX_OUTPUT_LINES, MAX_OUTPUT_CHARS),
            is_error=proc.returncode != 0,
        )
