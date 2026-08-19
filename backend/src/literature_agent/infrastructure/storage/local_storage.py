"""本地文件系统存储适配器。"""

from pathlib import Path

from literature_agent.application.ports.storage import Storage, StorageError


class LocalFileStorage(Storage):
    """将文件持久化到本地文件系统的 Storage 适配器。

    key 被映射为 storage_root 下的相对路径。实现会阻止 ``..`` 和绝对路径，
    但调用方仍应使用系统生成的 key，不直接使用用户输入。
    """

    def __init__(self, storage_root: str) -> None:
        """初始化本地存储。

        参数:
            storage_root: 本地存储根目录。
        """
        self._root = Path(storage_root).resolve()

    def _resolve(self, key: str) -> Path:
        """将 key 解析为绝对路径并校验不越界。"""
        if not key:
            raise StorageError("存储 key 不能为空")
        if ".." in key.split("/"):
            raise StorageError(f"存储 key 包含路径穿越: {key}")
        resolved = (self._root / key).resolve()
        # 确保解析后的路径仍在根目录下
        if self._root not in resolved.parents and resolved != self._root:
            raise StorageError(f"存储 key 越界: {key}")
        return resolved

    async def write(self, key: str, content: bytes) -> None:
        """写入文件到本地存储。"""
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_bytes(content)
        except OSError as exc:
            raise StorageError(f"写入存储失败: {exc}") from exc

    async def read(self, key: str) -> bytes:
        """从本地存储读取文件。"""
        path = self._resolve(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise StorageError(f"存储 key 不存在: {key}") from exc
        except OSError as exc:
            raise StorageError(f"读取存储失败: {exc}") from exc
