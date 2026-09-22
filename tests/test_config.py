"""config 模块单测（T2）。

覆盖三层失败：文件层（不存在 / 读不动）、YAML 层（语法错 / 顶层类型不对）、
字段层（必填缺失、协议不认识、类型不对）。

断言不满足于「抛了 ConfigError」，多数还检查 message 里**有没有指出是哪一项的哪个
字段**——启动期报错的价值全在这，报不准等于没报。
"""

from pathlib import Path

import pytest
import yaml

from qicode.config import Config, ConfigError, _describe_yaml_error, load


def write_config(tmp_path: Path, text: str) -> str:
    """把一段配置文本落到临时文件，返回路径。"""
    file = tmp_path / "config.yaml"
    file.write_text(text, encoding="utf-8")
    return str(file)


def dump(providers: list[dict]) -> str:
    """把 providers 列表序列化成配置文本。

    用 pyyaml 反向生成而不是手写字符串：省得测试数据自己先写错缩进，
    那会让「测试挂了」变得分不清是代码的问题还是测试数据的问题。
    """
    return yaml.safe_dump({"providers": providers}, allow_unicode=True, sort_keys=False)


def provider(**overrides: object) -> dict:
    """一份最小的合法 provider，按需覆盖字段。"""
    base: dict = {
        "name": "deepseek",
        "protocol": "anthropic",
        "api_key": "sk-test",
        "model": "deepseek-chat",
    }
    base.update(overrides)
    return base


# ───────────────────────── 正常路径 ─────────────────────────


def test_full_fields_are_read_back(tmp_path):
    path = write_config(
        tmp_path,
        dump(
            [
                {
                    "name": "claude",
                    "protocol": "anthropic",
                    "api_key": "sk-ant-xxx",
                    "model": "claude-sonnet-5",
                    "base_url": "https://api.anthropic.com",
                    "thinking": True,
                }
            ]
        ),
    )

    cfg = load(path)

    assert isinstance(cfg, Config)
    assert len(cfg.providers) == 1
    p = cfg.providers[0]
    assert (p.name, p.protocol, p.api_key, p.model) == (
        "claude",
        "anthropic",
        "sk-ant-xxx",
        "claude-sonnet-5",
    )
    assert p.base_url == "https://api.anthropic.com"
    assert p.thinking is True


def test_optional_fields_fall_back_to_defaults(tmp_path):
    cfg = load(write_config(tmp_path, dump([provider()])))

    p = cfg.providers[0]
    assert p.base_url is None  # None 表示交给 SDK 用官方端点
    assert p.thinking is False


def test_multiple_providers_keep_their_order(tmp_path):
    """顺序有意义：配置里的第一项就是「不选直接进」时的默认 provider（F2）。"""
    providers = [
        provider(name="first"),
        provider(
            name="second", protocol="openai", base_url="http://127.0.0.1:11434/v1"
        ),
    ]

    cfg = load(write_config(tmp_path, dump(providers)))

    assert [p.name for p in cfg.providers] == ["first", "second"]
    assert [p.protocol for p in cfg.providers] == ["anthropic", "openai"]


# ───────────────────────── 文件层 ─────────────────────────


def test_missing_file_raises(tmp_path):
    missing = tmp_path / "not-here.yaml"

    with pytest.raises(ConfigError) as excinfo:
        load(str(missing))

    # 报错要带上具体路径：只说不存在，用户还得猜我们找的是哪个位置。
    assert "配置文件不存在" in str(excinfo.value)
    assert str(missing) in str(excinfo.value)


def test_directory_as_path_raises(tmp_path):
    with pytest.raises(ConfigError, match="配置文件不存在"):
        load(str(tmp_path))


# ───────────────────────── YAML 层 ─────────────────────────


def test_broken_yaml_raises_config_error(tmp_path):
    path = write_config(tmp_path, "providers: [\n  - name: x\n")  # 中括号没闭合

    with pytest.raises(ConfigError) as excinfo:
        load(path)

    message = str(excinfo.value)
    assert "YAML" in message
    assert path in message
    # 关键：要能定位到第几行。pyyaml 原文是带源码片段的多行文本，
    # 直接漏出去会变成一大坨，这里要求它被压成带行列号的一句。
    assert "行" in message and "列" in message
    assert "\n" not in message


def test_yaml_error_without_position_is_still_one_line():
    """没有位置信息的 YAMLError（少见）也不能把多行原文原样漏出去。"""
    assert _describe_yaml_error(yaml.YAMLError("第一行\n第二行")) == "第一行 第二行"


@pytest.mark.parametrize("text", ["", "just a string\n", "- 1\n- 2\n"])
def test_top_level_must_be_a_mapping(tmp_path, text):
    # 空文件解析出 None、只写一行标量、顶层是列表，都该在这里被拦下。
    with pytest.raises(ConfigError, match="顶层"):
        load(write_config(tmp_path, text))


def test_providers_empty_list(tmp_path):
    with pytest.raises(ConfigError, match="providers 不能为空"):
        load(write_config(tmp_path, "providers: []\n"))


def test_providers_key_missing(tmp_path):
    with pytest.raises(ConfigError, match="providers 不能为空"):
        load(write_config(tmp_path, "other: 1\n"))


def test_providers_without_content(tmp_path):
    with pytest.raises(ConfigError, match="providers 不能为空"):
        load(write_config(tmp_path, "providers:\n"))


def test_providers_not_a_list(tmp_path):
    with pytest.raises(ConfigError, match="providers 必须是列表"):
        load(write_config(tmp_path, "providers: 3\n"))


def test_provider_item_not_a_mapping(tmp_path):
    with pytest.raises(ConfigError, match=r"providers\[0\]"):
        load(write_config(tmp_path, "providers:\n  - 3\n"))


# ───────────────────────── 字段层 ─────────────────────────


@pytest.mark.parametrize("field", ["name", "protocol", "api_key", "model"])
def test_required_field_missing(tmp_path, field):
    incomplete = provider()
    del incomplete[field]

    with pytest.raises(ConfigError) as excinfo:
        load(write_config(tmp_path, dump([incomplete])))

    assert f"providers[0].{field} 不能为空" in str(excinfo.value)


@pytest.mark.parametrize("field", ["name", "protocol", "api_key", "model"])
def test_required_field_blank(tmp_path, field):
    """只有空白的值按空处理——`api_key: "   "` 显然不是有意的。"""
    with pytest.raises(ConfigError) as excinfo:
        load(write_config(tmp_path, dump([provider(**{field: "   "})])))

    assert f"providers[0].{field} 不能为空" in str(excinfo.value)


@pytest.mark.parametrize(
    "protocol",
    [
        "openai_compatible",  # 旧配置里用过的写法，容易被顺手抄进来
        "Anthropic",  # 大小写敏感
        "gemini",
    ],
)
def test_unknown_protocol_lists_the_valid_ones(tmp_path, protocol):
    with pytest.raises(ConfigError) as excinfo:
        load(write_config(tmp_path, dump([provider(protocol=protocol)])))

    message = str(excinfo.value)
    assert "providers[0].protocol" in message
    assert protocol in message  # 把实际值回显出来，方便对照
    assert "anthropic" in message and "openai" in message  # 顺便告诉用户可选什么


def test_numeric_api_key_is_rejected(tmp_path):
    """YAML 会把不加引号的 12345 解析成整数，不能当合法密钥放过去。"""
    with pytest.raises(ConfigError, match=r"providers\[0\]\.api_key 必须是字符串"):
        load(write_config(tmp_path, dump([provider(api_key=12345)])))


def test_base_url_wrong_type_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match=r"providers\[0\]\.base_url 必须是字符串"):
        load(write_config(tmp_path, dump([provider(base_url=11434)])))


def test_thinking_must_be_a_real_bool(tmp_path):
    """`thinking: "yes"` 是字符串不是布尔——报错好过静默按 False 跑。"""
    with pytest.raises(
        ConfigError, match=r"providers\[0\]\.thinking 必须是 true / false"
    ):
        load(write_config(tmp_path, dump([provider(thinking="yes")])))


def test_error_names_the_offending_index(tmp_path):
    """多项配置里第 2 项坏掉，报错必须指向 [1]，否则用户只能从头数。"""
    providers = [provider(), provider(name="broken", api_key=None)]

    with pytest.raises(ConfigError) as excinfo:
        load(write_config(tmp_path, dump(providers)))

    assert "providers[1].api_key 不能为空" in str(excinfo.value)
