"""入口装配的单测（T13、N4）。

验的是「配置出错时人看到什么」：一行可读信息 + 非零退出码，**不是**一堆 traceback。
"""

import pytest
import yaml

from qicode.cli import main
from qicode.conversation import Conversation
from qicode.tool import Registry


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
        def __init__(self, providers: object, registry: Registry) -> None:
            started.append(providers)
            started.append(registry)
            self.conv = Conversation()

        def run(self) -> None:
            started.append("ran")

    monkeypatch.setattr("qicode.cli.QicodeApp", FakeApp)

    main()

    assert started[-1] == "ran"
    # 传下去的确实是解析好的 providers。
    providers = started[0]
    assert isinstance(providers, list)
    assert providers[0].name == "anthropic"
    # 工具注册中心是在 cli 这一层建好的，六个核心工具一个不少（F2）。
    registry = started[1]
    assert [d.name for d in registry.definitions()] == [
        "read_file",
        "write_file",
        "edit_file",
        "bash",
        "glob",
        "grep",
    ]


def test_transcript_is_replayed_after_exit(tmp_path, monkeypatch, capsys):
    """退出后把会话打到主屏幕上（checklist 的 scrollback 那条）。

    Textual 跑在备用屏幕上，`run()` 一返回整屏内容就没了。这条用例守的是
    「退出之后还能看到刚才聊了什么」——回放必须发生在 `run()` **之后**。
    """
    cfg_dir = tmp_path / ".qicode"
    cfg_dir.mkdir()
    doc = {
        "providers": [
            {
                "name": "a",
                "protocol": "anthropic",
                "api_key": "sk-test",
                "model": "m",
            }
        ]
    }
    (cfg_dir / "config.yaml").write_text(
        yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    class FakeApp:
        def __init__(self, providers: object, registry: Registry) -> None:
            self.conv = Conversation()
            self.conv.add_user("我叫居居")
            self.conv.add_assistant("记住了。")

        def run(self) -> None:
            # 打一个记号，等下用来验**顺序**：回放必须排在 run() 返回之后。
            print("RUN-RETURNED", flush=True)

    monkeypatch.setattr("qicode.cli.QicodeApp", FakeApp)

    main()

    out = capsys.readouterr().out
    assert "我叫居居" in out
    assert "记住了。" in out
    # 顺序：备用屏幕还没退出时打的东西会被一起丢掉，所以回放必须在 run() 之后。
    assert out.index("RUN-RETURNED") < out.index("我叫居居")


def test_nothing_is_replayed_when_nothing_was_said(tmp_path, monkeypatch, capsys):
    """一句没聊就退出：不打一片空白出来。"""
    cfg_dir = tmp_path / ".qicode"
    cfg_dir.mkdir()
    doc = {
        "providers": [
            {
                "name": "a",
                "protocol": "anthropic",
                "api_key": "sk-test",
                "model": "m",
            }
        ]
    }
    (cfg_dir / "config.yaml").write_text(
        yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    class FakeApp:
        def __init__(self, providers: object, registry: Registry) -> None:
            self.conv = Conversation()

        def run(self) -> None:
            pass

    monkeypatch.setattr("qicode.cli.QicodeApp", FakeApp)

    main()

    assert capsys.readouterr().out == ""
