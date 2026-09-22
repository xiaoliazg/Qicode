# Qicode

一个终端 AI 对话助手（类似 Claude Code），使用 Python 实现。

**V1 定位：对话基座** —— 让你在终端里和模型「聊起来」，为后续叠加 tool use、文件操作等 agent 能力打好地基。当前 V1 是**纯对话**（不读写文件、不执行命令）。

模型接入按协议抽象成两层，两条链路都支持：

| 协议 | 对接对象 | 本项目验证状态 |
|------|----------|----------------|
| `openai_compatible` | 本地 Ollama、OpenAI 官方接口、各类 OpenAI 兼容网关 | **已真机验证**（本地 Ollama `qwen2.5vl:7b`） |
| `anthropic` | Anthropic 官方 Claude、DeepSeek 的 Anthropic 端点等 | **已真机验证**（DeepSeek 的 Anthropic 端点，含思考块）；Anthropic **官方** Claude 仍仅单元测试 |

## 功能

- **流式回复**：回复以 SSE 流式逐字打印——每个增量到达就立即重绘一次，而不是按固定帧率成段跳变。
- **多轮记忆**：AI 记得当前会话内说过的话，上下文完整传给模型。
- **可扩展思考**：开启 `thinking` 后，模型的思考过程以灰色斜体先行流式展示，正式回复紧随其后。能否生效取决于所选后端与模型是否支持思考输出。
- **多 provider**：一份 YAML 配置多个后端，启动直接进对话（默认用配置里第一个），运行中 `/model` 切换，**切换后历史保留**（跨协议切换同样保留）。
- **可打断**：生成过程中按 `Esc` 立即停止，已打印内容保留为该条回复。
- **富文本**：回复按 Markdown 渲染（代码块等宽、列表、加粗等）。

## 环境要求

- Python **≥ 3.10**（本项目用 conda 虚拟环境 `Qicode` 开发，Python 3.12）
- macOS / Linux

## 安装

```bash
# 用 conda 环境（本项目约定）
conda activate Qicode
pip install -e ".[dev]"
```

安装后得到 `qicode` 命令（等价于 `python -m qicode`）。

## 配置

配置文件默认在 `~/.qicode/config.yaml`，可用 `-c` 指定其他路径。参考 [config.example.yaml](config.example.yaml)：

```bash
mkdir -p ~/.qicode && cp config.example.yaml ~/.qicode/config.yaml
```

每个 provider **五个必填字段 + 两个可选字段**：

| 字段 | 必填 | 说明 |
|------|:----:|------|
| `name` | ✔ | 供应商标识名（列表内不可重复） |
| `protocol` | ✔ | 协议类型：`openai_compatible` 或 `anthropic` |
| `model` | ✔ | 模型名（服务端注册/约定的模型 ID） |
| `base_url` | ✔ | 服务地址（`http://` / `https://` 开头） |
| `api_key` | ✔ | 认证密钥；支持 `${ENV_VAR}` 引用环境变量 |
| `thinking` | | 是否启用扩展思考，缺省 `false` |
| `thinking_params` | | 思考开关对应的请求参数；不写则用适配器默认值 |

### 两条链路的配置片段

```yaml
providers:
  # OpenAI 兼容协议（本地 Ollama 开箱即用）
  - name: my-ollama
    protocol: openai_compatible
    model: qwen2.5vl:7b
    base_url: http://127.0.0.1:11434/v1
    api_key: ollama            # Ollama 不校验密钥，填任意非空值

  # Anthropic 原生协议（官方 Claude）
  - name: my-claude
    protocol: anthropic
    model: claude-sonnet-5
    base_url: https://api.anthropic.com
    api_key: ${ANTHROPIC_API_KEY}   # 用前先 export
    thinking: true
```

### API key 与 thinking 说明

- **API key**：写成 `api_key: ${ANTHROPIC_API_KEY}` 会读取环境变量，避免明文落盘；用之前先 `export ANTHROPIC_API_KEY=<你的密钥>`。变量没设置时启动会明确报出变量名，而不是抛裸异常。
- **`thinking` 的含义是「主动请求开启思考」，关掉它不等于服务端一定不思考。** 我们不发送思考参数时，服务端会用它自己的默认值——而 Anthropic 官方 Opus 5 / Fable 5、DeepSeek 等后端的默认**就是开启思考**。所以 `thinking: false` 的准确含义是「不主动请求」，不是「关闭」。要**确保关闭**，得用 `thinking_params` 明确表达（见下）。
- **`thinking_params` 是「怎么向服务端表达思考」**，与 `thinking`（开关）分开。各协议字段形状不同（OpenAI 兼容侧是 `chat_template_kwargs`，Anthropic 侧是 `thinking` 参数）。**规则：显式写了就一定会发出去，与 `thinking` 开关无关**；只有没写时，才由 `thinking` 决定：

  | `thinking` | `thinking_params` | 实际发出去的参数 |
  |:----------:|:-----------------:|------------------|
  | `true` | 未写 | 适配器默认值（见下表） |
  | `true` | 已写 | 你写的那份 |
  | `false` | 未写 | **什么都不发**（服务端按自身默认值来） |
  | `false` | 已写 | **你写的那份** ← 这才是真正关闭思考的办法 |

  | 协议 | 适配器默认值 |
  |------|--------------|
  | `openai_compatible` | `{"chat_template_kwargs": {"enable_thinking": true}}` |
  | `anthropic` | `{"type": "adaptive", "display": "summarized"}` |

  Anthropic 侧这几个值都不是随便挑的：**固定预算写法 `{type: enabled, budget_tokens: N}` 在当前 Claude 模型上已被移除、传了直接返回 400**；`display` 缺省为 `"omitted"`，思考内容会是空串——不显式要 `summarized`，界面上就看不到任何思考文字；而**要关掉思考就写 `{type: "disabled"}`**。

## 使用

```bash
qicode                 # 默认配置，直接进对话（用配置里第一个 provider）
qicode -c my.yaml      # 指定配置文件
qicode -p my-ollama    # 指定要用哪个 provider（默认就是第一个）
qicode --debug         # 出错时打印完整堆栈
```

进入交互界面后：

- 直接输入文字 → 发给模型，流式回复。
- `Esc` → 生成中立即打断（已打印部分保留）。
- 输入态按上/下键 → 翻阅本会话已输入的语句。
- 斜杠命令：
  - `/clear` — 清空对话历史，重新开始（后端不变）
  - `/model` — 弹出选择面板换模型（方向键选、回车生效、Esc 取消）；`/model <name>` 直接切换
  - `/exit` — 退出
- 退出快捷键：`Ctrl+D`（直接退出）、`Ctrl+C`（输入态连按两次退出，单次仅清行）。

## 架构

```
cli（启动编排）→ tui（交互/渲染/打断）→ session（对话状态）→ provider（模型接入）
                        config（配置加载）被 cli / provider 使用
```

- **provider** 是唯一知道「HTTP 长什么样」的层：一个 `ChatProvider` 接口 + 两个协议适配器，`create_provider` 工厂按 `protocol` 字段分发。**加新协议 = 新适配器文件 + 工厂表里加一行**，上面三层零改动。
- **session** 保管对话历史与当前后端，思考内容不进历史（不参与回传）。
- **tui** 只管用户可见的东西，不碰 HTTP。
- **config** 只产出纯数据（`ProviderConfig`），不含任何通信行为。

设计文档按**阶段**归档在 [`docs/`](docs/README.md)：v1 已归档，当前阶段是 [v2-chat-client](docs/v2-chat-client/)（[需求](docs/v2-chat-client/spec.md) / [设计](docs/v2-chat-client/plan.md) / [任务](docs/v2-chat-client/task.md) / [验收](docs/v2-chat-client/checklist.md)）。

## 开发

```bash
pytest tests/ -v     # 运行单元测试
```

模块单测用 mock 覆盖，不触网；端到端在 tmux 里对真实后端跑（当前后端为本地 Ollama，见 [docs/v1/checklist.md](docs/v1/checklist.md)）。
