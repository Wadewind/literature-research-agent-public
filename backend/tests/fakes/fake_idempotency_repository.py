"""Idempotency Repository 的内存假实现。"""

from literature_agent.application.ports.idempotency_repository import (
    IdempotencyRecord,
    IdempotencyRepository,
)


class FakeIdempotencyRepository(IdempotencyRepository):
    """不依赖数据库的 Idempotency Repository 假实现。"""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], IdempotencyRecord] = {}

    async def get(self, owner_id: str, idempotency_key: str) -> IdempotencyRecord | None:
        """根据 owner_id 和 idempotency_key 返回记录。"""
        return self._records.get((owner_id, idempotency_key))

    async def add(self, record: IdempotencyRecord) -> IdempotencyRecord:
        """保存幂等键记录。"""
        key = (record.owner_id, record.idempotency_key)
        if key in self._records:
            raise ValueError(f"Idempotency key 已存在: {record.idempotency_key}")
        self._records[key] = record
        return record
