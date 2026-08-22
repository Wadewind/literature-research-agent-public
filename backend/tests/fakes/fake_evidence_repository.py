"""Evidence Repository 的内存假实现。"""

from literature_agent.application.ports.evidence_repository import EvidenceRepository
from literature_agent.domain.evidence import Evidence


class FakeEvidenceRepository(EvidenceRepository):
    """不依赖数据库的 Evidence Repository 假实现。

    模拟 ``(run_id, chunk_id)`` 唯一约束的幂等语义由应用层
    （``list_by_run`` 回读）保证，Fake 不主动拒绝重复写入。
    """

    def __init__(self) -> None:
        self._evidence: dict[str, Evidence] = {}

    async def add_many(self, evidence: list[Evidence]) -> None:
        """将 Evidence 批量存入内存。"""
        for item in evidence:
            self._evidence[item.evidence_id] = item

    async def get_or_add_many(self, evidence: list[Evidence]) -> list[Evidence]:
        """按 Run 与 Chunk 复用既有 Evidence。"""
        result: list[Evidence] = []
        for item in evidence:
            existing = next(
                (
                    current
                    for current in self._evidence.values()
                    if current.run_id == item.run_id and current.chunk_id == item.chunk_id
                ),
                None,
            )
            if existing is None:
                self._evidence[item.evidence_id] = item
                existing = item
            result.append(existing)
        return result

    async def list_by_run(self, run_id: str) -> list[Evidence]:
        """按 Run 返回 Evidence，按创建时间升序。"""
        result = [e for e in self._evidence.values() if e.run_id == run_id]
        result.sort(key=lambda e: e.created_at)
        return result

    async def list_by_ids(self, evidence_ids: list[str]) -> list[Evidence]:
        """按 ID 列表返回 Evidence。"""
        return [self._evidence[eid] for eid in evidence_ids if eid in self._evidence]
