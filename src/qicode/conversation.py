"""单会话的多轮历史（F6）。

只做一件事：按顺序攒 user / assistant / tool 回合，需要时给出一份完整历史的副本。
不持久化、不裁剪、不做角色校验——v2 要的是「进程内多轮上下文完整，且工具回合
按协议格式入历史」这一条。

**写历史这件事只有 agent 做**（`docs/v2/plan.md` 技术决策「历史由谁写」）。v1 里是
TUI 在收尾时 `add_assistant`；v2 一轮里有 preamble / tool_results / final 三条要按
各自的协议格式入历史，散在渲染层必然写乱。一处写、一处读，边界才干净。
"""

from qicode.llm import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    ROLE_USER,
    Message,
    ToolCall,
    ToolResult,
)


class Conversation:
    """一个会话的消息列表。

    刻意做得很薄：不碰网络、不管渲染。它唯一的上游是用户输入和模型回复，
    唯一的下游是 `Provider.stream()`。
    """

    def __init__(self) -> None:
        # 私有 list 存着，外面只能通过下面几个方法进出。
        self._messages: list[Message] = []

    def add_user(self, text: str) -> None:
        """追加一条用户消息。"""
        self._messages.append(Message(role=ROLE_USER, content=text))

    def add_assistant(self, text: str) -> None:
        """追加一条助手回复。

        调用时机是**整轮流式结束之后**：流式过程中内容还在界面的缓冲里滚，
        只有收到 done、渲染定型了，才作为一条完整消息进历史。
        这样下一轮的上下文里不会混进半截回复。
        """
        self._messages.append(Message(role=ROLE_ASSISTANT, content=text))

    def add_assistant_with_tool_calls(self, text: str, calls: list[ToolCall]) -> None:
        """追加一条「模型要求调用工具」的助手回合。

        `text` 是模型调工具之前说的话（preamble），可能为空——它完全可以一句话不说
        直接开调。这个回合在两条协议里都要**原样留着**：下一轮请求必须把它连同
        `tool_calls` 一起发回去，否则工具结果就没有对应的发起方，服务端会直接拒绝
        （结果和调用是靠 id 配对的，缺一半等于对不上账）。

        `list(calls)` 拷一份：外面那份列表若被改动，历史会跟着变，而历史是后续每一轮
        的上下文，被悄悄改掉排查起来极难（同 `messages()` 的理由）。
        """
        self._messages.append(
            Message(role=ROLE_ASSISTANT, content=text, tool_calls=list(calls))
        )

    def add_tool_results(self, results: list[ToolResult]) -> None:
        """追加一条工具执行结果回合。

        一批工具调用的结果放在**同一条** `Message` 里（`tool_results` 是个列表）：
        它们本来就是同一次请求里发出的，协议也按「一批」来配对。拆成 N 条反而要
        在适配器里再合回去。
        """
        self._messages.append(Message(role=ROLE_TOOL, tool_results=list(results)))

    def messages(self) -> list[Message]:
        """给出完整历史的一份**副本**。

        必须是副本，不能直接 `return self._messages`。

        主要理由是**封装**：这份列表要交给适配器转成 SDK 入参，调用方顺手在
        上面加工是很自然的事。历史是后续每一轮的上下文，被外部悄悄改掉的话，
        排查起来极难。

        顺带也挡住一类隐患：万一有人拿着返回的列表**跨 `await` 迭代**，同时
        又有 `add_user` 往里追加——那就落到「迭代中修改集合」这个坑里了。

        这里的行为值得记一笔，因为它和直觉相反：**list 不会报错**。
        CPython 的 list 迭代器只比对下标和当前长度，追加元素它根本察觉不到，
        于是永远追不到末尾——**闷头转成死循环**。（dict 才是当场抛
        `RuntimeError: dictionary changed size during iteration`。）
        实测：边迭代边追加，跑满 1001 次人工上限仍未结束，也没抛任何异常。
        静默挂死比抛异常难查得多，所以这条隐患值得一并挡在门外。

        注意只复制**列表**、不复制里面的 `Message`：`Message` 是 dataclass，
        字段理论上是可变的。但没有任何代码会去改它，为这点可能性付深拷贝的
        开销（每轮都要拷一遍完整历史）不划算。
        """
        return list(self._messages)
