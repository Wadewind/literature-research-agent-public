"""Storage 的内存假实现。"""

from literature_agent.application.ports.storage import Storage, StorageError


class FakeStorage(Storage):
    """不依赖文件系统的 Storage 假实现。"""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    async def write(self, key: str, content: bytes) -> None:
        """将内容存入内存。"""
        self._objects[key] = content

    async def read(self, key: str) -> bytes:
        """从内存读取内容。"""
        if key not in self._objects:
            raise StorageError(f"key 不存在: {key}")
        return self._objects[key]
