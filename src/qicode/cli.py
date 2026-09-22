"""Qicode 入口。

**当前是 T1 的占位实现**：只打印版本号，用来确认「包能装、命令能跑」这条链路是通的。
T13 会把它替换成真正的装配——加载配置、打印 banner、启动 Textual App。
"""

from qicode import __version__


def main() -> None:
    print(f"qicode {__version__}")
