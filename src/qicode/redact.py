"""把可能夹带了密钥的文本抹干净再往外显示。

**为什么需要这一层。** 适配器把上游的异常原样翻成 `err` 事件交给界面（F11），
而 `str(exc)` 是**别人写的字符串**——SDK 也好、网关也好、反向代理也好。
它们完全可能把请求里的凭据抄进错误信息里。实测就撞到过一次：拿错 key 打
DeepSeek，回来的错误里带着密钥的后四位（`... your api key: ****rong ...`）。
那次是服务端自己掩了码，但这不是我们能担保的事——换一家上游，抄的可能就是全 key。

而这句话的落点是**用户屏幕上的一块可见内容**，截屏、录屏、共享屏幕都带得走。
（失败的那一轮不进 `Conversation`，所以它不会被 `cli._replay_transcript` 回放到
终端回滚缓冲——但「显示在屏幕上」这一条已经足够构成理由了：一次都不该出现。）

所以：凡是拿到 `str(exc)` 要往外显示的地方，先过一遍这里。

**反过来也要小心：抹过头同样是 bug。** 一个 api_key 完全可以合法地是个短占位符
（本地 Ollama 常写 `ollama`），无差别替换会把正常的排障信息毁掉。取舍写在
`MIN_SECRET_LENGTH` 那儿。
"""

from collections.abc import Iterable

#: 替换成什么。不保留任何原字符——保留前缀看着友好，但前缀往往也带信息
#: （密钥的家族、环境），而且长度本身就能当线索用。
MASK = "***"

#: 短于这个长度的「密钥」不抹。
#:
#: 这是个有代价的取舍，写清楚免得以后被当成 bug：
#: `api_key` 在很多配置里根本不是密钥，是占位符——本地 Ollama 就常写
#: `ollama`、写 `none`、写 `x`。无差别替换的话，一句再普通不过的
#: 「cannot reach ollama server」会被抹成「cannot reach *** server」，
#: 把有用的排障信息一起毁了。真实的密钥（`sk-` 那一类）都在 20 字符以上，
#: 8 这个门槛把它们全都覆盖到了，同时放过了那些一眼是占位符的短串。
#:
#: 反过来说：**长度不到 8 的真密钥不会被抹**。那种密钥也谈不上什么秘密
#: （暴力枚举的空间太小），这里选择保住错误信息的可读性。
MIN_SECRET_LENGTH = 8


def redact(text: str, secrets: Iterable[str]) -> str:
    """把 `text` 里出现的每一个 `secrets` 换成 `MASK`。

    空串由上面那道长度关一并挡掉——`str.replace("", ...)` 会在每两个字符之间
    插一刀，把整段文本搅碎。配置层已经拦过空 api_key，但这是公开函数，
    别指望调用方一定干净。
    """
    for secret in secrets:
        if len(secret) < MIN_SECRET_LENGTH:
            continue
        text = text.replace(secret, MASK)
    return text
