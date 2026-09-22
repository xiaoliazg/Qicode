"""配置加载与校验（F1）。

职责很窄：**把 `.qicode/config.yaml` 变成一份可信的 `ProviderConfig` 列表**。
这一层只做「读文件 → 翻译 → 校验」，没有任何通信行为——上层拿到手里的数据一定是
合法的，不必再判断字段在不在、协议认不认识。

校验为什么放在这里而不是让各适配器自己查：错误要**在启动期一次性讲清楚**。
`providers[1].api_key 不能为空` 比跑起来之后在请求上撞一个 401 有用得多（F1、AC1）。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

# 协议名的合法取值。用 Literal 而不是裸 str，是为了让类型检查器也能挡住写错的协议名。
ProtocolName = Literal["anthropic", "openai"]

# 认得的协议名，也是适配器分派的依据（见 docs/v1/plan.md「模块 qicode.llm」）。
#
# 写成「映射」而不是「元组」是有原因的：查表命中时拿到的值就是 ProtocolName 类型，
# 校验和类型收窄一步完成，不必再 cast 一次。键用于校验（`protocol not in ...`），
# 值用于构造 —— 两者是同一份数据，报错信息里的「可选值」也直接迭代这个映射，不会写歪。
_PROTOCOLS: dict[str, ProtocolName] = {"anthropic": "anthropic", "openai": "openai"}

# 每一项 provider 都必须写、且不能为空的字段。顺序即报错时的呈现顺序。
_REQUIRED_FIELDS: tuple[str, ...] = ("name", "protocol", "api_key", "model")


class ConfigError(Exception):
    """配置相关的失败一律抛这个类型。

    调用方（`cli.main`）只需接住它、打印、以退出码 1 结束，不必区分「文件没找到」
    还是「YAML 语法错」还是「字段不合法」——message 里已经写明是哪一种了。
    """


@dataclass
class ProviderConfig:
    """一个后端接入点的全部信息，纯数据。"""

    name: str  # 状态栏左侧显示
    protocol: ProtocolName  # 决定用哪个适配器
    api_key: str
    model: str  # 状态栏右侧显示
    base_url: str | None = None  # None 表示用该协议 SDK 的默认端点
    thinking: bool = False  # 是否主动请求扩展思考；仅 anthropic 适配器会读它


@dataclass
class Config:
    """整个配置文件。目前只有 providers 一项。"""

    providers: list[ProviderConfig] = field(default_factory=list)


def load(path: str) -> Config:
    """读取并校验配置文件，返回 `Config`；任何问题都以 `ConfigError` 抛出。

    分三步，每步的失败信息都带上足够定位的上下文（是哪个文件、第几项、哪个字段）：
    读文件 → 解析 YAML → 映射成 dataclass。
    """
    file = Path(path)
    if not file.is_file():
        # 顶层目录不存在、路径写错、指到了目录上，在这里是同一回事：没有可读的文件。
        raise ConfigError(f"配置文件不存在: {path}")

    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        # 文件在，但读不动（权限、编码、设备问题）。要和「不存在」区分开，
        # 否则用户会去反复确认路径，而真正的问题是权限。
        raise ConfigError(f"配置文件读取失败: {path}（{exc}）") from exc

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        # 把 yaml 的异常类型统一收进 ConfigError：调用方只需要认识一种异常。
        raise ConfigError(
            f"配置文件不是合法的 YAML: {path}（{_describe_yaml_error(exc)}）"
        ) from exc

    return _from_dict(raw)


def _describe_yaml_error(exc: yaml.YAMLError) -> str:
    """把 pyyaml 的报错压成一句「第几行第几列、什么毛病」。

    pyyaml 原文是 traceback 样式的多行文本，带源码片段和 `^` 插入符。直接塞进启动期的
    一行报错里，那些源码片段会把位置信息淹掉——而位置恰恰是改 YAML 时唯一需要的东西。
    """
    mark = getattr(exc, "problem_mark", None)
    problem = getattr(exc, "problem", None) or str(exc)
    if mark is None:
        # 没有位置信息的 YAMLError（少见），退化成单行原文。
        return problem.replace("\n", " ")
    return f"第 {mark.line + 1} 行第 {mark.column + 1} 列：{problem}"


def _from_dict(raw: Any) -> Config:
    """把解析出来的顶层对象映射成 `Config`。

    这里刻意不用 `ProviderConfig(**item)` 之类的一步到位写法：那样字段名写错会先撞上
    Python 的 TypeError，报错就变成了「unexpected keyword argument」而不是「第几项缺什么」，
    用户拿到的是实现细节而不是配置问题。
    """
    if not isinstance(raw, dict):
        # 空文件（safe_load 得到 None）、只写了一行标量、顶层是个列表，都落在这里。
        raise ConfigError("配置文件顶层必须是键值对，例如：\nproviders:\n  - name: ...")

    raw_providers = raw.get("providers")
    if raw_providers is None:
        # 键没写，或者写了 `providers:` 却没跟内容——对用户来说都是「还没配」。
        raise ConfigError("providers 不能为空，至少配置一个")
    if not isinstance(raw_providers, list):
        raise ConfigError("providers 必须是列表")
    if not raw_providers:
        raise ConfigError("providers 不能为空，至少配置一个")

    return Config(
        providers=[
            _parse_provider(item, index) for index, item in enumerate(raw_providers)
        ]
    )


def _parse_provider(item: Any, index: int) -> ProviderConfig:
    """把列表里的一项翻成 `ProviderConfig`。

    报错信息统一带 `providers[<下标>]` 前缀：配置里通常有好几项，不说清楚是哪一项，
    用户得自己从头数。
    """
    where = f"providers[{index}]"
    if not isinstance(item, dict):
        raise ConfigError(f"{where} 必须是键值对（name / protocol / api_key / model）")

    # 必填字段先逐个过一遍。「键没写」和「写了个空串」归为同一类错误——
    # 对用户来说要做的事一样：补上。
    values = {key: _require_str(item, key, where) for key in _REQUIRED_FIELDS}

    # 查表而不是 `in` 判断：命中时拿到的已经是 ProtocolName，不用额外 cast。
    protocol = _PROTOCOLS.get(values["protocol"])
    if protocol is None:
        raise ConfigError(
            f"{where}.protocol 必须是 {' 或 '.join(_PROTOCOLS)}，"
            f"实际是 {values['protocol']!r}"
        )

    return ProviderConfig(
        name=values["name"],
        protocol=protocol,
        api_key=values["api_key"],
        model=values["model"],
        base_url=_optional_str(item, "base_url", where),
        thinking=_optional_bool(item, "thinking", where),
    )


def _require_str(item: dict[Any, Any], key: str, where: str) -> str:
    """取一个必填的字符串字段；没写、为空、类型不对都在这里拦下。"""
    value = item.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        # 只有空白的字符串按空处理：`api_key: "   "` 显然不是有意的。
        raise ConfigError(f"{where}.{key} 不能为空")
    if not isinstance(value, str):
        # YAML 会把不加引号的 `api_key: 12345` 解析成整数。不在这里拦，
        # 它会一路带到 SDK 里，最后以一个看不出跟配置有关的错收场。
        raise ConfigError(
            f"{where}.{key} 必须是字符串（纯数字的编号要加引号），"
            f"实际是 {type(value).__name__}"
        )
    return value


def _optional_str(item: dict[Any, Any], key: str, where: str) -> str | None:
    """取一个可选字符串字段；没写就是 None（对 SDK 意味着「用默认端点」）。"""
    if key not in item or item[key] is None:
        return None
    # 写了就按必填的标准查：显式写下的字段不该被静默忽略。
    return _require_str(item, key, where)


def _optional_bool(item: dict[Any, Any], key: str, where: str) -> bool:
    """取一个可选布尔字段，缺省 False。

    这里刻意不把 `"true"` / `1` 之类的值「宽容地」当布尔收下：YAML 里本来就该写
    `thinking: true`，写成字符串或数字多半是笔误——启动时报错，好过静默按 False 跑，
    让用户以为思考开着而实际没开。
    """
    value = item.get(key)
    if value is None:
        return False
    if not isinstance(value, bool):
        # 只认 bool 本身：Python 里 bool 是 int 的子类，一旦放宽到 int，
        # `thinking: 1` 就会被当成合法的 True 放过去。
        raise ConfigError(
            f"{where}.{key} 必须是 true / false，实际是 {type(value).__name__}"
        )
    return value
