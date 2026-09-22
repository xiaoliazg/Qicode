# Qicode 文档索引

本目录按**阶段**归档。每个阶段用同一套四份文档，逐份细化：

```
spec.md（做什么）→ plan.md（怎么做）→ task.md（按什么顺序做）→ checklist.md（做对了没）
```

| 文档 | 回答什么 | 内容 |
|------|----------|------|
| `spec.md` | **做什么** | 背景、目标、功能需求（F）、非功能需求（N）、边界、验收标准（AC） |
| `plan.md` | **怎么做** | 架构概览、核心数据结构与接口、模块设计、模块交互、文件组织、技术决策 |
| `task.md` | **按什么顺序做** | 文件清单、有序任务（T 编号）、每步的步骤与验证方式 |
| `checklist.md` | **做对了没** | 可观测的行为检查、集成检查、编译与测试、端到端场景 |

## 存放约定

- 每个阶段一个目录：`docs/<阶段名>/`，内含上述四份文档。
- 阶段名取「版本号 + 主题」，如 `v2-chat-client`。
- **根目录不放文档**——所有文档一律进 `docs/`，由本文件索引。

## 阶段

| 阶段 | 主题 | 技术栈 | 状态 | 文档 |
|------|------|--------|------|------|
| v1 | 对话基座 | prompt_toolkit + 同步 + threading | 已归档 | [v1/](v1/) |
| **v2-chat-client** | 多协议 LLM 终端对话客户端 | **Textual + async-first** | **待实施** | [v2-chat-client/](v2-chat-client/) |

### 关于 v1（已归档）

`v1/` 是第一版对话基座的文档，代码即仓库里 `qicode/` 现有实现的主体。

它的技术栈是 prompt_toolkit + 同步 + threading，与 v2 定的 Textual + async-first 是两套
东西。v1 里记录的东西仍然有效，值得在实施 v2 前翻一翻：

- **实测证据的做法**——每个验收条目都附「怎么验的、看到了什么」（如 20 秒内 283 次内容
  变化、DeepSeek 同题 187 个 vs 0 个思考字符的受控对比、抓转义序列核对灰斜体）。
- **踩过的坑**——尤其：Anthropic 侧 `thinking` 用固定预算写法 `{type: enabled,
  budget_tokens: N}` 在当前 Claude 模型上会直接 400；`display` 缺省为 `omitted`，不显式
  写 `summarized` 就看不到思考文字。v2 的 `plan.md` 里写的是 `budget_tokens` 那种形状，
  接官方 Claude 时需要按这里的记录调整。
- **终端行为上的坑**——`shutil.get_terminal_size()` 在 TUI 接管 stdin 后会退回 (80, 24)
  兜底值；底栏与输入行在 prompt_toolkit 里凑不到一起。这些在 Textual 下多半不复现，但先
  知道有这类问题不是坏事。
