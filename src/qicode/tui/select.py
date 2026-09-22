"""provider 选择（F2、AC2）。

跟 `stream.py` 同样的理由：这里只有数据变换，不 import textual 的重型部件，
选谁、怎么找回配置都能单独测。
"""

from textual.widgets.option_list import Option

from qicode.config import ProviderConfig


def build_options(providers: list[ProviderConfig]) -> list[Option]:
    """把 provider 列表转成选择列表的条目（F2：列出名称与模型）。

    `Option` 的 id 用**列表下标**而不是 name：选中后要拿它找回对应的配置，
    下标天然唯一，而两套配置完全可能重名（比如同一家的两个模型都叫 claude）。
    """
    return [
        Option(f"{cfg.name} ({cfg.model})", id=str(index))
        for index, cfg in enumerate(providers)
    ]


def pick(providers: list[ProviderConfig], option_id: str | None) -> ProviderConfig:
    """从选中的条目 id 找回那份配置。

    理论上 id 一定是我们自己塞进去的下标，不该出错；但 `Option.id` 的类型是
    `str | None`，而且这是界面数据往回映射的一步，宁可在这里明确地炸，
    也不要让 `int(None)` 抛一个读不懂的 TypeError 出来。
    """
    if option_id is None:
        raise ValueError("选中的条目没有 id，无法确定是哪份配置")

    try:
        index = int(option_id)
    except ValueError as exc:
        raise ValueError(f"选中的条目 id 无效: {option_id!r}") from exc

    # 范围要自己查：**不能只靠 IndexError**。Python 的负下标是合法的，
    # `providers[-1]` 会安安静静绕回最后一条——用户选了 A，结果用上的是 B，
    # 而且一声不吭。这种错按起来最费劲，所以在这里堵死。
    if not 0 <= index < len(providers):
        raise ValueError(f"选中的条目 id 越界: {option_id!r}")

    return providers[index]
