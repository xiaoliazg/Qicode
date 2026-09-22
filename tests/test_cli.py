"""入口装配的单测（T13、N4）。

验的是「配置出错时人看到什么」：一行可读信息 + 非零退出码，**不是**一堆 traceback。
"""

import pytest
import yaml

from qicode.cli import main


def test_missing_config_exits_with_readable_message(tmp_path, monkeypatch, capsys):
    """配置文件不存在时（N4）。"""
    monkeypatch.chdir(tmp_path)  # 空目录，没有 .qicode/config.yaml

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "配置文件不存在" in err
    assert ".qicode/config.yaml" in err
    assert "Traceback" not in err


def test_invalid_yaml_exits_with_readable_message(tmp_path, monkeypatch, capsys):
    """YAML 语法错时，错误信息要指到具体行列（N4）。"""
    cfg_dir = tmp_path / ".qicode"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(
        "providers: [\n  - name: 破\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "不是合法的 YAML" in err
    assert "第" in err and "行" in err  # 指到了具体位置
    assert "Traceback" not in err


def test_missing_required_field_names_the_field(tmp_path, monkeypatch, capsys):
    """缺字段时，信息里要点名是哪个 provider 的哪个字段。"""
    cfg_dir = tmp_path / ".qicode"
    cfg_dir.mkdir()
    doc = {"providers": [{"name": "a", "protocol": "anthropic", "model": "m"}]}
    (cfg_dir / "config.yaml").write_text(
        yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "providers[0].api_key" in err


def test_valid_config_starts_the_app(tmp_path, monkeypatch):
    """配置没问题时，入口要把 App 拉起来（这里把 run() 换掉，不起真终端）。"""
    cfg_dir = tmp_path / ".qicode"
    cfg_dir.mkdir()
    doc = {
        "providers": [
            {
                "name": "anthropic",
                "protocol": "anthropic",
                "api_key": "sk-test",
                "model": "claude-sonnet-5",
            }
        ]
    }
    (cfg_dir / "config.yaml").write_text(
        yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    started: list[object] = []

    class FakeApp:
        def __init__(self, providers: object) -> None:
            started.append(providers)

        def run(self) -> None:
            started.append("ran")

    monkeypatch.setattr("qicode.cli.QicodeApp", FakeApp)

    main()

    assert started[-1] == "ran"
    # 传下去的确实是解析好的 providers。
    providers = started[0]
    assert isinstance(providers, list)
    assert providers[0].name == "anthropic"
