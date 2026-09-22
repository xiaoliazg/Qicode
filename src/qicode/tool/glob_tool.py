"""`glob`：按模式找文件（F2-找）。"""

import asyncio
import os
from pathlib import Path
from typing import Any

from qicode.tool import Result, _parse_args, _require_str

#: 最多列出多少个匹配。100 条路径已经足够模型看清一个目录的构成，
#: 再多就只是在烧上下文（N5）。
MAX_MATCHES = 100


class GlobTool:
    """按 glob 模式列文件路径。"""

    def name(self) -> str:
        return "glob"

    def description(self) -> str:
        return (
            "按 glob 模式查找文件路径，例如 `**/*.py`（递归找所有 Python 文件）、"
            "`docs/*.md`。返回相对搜索根目录的路径列表，按字母序排列。"
            "适合先摸清项目里有哪些文件，再决定要读哪一个。"
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "glob 模式，如 `**/*.py`、`src/**/*.py`、`*.toml`",
                },
                "path": {
                    "type": "string",
                    "description": "搜索的根目录，默认当前工作目录",
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

        root = Path(data.get("path") or ".")
        if not root.is_dir():
            return Result(f"路径不是目录: {root}", is_error=True)

        found: list[str] = []
        try:
            for index, entry in enumerate(root.glob(pattern)):
                # 每 100 个让出一次 event loop：跨整个仓库的 `**` 可能要遍历几万条目录项，
                # 一直攥着不放会把界面冻住（N2）。
                if index % 100 == 0:
                    await asyncio.sleep(0)
                if entry.is_file():
                    # 相对根目录给路径，模型看到的是一份能直接接着用的清单
                    # （也是它随后要传给 read_file 的形式）。
                    found.append(os.path.relpath(entry, root))
        except (OSError, ValueError, NotImplementedError) as exc:
            # ValueError：模式里有非法字符（如 `\0`）。NotImplementedError：
            # 传了绝对路径模式（Python 3.12 的 `Path.glob` 还不接受）。
            return Result(f"查找失败: {exc}", is_error=True)

        if not found:
            # **无匹配不是错误**：`docs/v2/spec.md` F9 明确把「搜索无结果」从失败里
            # 摘了出去。把它标成错误，模型会以为工具坏了，然后反复重试同一个模式。
            return Result(f"无匹配：没有文件符合模式 `{pattern}`")

        found.sort()
        body = "\n".join(found[:MAX_MATCHES])
        if len(found) > MAX_MATCHES:
            body += f"\n…（共 {len(found)} 个匹配，只列出前 {MAX_MATCHES} 个）"
        return Result(body)
