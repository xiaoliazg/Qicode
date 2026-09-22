# Qicode

我正在构建一个终端 AI 编程助手（类似 Claude Code），项目名叫 Qicode，使用 Python 实现。

## 语言
中文回答，中文注释。

## 测试

开发完功能后，用 tmux 做端到端测试：

1. 在 tmux 中启动 Qicode
2. 输入一段真实的对话请求
3. 观察 Qicode 是否正确调用工具、生成回复
4. 对照当前阶段的 checklist.md 逐项验收

## 文档

按阶段归档，每个阶段一套四份（spec → plan → task → checklist），放在 `docs/<阶段名>/`：

- 阶段名取「版本号 + 主题」，如 `docs/v2-chat-client/`
- **根目录不放文档**，一律进 `docs/`
- 代码注释引用设计文档要写全路径（如 `docs/v2-chat-client/plan.md`）——阶段名在前，裸文件名会有歧义

索引与各阶段状态见 `docs/README.md`。

## 环境
本项目使用conda 虚拟环境 Qicode 进行开发
