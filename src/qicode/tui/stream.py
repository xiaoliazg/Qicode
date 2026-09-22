"""流式消费与计时（F8、F12、N1）。

这一层**不 import textual**：它只认 `Provider` 的事件流和几个回调，界面的事全交给
调用方。这样切分的好处是——流式是本项目的核心路径，而它现在可以脱离终端、脱离
Textual 直接跑单测（一个假 provider 就够）。
"""

import asyncio
from collections.abc import Callable

from qicode.llm import Message, Provider

# 计时刷新频率（秒）。0.1s = 10fps：秒数看着是连续跳的，
# 「处理中」的转轮动画也够顺，又不会把每一帧都变成一次界面重排。
TICK_INTERVAL = 0.1


async def consume(
    provider: Provider,
    msgs: list[Message],
    *,
    on_text: Callable[[str], None],
    on_done: Callable[[], None],
    on_error: Callable[[Exception], None],
) -> None:
    """把一轮流式对话跑完，结果通过回调交出去。

    三个回调**保证恰有一个**会被调用（取消除外），调用方因此不需要自己兜底状态：

    - `on_text(增量)` —— 每收到一段正文调一次；调用方自己决定要不要累加。
      （这里不替调用方攒全文，界面想看「已经收到多少」就自己留着，两边不互相依赖。）
    - `on_done()`      —— 本轮正常结束。
    - `on_error(异常)` —— 本轮失败。**异常不会从本函数逃出去**（F11/AC11 要求
      失败不中断会话），所以调用方不必再包一层 try。

    取消（CancelledError）是唯一例外：它必须原样往上抛，见下面的注释。
    """
    try:
        async for event in provider.stream(msgs):
            if event.err is not None:
                on_error(event.err)
                return
            if event.text:
                on_text(event.text)
            if event.done:
                on_done()
                return
    except asyncio.CancelledError:
        # 用户按 Esc / Ctrl+C 时走这里。取消靠异常传播，**吞掉就等于把取消吞掉了**：
        # asyncio 会认为任务正常跑完，SDK 的流也不会被清理。
        raise
    except Exception as exc:  # noqa: BLE001
        # 适配器内部已经把 SDK 异常翻成了 err 事件，能到这里的是**我们没料到的**异常
        # （比如界面回调自己抛了）。同样翻成 on_error，不让它冒出去打崩 App。
        on_error(exc)
        return

    # 能走到这一行，说明流**迭代完了**却既没给 done 也没给 err。
    # 目前两个适配器都保证正常结束必 yield done=True，但那是它们代码里的承诺；
    # 这里兜一道，免得真出岔子时界面永远卡在「流式中」，用户连输入框都拿不回来。
    on_error(RuntimeError("流式响应意外结束：没收到结束信号，也没收到错误"))
