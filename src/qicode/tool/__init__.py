"""工具抽象、注册中心与执行入口（`docs/v2/spec.md` F1）。

这一层**不 import 任何 LLM SDK**，也不认识 anthropic / openai 的区别：它只知道
「有个叫 read_file 的东西，给它一段 JSON，它回一段文本」。协议差异全部留在 `qicode.llm`。

一条贯穿全包的不变量：**`execute` 永远返回 `Result`，从不抛异常给上层**（F9/N4）。
文件不存在、命令超时、参数不是合法 JSON——全都是「结果」，不是「故障」。上层的 agent
因此不需要为每个工具写一套 try/except，模型也总能拿到一段能读懂的话去自我调整。
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from qicode.llm import ToolDefinition

#: 单个工具执行的默认超时秒数（N1）。**不可配**——spec「不做的事」里明确排除了
#: 「超时时长配置化」。30 秒是「够跑完一条 grep / 一次 git status，又不至于让人干等」的量级。
DEFAULT_TIMEOUT: float = 30.0


@dataclass
class Result:
    """工具执行结果。

    `content` 是**回灌给模型看的文本**，不是给人看的漂亮输出：带行号、带截断标注、
    出错时说清楚哪儿错了。模型只有这段文字可看，它写得越具体，模型越可能一次改对。
    """

    content: str
    is_error: bool = False
    """True 表示这是一条结构化错误。UI 据此染色，适配器据此设 `is_error`。"""


@runtime_checkable
class Tool(Protocol):
    """统一工具抽象（F1）。

    用 Protocol 而不是抽象基类，和 `qicode.llm.Provider` 同一个理由：测试里想造个假工具
    不必先学会继承体系。`@runtime_checkable` 是为了 `isinstance(obj, Tool)` 能用来做
    `Registry.register` 的入参校验。
    """

    def name(self) -> str:
        """模型看到的工具名，如 `"read_file"`。"""
        ...

    def description(self) -> str:
        """给模型看的用途说明——**它是模型决定调不调这个工具的唯一依据**，比实现重要。"""
        ...

    def parameters(self) -> dict[str, Any]:
        """入参的 JSON Schema（`type` / `properties` / `required`），手写。"""
        ...

    async def execute(self, args: str) -> Result:
        """执行。`args` 是**原始 JSON 字符串**；超时由外面的 `asyncio.wait_for` 管。"""
        ...


class Registry:
    """集中登记、按名查找、导出定义、按名执行（F1/F3/F5）。"""

    def __init__(self) -> None:
        # 用 list 记注册顺序：`definitions()` 的导出顺序直接决定模型看到的工具排列，
        # 顺序一变，同一句话在不同版本里可能调用不同的工具——保持稳定是有意义的。
        self._order: list[str] = []
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """登记一个工具。

        重名**抛 `ValueError`**，不静默覆盖：六个工具在启动时一把注册，重名只可能是
        写错了名字，那时候炸掉比让某一个工具悄悄消失强得多。
        """
        name = tool.name()
        if name in self._tools:
            raise ValueError(f"工具名重复: {name}")
        self._order.append(name)
        self._tools[name] = tool

    def get(self, name: str) -> Tool | None:
        """按名查找；没有就返回 `None`（不抛——「未知工具」是要回灌给模型的正常情况）。"""
        return self._tools.get(name)

    def definitions(self) -> list[ToolDefinition]:
        """导出全部工具定义，供请求时随对话一起发给模型（F3/AC1）。"""
        return [
            ToolDefinition(
                name=name,
                description=self._tools[name].description(),
                input_schema=self._tools[name].parameters(),
            )
            for name in self._order
        ]

    async def execute(self, name: str, args: str, timeout: float) -> Result:
        """按名执行一个工具，把一切意外都收成 `Result`（F5/F9/N4）。

        三层兜底，缺一不可：

        1. 工具名不存在 → 直接回一条错误结果。模型偶尔会自己编一个工具名出来。
        2. 超时 → `asyncio.wait_for` 抛 `TimeoutError`。注意它同时**取消**了内部协程，
           所以工具那边要自己收尾（`bash` 就是靠这个取消信号去杀子进程的）。
        3. 其它任何异常 → 收成错误结果。工具本该自己返回 `Result`，这里是「万一」。
           `CancelledError` 不会被这一条接住（它是 `BaseException`），
           用户退出时的取消照常向上传播。
        """
        tool = self.get(name)
        if tool is None:
            return Result(f"未知工具: {name}", is_error=True)

        try:
            return await asyncio.wait_for(tool.execute(args), timeout)
        except TimeoutError:
            return Result(f"工具 {name} 执行超时（{timeout:g} 秒）", is_error=True)
        except Exception as exc:  # noqa: BLE001 —— 这里要的就是「什么都接住」
            return Result(f"工具 {name} 执行出错: {exc}", is_error=True)


def _truncate(text: str, max_lines: int, max_chars: int) -> str:
    """把过长的结果截断，并在尾部留下 `[truncated]` 标记（N5/AC13）。

    标记不是装饰：模型看到一段被切掉一半的代码却不知道被切了，会当成「文件就这么长」，
    然后基于这个错误前提继续推理。宁可让它知道自己看到的是残缺的。
    """
    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True
    lines = text.splitlines()
    if len(lines) > max_lines:
        text = "\n".join(lines[:max_lines])
        truncated = True
    return f"{text}\n[truncated]" if truncated else text


def _parse_args(args: str) -> tuple[dict[str, Any], str]:
    """解析工具入参，返回 `(数据, 错误说明)`——两者恰好有一个是「有值」的。

    空字符串按 `{}` 处理：OpenAI 侧无参工具的 `arguments` 会是空串而不是 `"{}"`
    （`docs/v2/plan.md`「空参数归一」），不归一的话每个无参工具都会误报一次参数错误。
    """
    text = args.strip() or "{}"
    try:
        data = json.loads(text)
    except ValueError as exc:
        return {}, f"参数不是合法的 JSON: {exc}"
    if not isinstance(data, dict):
        return {}, f"参数必须是一个 JSON 对象，收到的是 {type(data).__name__}"
    return data, ""


def _require_str(data: dict[str, Any], key: str) -> tuple[str, str]:
    """取一个**必填的非空字符串**参数，返回 `(值, 错误说明)`。"""
    value = data.get(key)
    if not isinstance(value, str) or not value:
        return "", f"缺少必填参数 {key}（它必须是非空字符串）"
    return value, ""


def _require_text(data: dict[str, Any], key: str) -> tuple[str, str]:
    """同 `_require_str`，但**允许空串**。

    写文件的内容可以是空的（清空一个文件），改文件的 `new_string` 也可以是空的
    （删掉一段）。这两处如果用 `_require_str`，用户就永远删不干净。
    """
    value = data.get(key)
    if not isinstance(value, str):
        return "", f"缺少必填参数 {key}（它必须是字符串）"
    return value, ""


def new_default_registry() -> Registry:
    """构造注册中心并登记六个核心工具（F2）。

    各工具用**函数内 import**，和 `qicode.llm.new_provider` 同一个理由：工具模块要
    `from qicode.tool import Result, _truncate`，写在文件顶部就成环了
    （`__init__` → `read_file` → `__init__`）。放在函数里，等包初始化完再进来。
    """
    from qicode.tool.bash import BashTool
    from qicode.tool.edit_file import EditFileTool
    from qicode.tool.glob_tool import GlobTool
    from qicode.tool.grep_tool import GrepTool
    from qicode.tool.read_file import ReadFileTool
    from qicode.tool.write_file import WriteFileTool

    registry = Registry()
    for tool in (
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        BashTool(),
        GlobTool(),
        GrepTool(),
    ):
        registry.register(tool)
    return registry
