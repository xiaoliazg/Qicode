"""`tui.stream.consume` 的单测（T10、F11）。

这段是「流式」这条核心路径的驱动器，但它不 import textual——所以这里可以用
假 provider 直接驱动它，不需要起终端、不需要 App。
"""

import asyncio
from collections.abc import AsyncIterator

from qicode.llm import Message, Provider, StreamEvent
from qicode.tui.stream import consume


class Recorder:
    """把 consume 的三个回调记下来，供断言。"""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.done_count = 0
        self.errors: list[Exception] = []

    def on_text(self, text: str) -> None:
        self.texts.append(text)

    def on_done(self) -> None:
        self.done_count += 1

    def on_error(self, err: Exception) -> None:
        self.errors.append(err)

    @property
    def joined(self) -> str:
        return "".join(self.texts)


def run_consume(provider: Provider) -> Recorder:
    """跑一遍 consume，返回记录器。"""
    recorder = Recorder()
    asyncio.run(
        consume(
            provider,
            [],
            on_text=recorder.on_text,
            on_done=recorder.on_done,
            on_error=recorder.on_error,
        )
    )
    return recorder


class Exploding:
    """一迭代就炸的 provider，用来验「异常不逃出 consume」。"""

    @property
    def name(self) -> str:
        return "boom"

    @property
    def model(self) -> str:
        return "boom-1"

    async def stream(self, msgs: list[Message]) -> AsyncIterator[StreamEvent]:
        raise ValueError("连接被重置")
        yield  # pragma: no cover  —— 只为让它是个 async generator


def test_normal_stream_calls_text_then_done(make_provider) -> None:
    provider = make_provider(
        [
            StreamEvent(text="你"),
            StreamEvent(text="好"),
            StreamEvent(done=True),
        ]
    )

    rec = run_consume(provider)

    assert rec.joined == "你好"
    assert rec.done_count == 1
    assert rec.errors == []


def test_error_event_goes_to_on_error(make_provider) -> None:
    """F11：适配器翻出来的 err 事件走 on_error，不往外抛。"""
    boom = RuntimeError("鉴权失败")
    provider = make_provider([StreamEvent(text="半"), StreamEvent(err=boom)])

    rec = run_consume(provider)

    assert rec.errors == [boom]
    assert rec.done_count == 0


def test_exception_from_provider_is_caught() -> None:
    """provider 自己抛异常也走 on_error——本函数保证异常不逃出去（F11/AC11）。"""
    rec = run_consume(Exploding())

    assert len(rec.errors) == 1
    assert isinstance(rec.errors[0], ValueError)
    assert rec.done_count == 0


def test_stream_ending_without_signal_reports_error(make_provider) -> None:
    """流自然结束却没给 done/err —— 必须报错。

    否则界面会永远卡在「流式中」：输入框拿不回来，用户也没法再来一轮。
    """
    provider = make_provider([StreamEvent(text="半截")])

    rec = run_consume(provider)

    assert rec.joined == "半截"
    assert rec.done_count == 0
    assert len(rec.errors) == 1
    assert "意外结束" in str(rec.errors[0])


def test_cancellation_propagates(make_provider) -> None:
    """取消必须**原样抛出去**，不能被翻成 on_error。

    吞掉的话 asyncio 会以为任务正常跑完了，取消语义就断了——而取消正是
    用户按 Ctrl+C 退出时关闭 HTTP 流的唯一途径。
    """
    provider = make_provider(
        [StreamEvent(text="一"), StreamEvent(done=True)], delay=5.0
    )
    rec = Recorder()
    cancelled = False

    async def scenario() -> None:
        nonlocal cancelled
        task = asyncio.create_task(
            consume(
                provider,
                [],
                on_text=rec.on_text,
                on_done=rec.on_done,
                on_error=rec.on_error,
            )
        )
        await asyncio.sleep(0.02)  # 让它进到第一个 await 点上
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            cancelled = True

    asyncio.run(scenario())

    assert cancelled is True  # 取消确实传到了调用方
    assert rec.errors == []  # 而且没有被伪装成一次普通失败
    assert rec.done_count == 0
    assert rec.texts == []  # 取消得早，一个增量都还没送出去
