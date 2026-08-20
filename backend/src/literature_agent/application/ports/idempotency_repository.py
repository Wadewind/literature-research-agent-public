"""IdempotencyKey Repository 端口。"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """幂等键记录。

    属性:
        owner_id: 所有者标识符。
        idempotency_key: 调用方提供的幂等键。
        project_id: 请求所属 Project 标识符。
        request_hash: 请求指纹，用于检测同一 key 的不同请求。
        run_id: 关联的 Run 标识符；复用已完成 Version 时可空。
    """

    owner_id: str
    idempotency_key: str
    project_id: str
    request_hash: str
    run_id: str | None
    paper_id: str = ""
    version_id: str = ""
    status: str = "queued"
    reused: bool = False
    already_added: bool = False


class IdempotencyRepository(Protocol):
    """幂等键持久化的抽象端口。"""

    async def get(self, owner_id: str, idempotency_key: str) -> IdempotencyRecord | None:
        """按 owner_id 和 idempotency_key 查询记录。"""
        ...

    async def add(self, record: IdempotencyRecord) -> IdempotencyRecord:
        """保存幂等键记录。

        同一 owner_id + idempotency_key 已存在时应由底层唯一约束抛出异常。
        """
        ...
