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
            #
            # 读的是**字节**而不是 `read_text`，这一点是必须的：`read_text` 的两个默认值
            # 都会悄悄改动内容——`errors="replace"` 把非 UTF-8 的字节换成 U+FFFD（原文
            # 永久丢失），universal newlines 把 `\r\n` 折叠成 `\n`。而我们只打算改一小段，
            # 其余部分应当**一个字节都不动**。
            raw = await asyncio.to_thread(target.read_bytes)
        except FileNotFoundError:
            return Result(f"文件不存在: {path}", is_error=True)
        except OSError as exc:
            return Result(f"读取失败: {path}: {exc}", is_error=True)

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            # 不猜编码、不做替换：猜错一次就是静默改坏一个文件，而返回的「已修改」三个字
            # 会让模型完全察觉不到。宁可拒绝，让用户自己拿 bash 去处理。
            return Result(
                f"{path} 不是 UTF-8 文本（{exc}），本工具不修改它。"
                "请先用 bash 确认它的真实编码。",
                is_error=True,
            )

        # 先按原样匹配一次；不中，再试一次把 `\n` 换成 `\r\n`。
        #
        # 为什么需要这第二次：模型手里的 `old_string` 是从 read_file 抄来的，而 read_file
        # 走 universal newlines，给模型看的行尾**一律是 `\n`**；可 CRLF 文件里真正的字节
        # 是 `\r\n`。少了这一步，模型在 CRLF 项目里永远匹配不上跨行的 `old_string`，
        # 而它收到的错误提示（「必须逐字符一致」）会把它引到完全错误的方向上去。
        insert = old
        count = text.count(insert)
        if count == 0:
            crlf = old.replace("\n", "\r\n")
            if crlf != old and text.count(crlf):
                insert, count = crlf, text.count(crlf)

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

        # `new_string` 的行尾要跟**匹配到的原文**对齐。不对齐的话，往 CRLF 文件里写一段
        # LF 换行的新文本，改完就成了混合行尾——那同样算「碰了文件的其他部分」。
        replacement = new.replace("\n", "\r\n") if "\r\n" in insert else new
        updated = text.replace(insert, replacement, 1).encode("utf-8")
        try:
            await asyncio.to_thread(target.write_bytes, updated)
        except OSError as exc:
            return Result(f"写入失败: {path}: {exc}", is_error=True)

        return Result(f"已修改 {path}")
