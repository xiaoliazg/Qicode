"""`QicodeApp` 的集成测试（T9–T12）。

用 Textual 官方的 `run_test()` 驱动：它在无头模式下**真跑一个 App**——真正的
compose、真正的消息分发、真正的键盘事件。所以这些用例验的是**界面行为**，
而不是「某个函数返回了什么」。

`screen_text()` 把合成器里的真实字符网格取出来，于是「这段文字到底有没有出现
在界面上」可以被断言，而不是靠肉眼。
"""

import asyncio
from collections.abc import Awaitable, Callable

import pytest
from textual.containers import VerticalScroll
from textual.pilot import Pilot

from qicode.config import ProviderConfig
from qicode.llm import StreamEvent
from qicode.tui.app import QicodeApp, SessionState
from qicode.tui.view import MARKER, PromptArea, ReplyBlock

Scenario = Callable[[QicodeApp, Pilot], Awaitable[None]]


def with_app(providers: list[ProviderConfig], scenario: Scenario) -> None:
    """起一个 App 跑一遍 scenario，跑完自动收摊。"""

    async def main() -> None:
        app = QicodeApp(providers)
        async with app.run_test(size=(80, 24)) as pilot:
            await scenario(app, pilot)

    asyncio.run(main())


def screen_text(app: QicodeApp) -> str:
    """当前整屏的文字，供断言。

    不用 `app.export_screenshot()`：那个出的是 SVG，空格被写成 `&#160;` 只是
    最表层的问题，真正麻烦的是 Rich **按样式把一行切成好几段 `<text>`**——
    占位符 `Send a message...` 在 SVG 里是 `S` 和 `end a message...` 两个
    独立元素，夹着一堆标签，怎么写子串搜索都搜不着。

    `render_strips()` 出的是合成器里的真实字符网格（每行一个 `Strip`），
    `Strip.text` 直接给出该行的纯文本，按顺序拼起来就是屏幕上**一字不差**的内容。
    """
    # `_compositor` 是内部属性：Textual 只公开了 SVG 导出，没有纯文本导出。
    strips = app.screen._compositor.render_strips()
    return "\n".join(strip.text for strip in strips)


def screen_lines(app: QicodeApp) -> list[str]:
    """整屏按行切开，用来断言「谁和谁落在同一行」。"""
    return screen_text(app).split("\n")


async def finish_turn(app: QicodeApp, pilot: Pilot) -> None:
    """等本轮流式跑完。

    直接 await 那个 task：它是 `submit()` 同步创建出来的，所以此刻一定拿得到；
    等它返回时，收尾逻辑（换回 IDLE、清动态区）也已经执行完了。
    """
    # 直接读这个私有属性是故意的：测试需要**精确等待**，而不是轮询猜测。
    task = app._stream_task
    assert task is not None, "submit() 之后应当已有流式 task"
    await task
    await pilot.pause()


async def type_and_submit(pilot: Pilot, text: str) -> None:
    """敲进去再回车。"""
    pilot.app.query_one("#input", PromptArea).insert(text)
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


# ────────────────────────── 启动与布局（F7、F2）──────────────────────────


def test_single_provider_starts_idle(make_config) -> None:
    """F2/AC1：只有一份配置时直接进入对话。"""

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        assert app.state is SessionState.IDLE
        assert app.provider is not None
        # 选择列表建好了但藏着。
        assert app.query_one("#select").display is False
        # 输入框可见，且已经拿到焦点（不用先点一下才能打字）。
        assert app.query_one("#input-row").display is True
        assert isinstance(app.focused, PromptArea)

    with_app([make_config()], scenario)


def test_multiple_providers_start_in_selecting(make_config) -> None:
    """F2/AC2：多份配置时先出选择列表，其余区域收起。"""

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        assert app.state is SessionState.SELECTING
        assert app.provider is None
        assert app.query_one("#select").display is True
        assert app.query_one("#input-row").display is False
        assert app.query_one("#log").display is False

    with_app([make_config(name="a"), make_config(name="b")], scenario)


def test_picking_the_second_provider_enters_conversation(make_config) -> None:
    """AC2：方向键选到第二条，状态栏跟着变成它的 name / model。"""

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        await pilot.press("down")  # 从第一条移到第二条
        await pilot.press("enter")
        await pilot.pause()

        assert app.state is SessionState.IDLE
        assert app.provider is not None
        assert app.provider.name == "second"
        assert app.provider.model == "model-second"
        assert app.query_one("#select").display is False

        screen = screen_text(app)
        assert "second" in screen
        assert "model-second" in screen

    with_app(
        [
            make_config(name="first", model="model-first"),
            make_config(name="second", model="model-second"),
        ],
        scenario,
    )


def test_banner_and_statusbar_are_on_screen(make_config) -> None:
    """AC7：banner（版本 + cwd）+ 提示行 + ❯ + 占位符 + 状态栏。"""

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        screen = screen_text(app)

        assert "Qicode" in screen
        assert "v0.1.0" in screen  # 版本号
        assert "/" in screen  # cwd 至少带个路径分隔符
        assert "/exit" in screen  # 就绪提示行
        assert "Send a message" in screen  # 占位符
        assert "anthropic" in screen  # 状态栏左侧 = provider 名
        assert "claude-sonnet-5" in screen  # 状态栏右侧 = 模型名

    with_app([make_config(name="anthropic", model="claude-sonnet-5")], scenario)


# ────────────────────────── 输入与提交（F9）──────────────────────────


def test_enter_submits_and_clears_input(make_config, make_provider) -> None:
    """AC9：Enter 提交，提交后输入框清空。"""

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        app.provider = make_provider([StreamEvent(text="嗨"), StreamEvent(done=True)])
        await type_and_submit(pilot, "你好")

        area = app.query_one("#input", PromptArea)
        assert area.text == ""
        # 只看第一条：这一轮可能已经跑完（假 provider 没有延迟），
        # 后面会多一条 assistant，那是另一条用例的事。
        assert (app.conv.messages()[0].role, app.conv.messages()[0].content) == (
            "user",
            "你好",
        )
        assert "你好" in screen_text(app)

    with_app([make_config()], scenario)


@pytest.mark.parametrize("key", ["alt+enter", "shift+enter", "ctrl+j"])
def test_newline_keys_insert_a_newline_instead_of_submitting(
    make_config, key: str
) -> None:
    """AC9：三个换行键都换行，不提交。

    为什么要三个：AC9 写的 Alt+Enter 在多数终端上发出来的是 `ESC CR`，被 Textual
    归并成了普通 Enter（见 `view.PromptArea` 的类文档），按下去会把消息发出去；
    `ctrl+j` 是 LF，各终端一致，是那个保底可用的。三个都得接住。
    """

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        area = app.query_one("#input", PromptArea)
        area.insert("第一行")
        await pilot.press(key)
        area.insert("第二行")
        await pilot.pause()

        assert area.text == "第一行\n第二行"
        # 关键：这次换行**没有**被当成提交。
        assert app.conv.messages() == []
        assert app.state is SessionState.IDLE

    with_app([make_config()], scenario)


def test_blank_input_is_ignored(make_config) -> None:
    """空输入不发出去（发出去只会换回一个 400）。"""

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        await type_and_submit(pilot, "   ")

        assert app.conv.messages() == []
        assert app.state is SessionState.IDLE

    with_app([make_config()], scenario)


def test_second_submit_is_ignored_while_streaming(make_config, make_provider) -> None:
    """F9：流式期间不接受新的提交。"""

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        app.provider = make_provider([StreamEvent(text="好"), StreamEvent(done=True)])

        app.submit("第一条")
        app.submit("第二条")  # 此刻状态是 STREAMING，应当被挡掉

        assert [m.content for m in app.conv.messages()] == ["第一条"]

        await finish_turn(app, pilot)

    with_app([make_config()], scenario)


# ────────────────────────── 流式与定型（F8、F12）──────────────────────────


def test_streaming_shows_text_then_settles_into_markdown(make_config, make_provider):
    """F8/F12：流式逐字 → done 后整段进对话区，并干净收尾。"""

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        app.provider = make_provider(
            [
                StreamEvent(text="# 标题\n"),
                StreamEvent(text="- 甲\n"),
                StreamEvent(text="- 乙\n"),
                StreamEvent(done=True),
            ]
        )

        app.submit("给我一个列表")
        await finish_turn(app, pilot)

        # 完整回复入了历史（F6）——第二轮请求要带上它。
        assert [(m.role, m.content) for m in app.conv.messages()] == [
            ("user", "给我一个列表"),
            ("assistant", "# 标题\n- 甲\n- 乙\n"),
        ]

        # 收尾干净：动态区清空、回 IDLE、流式 task 归位。
        assert app.state is SessionState.IDLE
        assert app.cur_reply == ""
        assert app._stream_task is None

        screen = screen_text(app)
        assert "标题" in screen
        assert "甲" in screen

    with_app([make_config()], scenario)


def test_streaming_area_shows_timer_before_first_token(make_config, make_provider):
    """F12/N2：还没拿到增量时，也要看得见「正在等待 + 秒数」。"""

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        app.provider = make_provider(
            [StreamEvent(text="慢"), StreamEvent(done=True)], delay=0.3
        )

        app.submit("在吗")
        # 这里**故意不等**流结束：要看的就是「首个增量到达前」那一瞬间。
        await pilot.pause(0.05)

        assert "Imagining" in screen_text(app)
        assert app.state is SessionState.STREAMING

        await finish_turn(app, pilot)

    with_app([make_config()], scenario)


def test_conversation_starts_at_the_top_not_glued_to_the_bottom(make_config) -> None:
    """内容不满一屏时，对话从**顶部**开始排，不贴底。

    Textual 的锚定是无条件贴底的：合成器每帧算 `内容底部 - 容器高` 直接写进
    `scroll_y`（绕过 clamp），内容比视口矮时这个值是负数，整块内容被推到屏幕
    下半截。后果一是启动时 banner 悬在半空、上面一片空白，二是最后一块长高
    一行就把上面全都顶上去一行。所以锚定要等内容真的溢出视口再挂。
    """

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        log = app.query_one("#log", VerticalScroll)
        assert log.scroll_y == 0
        assert log.max_scroll_y == 0  # 确认前提：内容确实没超出视口
        # banner 是对话区的第一块，它必须贴着顶。
        assert log.children[0].region.y == 0

    with_app([make_config()], scenario)


def test_long_conversation_follows_the_bottom(make_config, make_provider) -> None:
    """内容超出视口之后要跟着走——最新的那块必须留在屏幕上。

    这是 `_follow_tail` 的另一半。不贴底（内容不满一屏时）和不跟随（内容溢出
    之后）是两件事，改的时候很容易只顾一头：把锚定整个去掉，长对话就会停在
    顶上、新内容全在屏幕外面。

    这里特意让整段回复**一口气**灌进来（假 provider 没有间隔），因为
    `mount()` / `update()` 只是把重绘排进队列，内容刚变的那一刻 `max_scroll_y`
    还是旧值——一次性到达的一大段是最容易漏掉的情形。
    """

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        reply = "\n\n".join(f"第 {i} 段" for i in range(40))
        app.provider = make_provider([StreamEvent(text=reply), StreamEvent(done=True)])
        app.submit("来一大段")
        await finish_turn(app, pilot)

        log = app.query_one("#log", VerticalScroll)
        assert log.max_scroll_y > 0, "前提不成立：这段内容没把视口撑满"
        assert log.is_anchored, "内容已经溢出了，却还没开始跟随"
        assert log.scroll_y == log.max_scroll_y, "没停在底部"
        assert "第 39 段" in screen_text(app), "最新的一段不在屏幕上"

    with_app([make_config()], scenario)


def test_scrolling_up_stops_the_following(make_config, make_provider) -> None:
    """用户自己往上滚之后，就不再被硬拽到底。

    这条是用 Textual 的锚定、而不是每次来个新块就手动 `scroll_end()` 的理由：
    手动拽会一直跟用户抢滚动条，他根本翻不上去。锚定由 `_anchor_released`
    让位（`Widget.anchor()` 的原话是 "until the user moves the scroll position"），
    滚回底部又自动恢复。
    """

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        reply = "\n\n".join(f"第 {i} 段" for i in range(40))

        async def ask(text: str) -> None:
            app.provider = make_provider(
                [StreamEvent(text=reply), StreamEvent(done=True)]
            )
            app.submit(text)
            await finish_turn(app, pilot)

        await ask("来一大段")

        log = app.query_one("#log", VerticalScroll)
        log.scroll_up(animate=False)
        await pilot.pause()
        resting = log.scroll_y
        assert resting < log.max_scroll_y, "前提不成立：没能滚上去"

        # 再来一轮，内容会继续变长；刚才停的位置不该被拽走。
        await ask("再来一段")

        assert log.scroll_y == resting

    with_app([make_config()], scenario)


def test_reply_text_starts_on_the_same_line_as_the_marker(
    make_config, make_provider
) -> None:
    """`●` 必须跟正文的第一行同行（这是这次改版要修的观感问题之一）。

    用的是一整段**不含空格的纯中文**，这是最容易露馅的输入：Rich 的折行
    （`rich/_wrap.py` 的 `divide_line`）按空白分词，整段中文就是一个「词」，
    词比整行宽时会「先换行再硬折」——如果 `● ` 是拼进 markdown 源码里的，
    它就会被晾在单独一行、正文从第二行顶格开始。分成两列才不会这样。
    """
    reply = "杭州是一座拥有千年的历史古城，位于中国浙江省北部，自古以来就是繁华之地。"

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        app.provider = make_provider([StreamEvent(text=reply), StreamEvent(done=True)])
        app.submit("介绍一下杭州")
        await finish_turn(app, pilot)

        head = reply[:8]
        paired = [ln for ln in screen_lines(app) if MARKER in ln and head in ln]
        assert paired, "圆点没跟正文同行：\n" + screen_text(app)

    with_app([make_config()], scenario)


def test_reply_block_stays_put_when_it_settles(make_config, make_provider) -> None:
    """流式那一块定型时**不许挪窝**——这是这次改版修的主要问题。

    改之前：对话区是 `RichLog`，流式文字画在它下面另一个独立的 `#streaming`
    Static 上；定型后整段写进 RichLog（历史不满一屏时它从头顶开始排），于是
    屏幕上看起来就是「生成时贴着输入框、一结束就弹上去」。

    改之后：回复块本身就是对话区的**最后一个子节点**，流式和定型刷的是同一个
    控件。所以这里断言的是 y 坐标**逐像素相等**，而不只是「文字还在」。
    """

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        app.provider = make_provider(
            [
                StreamEvent(text="第一行\n"),
                StreamEvent(text="第二行\n"),
                StreamEvent(done=True),
            ],
            delay=0.3,
        )
        app.submit("问")
        # 故意不等流结束：要的就是「还在流式」的那一瞬间的位置。
        await pilot.pause(0.1)

        block = app._streaming
        assert isinstance(block, ReplyBlock)
        y_streaming = block.region.y
        # 挡住退化情形：布局没跑过时 region 是 (0,0,0,0)，那样两边都相等、
        # 用例会「假绿」。
        assert y_streaming > 0

        await finish_turn(app, pilot)

        assert block.region.y == y_streaming
        assert "第二行" in screen_text(app)  # 定型内容确实画上去了

    with_app([make_config()], scenario)


def test_multi_turn_history_is_passed_to_provider(make_config, make_provider) -> None:
    """F6/AC6：换轮请求带上了之前所有轮次的完整上下文。"""

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        fake = make_provider([StreamEvent(text="收到"), StreamEvent(done=True)])
        app.provider = fake

        app.submit("我叫居居")
        await finish_turn(app, pilot)
        app.submit("我叫什么")
        await finish_turn(app, pilot)

        # 两次请求各自发了什么，都要看得到——F6 的实质是「历史在累积」，
        # 只验最后一次的话，把历史发成空列表也照样能过。
        assert [(m.role, m.content) for m in fake.calls[0]] == [("user", "我叫居居")]
        assert [(m.role, m.content) for m in fake.calls[1]] == [
            ("user", "我叫居居"),
            ("assistant", "收到"),
            ("user", "我叫什么"),
        ]

    with_app([make_config()], scenario)


# ────────────────────────── 错误恢复（F11）──────────────────────────


def test_error_is_shown_and_session_survives(make_config, make_provider) -> None:
    """AC11：出错在对话区显示、不退出，下一轮照常能发。"""

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        app.provider = make_provider([StreamEvent(err=RuntimeError("invalid api key"))])

        app.submit("第一问")
        await finish_turn(app, pilot)

        assert app.state is SessionState.IDLE  # 没卡在流式态
        assert "invalid api key" in screen_text(app)
        # 失败的一轮**不往历史里塞** assistant 消息。
        assert [m.role for m in app.conv.messages()] == ["user"]

        # 换一个好的 provider，继续下一轮。
        app.provider = make_provider(
            [StreamEvent(text="这回好了"), StreamEvent(done=True)]
        )
        app.submit("第二问")
        await finish_turn(app, pilot)

        assert [m.role for m in app.conv.messages()] == ["user", "user", "assistant"]
        assert "这回好了" in screen_text(app)

    with_app([make_config()], scenario)


def test_empty_reply_is_not_stored(make_config, make_provider) -> None:
    """模型什么都没吐时，不往历史里塞空 assistant 消息。

    Anthropic 对 content 为空的消息直接 400；存进去会让**下一轮**莫名其妙失败，
    而用户完全看不出那跟上一轮有关。
    """

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        app.provider = make_provider([StreamEvent(done=True)])

        app.submit("问")
        await finish_turn(app, pilot)

        assert [m.role for m in app.conv.messages()] == ["user"]
        assert "空回复" in screen_text(app)
        assert app.state is SessionState.IDLE

    with_app([make_config()], scenario)


# ────────────────────────── 退出（F10）──────────────────────────


def test_exit_command_quits(make_config) -> None:
    """AC10：/exit 退出。"""

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        await type_and_submit(pilot, "/exit")
        await pilot.pause()

        assert app._exit is True
        # /exit 是命令，不该被当成一句话发出去。
        assert app.conv.messages() == []

    with_app([make_config()], scenario)


def test_ctrl_c_quits_even_with_input_focused(make_config) -> None:
    """AC10：Ctrl+C 退出——而且要在输入框有焦点时也管用。

    这是这条用例真正的价值所在：Textual 8.x 里 TextArea 自带
    `ctrl+c -> copy`、Screen 自带 `ctrl+c -> copy_text`，App 只把 ctrl+c 绑成
    「提示你去按 ctrl+q」。不用 priority binding 显式抢下来，焦点在输入框时
    Ctrl+C 就被「复制」吃掉，退出直接失效。
    """

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        assert isinstance(app.focused, PromptArea)  # 确认焦点确实在输入框上

        await pilot.press("ctrl+c")
        await pilot.pause()

        assert app._exit is True

    with_app([make_config()], scenario)


def test_quit_cancels_in_flight_stream(make_config, make_provider) -> None:
    """退出时要取消正在跑的流，让 SDK 有机会关掉 HTTP 连接（N7）。"""

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        app.provider = make_provider(
            [StreamEvent(text="慢"), StreamEvent(done=True)], delay=5.0
        )
        app.submit("在吗")
        await pilot.pause(0.05)

        task = app._stream_task
        assert task is not None and not task.done()

        app._quit()
        await pilot.pause()

        assert task.done()

    with_app([make_config()], scenario)


# ────────────────────────── 密钥安全（N5）──────────────────────────


def test_api_key_never_appears_on_screen(make_config, make_provider) -> None:
    """N5：界面上任何地方都不出现密钥。

    用一个一眼能认出来的假密钥，然后在整屏导出里搜它——比「我看了一遍没看到」
    可靠得多。
    """
    secret = "sk-DEADBEEF-should-never-be-rendered"

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        app.provider = make_provider([StreamEvent(text="好"), StreamEvent(done=True)])
        app.submit("你好")
        await finish_turn(app, pilot)

        assert secret not in screen_text(app)

    with_app([make_config(api_key=secret)], scenario)


def test_api_key_inside_an_error_message_is_masked(make_config, make_provider) -> None:
    """N5：上游把密钥抄进了错误原文时，抹掉它再显示。

    `str(exc)` 是别人写的字符串，抄没抄凭据不由我们说了算；而这句原文会直接
    画在用户屏幕上（`tests/test_redact.py` 里另有一套针对这一层函数的边界用例）。
    """
    secret = "sk-DEADBEEF-should-never-be-rendered"

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        app.provider = make_provider(
            [
                StreamEvent(
                    err=RuntimeError(f"401 invalid api key: {secret} (check config)")
                )
            ]
        )
        app.submit("问")
        await finish_turn(app, pilot)

        screen = screen_text(app)
        assert secret not in screen
        # 抹掉的是密钥，不是整条错误——排障信息必须留着。
        assert "401 invalid api key" in screen
        assert "check config" in screen
        assert "***" in screen

    with_app([make_config(api_key=secret)], scenario)


def test_other_providers_keys_are_masked_too(make_config, make_provider) -> None:
    """抹的是**配置里所有** provider 的密钥，不止当前在用的那个。

    出错的时候用户多半正要切到另一家去试，那时候弹出来的错误里同样不该带出
    别家的密钥——哪怕这次请求根本没用它。
    """
    active = "sk-ACTIVE-key-aaaaaaaaaaaa"
    other = "sk-OTHER-key-bbbbbbbbbbbb"

    async def scenario(app: QicodeApp, pilot: Pilot) -> None:
        # 两份配置会先进选择界面；这条用例不关心选择，直接摆成对话态。
        app.state = SessionState.IDLE
        app.provider = make_provider(
            [StreamEvent(err=RuntimeError(f"boom: {active} / {other}"))]
        )
        app.submit("问")
        await finish_turn(app, pilot)

        screen = screen_text(app)
        assert active not in screen
        assert other not in screen

    with_app(
        [make_config(name="a", api_key=active), make_config(name="b", api_key=other)],
        scenario,
    )
