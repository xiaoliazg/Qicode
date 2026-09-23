"""`write_file`：写文件，父目录自动创建（F2-写）。"""

import asyncio
from pathlib import Path
from typing import Any

from qicode.tool import Result, _parse_args, _require_str, _require_text


def _write(target: Path, content: str) -> None:
    """建好父目录再写文件。

    同步函数，由调用方丢进工作线程跑（理由同 `read_file._read_head`）：
    路径是模型给的，可能落在网络盘上，`mkdir` / `write_text` 都可能阻塞，
    而就地阻塞会冻住整个界面（N2）。
    """
    # `parents=True` 连同中间层一起建；`exist_ok=True` 让「父目录已经有了」不算错。
    # 裸文件名（如 `a.txt`）的 parent 是 `.`，建它也是无害的空操作。
    target.parent.mkdir(parents=True, exist_ok=True)
    # `encoding` 写死 UTF-8（理由同 `read_file._read_head`）：不写就跟随 locale，
    # 在 `LC_ALL=C` 下写中文会直接抛 `'ascii' codec can't encode`。
    #
    # `newline=""` 是「原样写，不做换行转换」。默认的 `newline=None` 会把 `\n` 翻译成
    # `os.linesep`——在 macOS / Linux 上恰好就是 `\n`，看着毫无问题，可到了 Windows 上
    # 会变成 `\r\n`，那就不是模型说的东西了。
    target.write_text(content, encoding="utf-8", newline="")


class WriteFileTool:
    """把内容写入文件（覆盖），父目录不存在就建。"""

    def name(self) -> str:
        return "write_file"

    def description(self) -> str:
        return (
            "把内容写入指定路径的文件，**已有内容会被整个覆盖**。父目录不存在时自动创建。"
            "只想改动文件里的一小段，请用 edit_file——它不会碰文件的其他部分。"
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要写入的文件路径（相对当前工作目录，或绝对路径）",
                },
                "content": {
                    "type": "string",
                    "description": "要写入的完整内容。允许为空字符串（相当于清空该文件）。",
                },
            },
            "required": ["path", "content"],
        }

    async def execute(self, args: str) -> Result:
        data, err = _parse_args(args)
        if err:
            return Result(err, is_error=True)
        path, err = _require_str(data, "path")
        if err:
            return Result(err, is_error=True)
        # 用 `_require_text` 而不是 `_require_str`：空内容是合法输入（清空一个文件）。
        content, err = _require_text(data, "content")
        if err:
            return Result(err, is_error=True)

        target = Path(path)
        try:
            await asyncio.to_thread(_write, target, content)
        except OSError as exc:
            return Result(f"写入失败: {path}: {exc}", is_error=True)

        # 字节数按 UTF-8 算——落盘用的就是它，报出来的数字和磁盘上的必须是一回事。
        return Result(f"已写入 {path}（{len(content.encode('utf-8'))} 字节）")
