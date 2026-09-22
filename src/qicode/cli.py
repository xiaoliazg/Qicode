"""Qicode 入口装配（T13）。

职责就三件事：找配置、把配置错误变成人能看懂的话、启动界面。
业务逻辑一概不在这一层。
"""

import sys

from qicode.config import ConfigError, load
from qicode.tui.app import QicodeApp

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
    QicodeApp(cfg.providers).run()


if __name__ == "__main__":  # pragma: no cover
    main()
