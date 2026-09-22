"""`tui.select` 的单测（T11、F2）。

纯数据变换：provider 列表 ↔ 选择列表条目。
"""

import pytest

from qicode.tui.select import build_options, pick


def test_build_options_lists_name_and_model(make_config) -> None:
    """F2 要求列表里能看出「名称 + 模型」。"""
    providers = [
        make_config(name="anthropic", model="claude-sonnet-5"),
        make_config(name="ollama", model="qwen2.5vl:7b"),
    ]

    options = build_options(providers)

    assert len(options) == 2
    assert "anthropic" in str(options[0].prompt)
    assert "claude-sonnet-5" in str(options[0].prompt)
    assert "qwen2.5vl:7b" in str(options[1].prompt)


def test_pick_returns_the_config_at_that_index(make_config) -> None:
    first = make_config(name="a", model="m-a")
    second = make_config(name="b", model="m-b")

    assert pick([first, second], "0") is first
    assert pick([first, second], "1") is second


def test_pick_handles_duplicate_names(make_config) -> None:
    """同名的两份配置也要能区分开——这正是拿下标当 id 的理由。"""
    first = make_config(name="claude", model="opus")
    second = make_config(name="claude", model="sonnet")

    assert pick([first, second], "1") is second


def test_pick_rejects_missing_id(make_config) -> None:
    with pytest.raises(ValueError, match="没有 id"):
        pick([make_config()], None)


def test_pick_rejects_non_numeric_id(make_config) -> None:
    with pytest.raises(ValueError, match="id 无效"):
        pick([make_config()], "abc")


@pytest.mark.parametrize("bad_id", ["9", "-1"])
def test_pick_rejects_out_of_range_id(make_config, bad_id: str) -> None:
    """越界要自己判，不能指望 IndexError。

    `-1` 在 Python 里是合法下标，会绕回最后一条——选了 A 却用上 B，
    而且一声不吭。这条用例专门守住这个。
    """
    with pytest.raises(ValueError, match="id 越界"):
        pick([make_config()], bad_id)
