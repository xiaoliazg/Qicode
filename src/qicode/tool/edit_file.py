"""`edit_file`：唯一匹配替换（F2-改）。"""

import asyncio
from pathlib import Path
from typing import Any

from qicode.tool import Result, _parse_args, _require_str, _require_text


class EditFileTool:
    """把文件里一段唯一的原文换成新文。"""

    def name(self) -> str:
        return "edit_file"

    def description(self) -> str:
        return (
            "把文件里的一段文本替换成另一段。`old_string` 必须在文件中**恰好出现一次**，"
            "否则不会做任何修改并返回错误——这是为了保护文件不被误改。"
            "如果不唯一，请在 `old_string` 里多带几行上下文让它唯一。"
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要修改的文件路径（相对当前工作目录，或绝对路径）",
                },
                "old_string": {
                    "type": "string",
                    "description": "要被替换掉的原文，必须与文件内容逐字符一致，且在文件中唯一",
                },
                "new_string": {
                    "type": "string",
                    "description": "替换成的新文本。允许为空字符串（相当于删除这一段）。",
                },
            },
            "required": ["path", "old_string", "new_string"],
        }

    async def execute(self, args: str) -> Result:
        data, err = _parse_args(args)
        if err:
            return Result(err, is_error=True)
        path, err = _require_str(data, "path")
        if err:
            return Result(err, is_error=True)
        # 空的 `old_string` 用不得：`str.count("")` 返回的是「长度 + 1」，会把一次
        # 明显写错的调用算成「匹配到 N 处」，报出一句莫名其妙的错误。这里直接挡掉。
        old, err = _require_str(data, "old_string")
        if err:
            return Result(err, is_error=True)
        new, err = _require_text(data, "new_string")
        if err:
            return Result(err, is_error=True)

        target = Path(path)
        try:
            # 读写都丢进工作线程（理由同 `read_file._read_head`）：路径由模型给出，
            # 可能落在网络盘上。注意这里读的是**整个文件**，没有上限——编辑本来
            # 就得看全文，靠不了 read_file 那个 256KB 的护栏，所以更不能就地阻塞。
            content = await asyncio.to_thread(target.read_text, errors="replace")
        except FileNotFoundError:
            return Result(f"文件不存在: {path}", is_error=True)
        except OSError as exc:
            return Result(f"读取失败: {path}: {exc}", is_error=True)

        count = content.count(old)
        # 0 处和 >1 处是**两种不同的问题**，文案必须能区分开（AC4）：前者是「你给我的
        # 原文跟文件对不上」，后者是「你给的原文不够具体」。模型据此做的调整完全不同——
        # 一个是重新读文件，一个是多带几行上下文。
        if count == 0:
            return Result(
                "未找到匹配的内容。`old_string` 必须与文件内容逐字符一致"
                "（包括缩进和换行），请先 read_file 确认原文。",
                is_error=True,
            )
        if count > 1:
            return Result(
                f"匹配到 {count} 处，`old_string` 不唯一，无法确定要改哪一处。"
                "请在 `old_string` 里多带几行上下文，使它只出现一次。",
                is_error=True,
            )

        try:
            await asyncio.to_thread(target.write_text, content.replace(old, new, 1))
        except OSError as exc:
            return Result(f"写入失败: {path}: {exc}", is_error=True)

        return Result(f"已修改 {path}")
