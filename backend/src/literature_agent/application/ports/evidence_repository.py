"""Evidence Repository 端口。"""

from typing import Protocol

from literature_agent.domain.evidence import Evidence


class EvidenceRepository(Protocol):
    """Evidence 持久化的抽象端口。"""

    async def add_many(self, evidence: list[Evidence]) -> None:
        """批量固化 Evidence。

        唯一约束 ``(run_id, chunk_id)`` 兜底重复提交；调用方应先经
        ``list_by_run`` 排除已固化的 Chunk（幂等回读）。
        """
        ...

    async def list_by_run(self, run_id: str) -> list[Evidence]:
        """按 Run 查询固化的 Evidence（跨 Run 隔离），按创建顺序返回。"""
        ...

    async def list_by_ids(self, evidence_ids: list[str]) -> list[Evidence]:
        """按 ID 列表查询 Evidence（引用详情与校验加载用）。"""
        ...
