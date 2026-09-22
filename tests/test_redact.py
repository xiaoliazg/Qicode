"""`qicode.redact` 的单测（N5）。

这条链路的下游是「显示在对话区」加「退出时回放到终端」，漏一次就是永久留在
回滚缓冲里。所以这里把边界逐条钉死，尤其是**不该抹什么**——抹过头会把有用的
排障信息一起毁掉，那也是一种 bug。
"""

import pytest

from qicode.redact import MASK, MIN_SECRET_LENGTH, redact

#: 一个长度够、一眼是密钥的串（跟 `tests/conftest.py` 里那个假 key 同风格）。
KEY = "sk-DEADBEEF-should-never-be-rendered"


def test_replaces_the_secret() -> None:
    text = f"401 Invalid api key: {KEY}"

    assert redact(text, [KEY]) == f"401 Invalid api key: {MASK}"


def test_replaces_every_occurrence() -> None:
    """同一段错误里出现两次也要抹两次——只抹第一处是最容易犯的疏漏。"""
    text = f"{KEY} ... 又一次 {KEY}"

    assert KEY not in redact(text, [KEY])
    assert redact(text, [KEY]).count(MASK) == 2


def test_replaces_every_secret_in_the_list() -> None:
    """配置里所有 provider 的密钥都要抹，不止当前在用的那个。"""
    other = "sk-ANOTHER-LONG-KEY-abcdef"
    text = f"primary={KEY} fallback={other}"

    cleaned = redact(text, [KEY, other])

    assert KEY not in cleaned
    assert other not in cleaned
    assert cleaned == f"primary={MASK} fallback={MASK}"


def test_leaves_ordinary_text_alone() -> None:
    """没命中就一个字都不动——别把正常错误信息改得面目全非。"""
    text = "Connection refused: 127.0.0.1:11434"

    assert redact(text, [KEY]) == text


def test_short_placeholder_keys_are_not_redacted() -> None:
    """占位符式的短 api_key 不抹，否则会把普通排障信息毁掉。

    本地 Ollama 的 api_key 惯例就是随便写 `ollama`。真要按字面替换，
    「cannot reach ollama server」会变成「cannot reach *** server」，
    把唯一有用的线索抹了。这是个**有代价的取舍**，不是漏网之鱼。
    """
    text = "cannot reach ollama server"

    assert redact(text, ["ollama"]) == text


def test_empty_key_does_not_shred_the_text() -> None:
    """空串必须跳过。

    `"abc".replace("", "-")` 得到的是 `-a-b-c-`——不拦的话，一个空 api_key
    会把整段错误信息搅成碎片。配置层挡过空值，但这是公开函数。
    """
    text = "Connection refused"

    assert redact(text, ["", KEY]) == text


@pytest.mark.parametrize(
    ("length", "should_redact"),
    [(MIN_SECRET_LENGTH - 1, False), (MIN_SECRET_LENGTH, True)],
)
def test_threshold_boundary(length: int, should_redact: bool) -> None:
    """门槛两侧各钉一条：差一个字符就该有不同结果。"""
    secret = "k" * length
    text = f"key={secret}"

    cleaned = redact(text, [secret])

    assert (cleaned == f"key={MASK}") is should_redact
