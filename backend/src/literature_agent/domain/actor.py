"""可信 Actor Context 值对象。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ActorContext:
    """表示当前请求的可信用户上下文。

    首版只包含 ``owner_id``，用于 Project 等资源的 ownership 校验。
    身份由可替换的依赖提供，请求体不能声明所有者。

    属性:
        owner_id: 稳定的用户标识符。
    """

    owner_id: str
