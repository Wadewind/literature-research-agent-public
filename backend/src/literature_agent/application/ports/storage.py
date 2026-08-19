"""文件存储抽象端口。"""

from typing import Protocol


class Storage(Protocol):
    """内容寻址或键寻址的文件存储抽象。

    实现可以是本地文件系统、对象存储或其他受控存储。
    调用方负责生成不冲突且不含路径穿越的 ``key``。
    """

    async def write(self, key: str, content: bytes) -> None:
        """将内容写入指定 key。

        参数:
            key: 存储键，内部可能映射为路径或对象名。
            content: 待写入的字节内容。

        异常:
            StorageError: 写入失败。
        """
        ...

    async def read(self, key: str) -> bytes:
        """读取指定 key 的内容。

        异常:
            StorageError: 读取失败或 key 不存在。
        """
        ...


class StorageError(Exception):
    """存储操作异常。"""
