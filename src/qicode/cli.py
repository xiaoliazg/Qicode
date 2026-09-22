"""Qicode 入口装配（T13）。

职责就三件事：找配置、把配置错误变成人能看懂的话、启动界面。
业务逻辑一概不在这一层。
"""

import sys

from rich.console import Console

from qicode.config import ConfigError, load
from qicode.tui.app import QicodeApp
from qicode.tui.view import transcript

# 配置文件相对**当前工作目录**找，不进 home、不看环境变量
# （docs/v1/spec.md「不做的事」里明确排除了其它配置来源）。
CONFIG_PATH = ".qicode/config.yaml"


def main() -> None:
    try:
        cfg = load(CONFIG_PATH)
    except ConfigError as exc:
        # 配置错误是**用户能自己修好**的，所以只打印一行可读信息、
        # 不带 traceback（N4），然后用非零码退出。
        print(f"qicode: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    # banner 交给界面在 on_mount 里写进 RichLog，而不是在这里 print——
    # 否则它会留在 Textual 接管屏幕**之前**的滚动缓冲里，两种输出混在一起。
    app = QicodeApp(cfg.providers)
    app.run()

    _replay_transcript(app)


def _replay_transcript(app: QicodeApp) -> None:
    """退出后把这次会话打到主屏幕上（`docs/v1/checklist.md` 的 scrollback 那条）。

    Textual 跑在备用屏幕上，`run()` 一返回，刚才整屏内容连同它的滚动历史就一起
    没了——用户按完 /exit 什么都看不到。这里把对话重放一遍，它才真正落进终端的
    回滚缓冲里，之后能用终端原生的方式翻看。

    放在 `run()` **之后**是必须的：备用屏幕还没退出的任何输出都会被一起丢掉。
    """
    messages = app.conv.messages()
    if not messages:
        # 一句没聊就退出了（比如只是误开了想看看）。这时候回放只会留下一片空白。
        return
    Console().print(transcript(messages))


if __name__ == "__main__":  # pragma: no cover
    main()
