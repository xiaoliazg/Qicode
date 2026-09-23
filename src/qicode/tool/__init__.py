"""工具抽象、注册中心与执行入口（`docs/v2/spec.md` F1）。

这一层**不 import 任何 LLM SDK**，也不认识 anthropic / openai 的区别：它只知道
「有个叫 read_file 的东西，给它一段 JSON，它回一段文本」。协议差异全部留在 `qicode.llm`。

一条贯穿全包的不变量：**`execute` 永远返回 `Result`，从不抛异常给上层**（F9/N4）。
文件不存在、命令超时、参数不是合法 JSON——全都是「结果」，不是「故障」。上层的 agent
因此不需要为每个工具写一套 try/except，模型也总能拿到一段能读懂的话去自我调整。
"""

import asyncio
import json
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
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


async def _run_blocking[T](fn: Callable[..., T], /, *args: Any) -> T:
    """把同步的阻塞调用丢进**守护线程**执行，结果回填给事件循环。

    **为什么不用 `asyncio.to_thread`**：它跑在默认线程池里，而 `asyncio.run()` 收尾时会
    调 `loop.shutdown_default_executor()`——那个方法会把池里**每一个**线程 join 掉。
    于是一个永远不会结束的阻塞调用（读一个没写端的 FIFO、读挂死的网络盘）就能把
    **整个进程**扣住，连界面都关不掉。实测过的形状：工具在 2 秒时如实报了超时、
    `main()` 也返回了，进程却从此不动，只能 `kill -9`。

    这不是「超时没生效」，而是超时**只管住了 await，管不住那个线程**。守护线程换来的
    是：`threading._shutdown()` 会跳过它，`asyncio.run()` 也不等它——**进程想退就退**。

    代价说在明处：被放弃的线程**会泄漏一个**，它继续阻塞，只是没人等它的结果了。
    这是刻意的取舍——「漏一个线程」比「进程关不掉」轻得多。各工具在调用前还会先做
    廉价的前置检查（如 `read_file` 拒收非普通文件），把最常见的成因掐在进入线程之前。

    回填时先查 `fut.done()`：超时那条路上 `wait_for` 已经把 Future 取消了，迟到几秒
    的结果直接丢掉即可——不查的话 `set_result` 会抛 `InvalidStateError`。
    事件循环已经关掉时 `call_soon_threadsafe` 抛 `RuntimeError`，同样吞掉——那一刻
    进程正在退出，这条结果本来也没人要了。
    """
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[T] = loop.create_future()

    def work() -> None:
        # 连 `BaseException` 一起接住：漏掉任何一种，这个 Future 就永远不 resolve，
        # 那边 `await` 的调用方会跟着一起挂死——比原样抛出更难查。
        try:
            payload: tuple[bool, Any] = (True, fn(*args))
        except BaseException as exc:  # noqa: BLE001 —— 见上
            payload = (False, exc)

        def deliver() -> None:
            if fut.done():
                return
            ok, value = payload
            if ok:
                fut.set_result(value)
            else:
                fut.set_exception(value)

        try:
            loop.call_soon_threadsafe(deliver)
        except RuntimeError:
            # 事件循环已关闭（进程正在收尾）。没有等待者了，丢掉。
            pass

    threading.Thread(
        target=work,
        name=f"qicode-blocking:{getattr(fn, '__name__', 'callable')}",
        daemon=True,
    ).start()
    return await fut


class _NotRegularFile(Exception):
    """路径存在，但不是普通文件——管道、设备、套接字。

    单独一个异常类型而不是复用 `OSError`：这几种路径 `open()` 未必报错（FIFO 会**阻塞**，
    字符设备会乖乖打开然后吐数据），所以它不是「打开失败」，而是「我们压根不打算打开」。
    `reason` 是给模型看的那半句话。
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _special_reason(mode: int) -> str | None:
    """inode 是管道 / 设备 / 套接字就说一句人话，否则回 `None`。

    **目录故意回 `None`**：它对三个工具的含义各不相同（读它是「不是文件」、写它是
    「不能往目录里写」），文案得各写各的，所以这里不抢那句话——放行让 `open()` /
    `write_text()` 抛它们原本那个 `IsADirectoryError`，各工具有各自的接法。

    `S_ISSOCK` 也值一提：套接字文件上用 `open()` 本来就抛 `ENXIO`，但那时只能说一句
    「读取失败」，不如这里直接点破它是什么。
    """
    if stat.S_ISFIFO(mode):
        return "是一个命名管道（FIFO）"
    if stat.S_ISSOCK(mode):
        return "是一个 Unix 套接字"
    if stat.S_ISCHR(mode):
        return "是一个字符设备"
    if stat.S_ISBLK(mode):
        return "是一个块设备"
    return None


def _refuse_if_special(target: Path) -> None:
    """目标的 inode 若是管道 / 设备 / 套接字，抛 `_NotRegularFile`；其余一律放行。

    三个文件工具在读/写**之前**都先过这一道。拦在门口而不是放行让 IO 去碰运气，
    是因为这几种路径的失败方式都不体面：没有写端的 FIFO 上 `open()` **永久阻塞**
    （实测：工具如实报了超时，进程却再也退不出去，只能 `kill -9`）；`/dev/zero` 会
    一直吐零；`/dev/random` 收不住。而「读一个文件的内容」这件事，在管道和设备上
    根本没有定义——读出来是什么取决于另一端有没有人在写。

    **这一步必须在线程里做**：`stat()` 自己碰上网盘挂死一样会阻塞，放事件循环上等于
    把刚绕开的坑换个地方挖。三个调用方都是在各自的同步函数里调它的。

    **不存在不算问题**，直接放过：三个调用方对「文件不存在」各有各的说法——写是
    「待创建」，读和改是「文件不存在」——都轮不到这里插嘴，交给它们原本那条路径去报。
    stat 的其它失败（权限不够、路径中段不是目录）同理。
    """
    try:
        mode = target.stat().st_mode
    except OSError:
        return
    reason = _special_reason(mode)
    if reason is not None:
        raise _NotRegularFile(reason)


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
