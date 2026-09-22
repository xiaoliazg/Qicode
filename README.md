# Qicode

终端 AI 编程助手（类似 Claude Code），Python + Textual 实现。

> **状态：v1 已实现，可以真正对话了。** v1 的目标是「多协议 LLM 终端对话客户端」——
> 人 ↔ LLM 的最小闭环：多协议接入、流式输出、markdown 定型、多轮上下文、退出后回放。
> 工具调用、权限、记忆等留到后续阶段。逐条验收证据见
> [docs/v1/checklist.md](docs/v1/checklist.md)。

## 文档

设计文档按**阶段**归档，每个阶段四份，逐份细化：

```
spec.md（做什么）→ plan.md（怎么做）→ task.md（按什么顺序做）→ checklist.md（做对了没）
```

- 索引与阶段表：[docs/README.md](docs/README.md)
- 当前阶段 v1：[需求](docs/v1/spec.md) · [设计](docs/v1/plan.md) · [任务](docs/v1/task.md) · [验收](docs/v1/checklist.md)

进度以 `docs/README.md` 的阶段表为准，本文件不另维护一份——两份必然对不上。

## 环境要求

- Python **≥ 3.12**
- macOS / Linux
- 开发用 conda 虚拟环境 `Qicode`

## 安装

```bash
conda activate Qicode
uv pip install -e . --group dev
```

装好后有两个等价入口：`qicode` 命令、`python -m qicode`。

> 不用 `uv sync`：它会建一个 `.venv`，跟本项目的 conda 环境约定冲突。
> 用 `uv pip install --python <conda 里的 python>` 也可以，效果一样，甚至不必先激活环境。

## 配置

复制模板再改：

```bash
mkdir -p .qicode && cp .qicode/config.yaml.example .qicode/config.yaml
```

[.qicode/config.yaml.example](.qicode/config.yaml.example) 里每个字段都有注释。
`.qicode/config.yaml` 已在 `.gitignore` 中忽略——它放真实密钥，**不要提交**，也不要
把密钥写进测试或临时脚本。

⚠️ v1 的 `api_key` 是纯字符串，**不做 `${ENV_VAR}` 之类的变量展开**。写成
`${ANTHROPIC_API_KEY}` 会被当作密钥字面量发出去，最后撞一个认证失败。请直接填明文，
靠 `.gitignore` 挡住。

## 用法

```bash
cd <你的项目目录>     # 配置文件相对当前工作目录找
qicode               # 或 python -m qicode
```

| 按键 | 作用 |
|------|------|
| `Enter` | 发送 |
| `Ctrl+J` | 换行（**这个最可靠**：raw mode 下原样送出，哪个终端都一样） |
| `Alt+Enter` / `Shift+Enter` | 换行（需要终端支持扩展键盘协议，如 Kitty / WezTerm / Ghostty） |
| `/exit` | 退出 |
| `Ctrl+C` | 退出（注意：因此输入框里不能用 Ctrl+C 复制；macOS 上可用 Cmd+C） |

退出后本次会话会**重放到主屏幕**，之后能用终端自带的回滚方式翻看——Textual 跑在备用
屏幕上，不重放的话退出的一瞬间就什么都不剩了。

## 开发

```bash
pytest tests/ -v                        # 单元测试（不触网，用 mock）
ruff check . && ruff format --check .   # lint 与格式
mypy src/qicode/                        # 类型检查
```

功能做完后用 tmux 起真实后端跑端到端验收，逐项对照当前阶段的 `checklist.md` 记录证据。
