"""ClaimSet Repository 的 PostgreSQL 适配器。"""

from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.claim_set_repository import ClaimSetRepository
from literature_agent.domain.evidence import AnswerStatus, Citation, Claim, ClaimSet
from literature_agent.infrastructure.persistence.models import (
    CitationORM,
    ClaimORM,
    ClaimSetORM,
)


def _claim_set_to_domain(orm: ClaimSetORM) -> ClaimSet:
    """将 ORM 模型转换为领域实体。"""
    return ClaimSet(
        claim_set_id=orm.claim_set_id,
        run_id=orm.run_id,
        answer_status=AnswerStatus(orm.answer_status),
        created_at=orm.created_at,
    )


class SqlalchemyClaimSetRepository(ClaimSetRepository):
    """基于 SQLAlchemy AsyncSession 的 ClaimSetRepository 实现。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Repository。

        参数:
            session: 当前异步数据库会话。
        """
        self._session = session

    async def add_claim_set(self, claim_set: ClaimSet) -> ClaimSet:
        """保存 ClaimSet。"""
        self._session.add(
            ClaimSetORM(
                claim_set_id=claim_set.claim_set_id,
                run_id=claim_set.run_id,
                answer_status=claim_set.answer_status.value,
                created_at=claim_set.created_at,
            )
        )
        return claim_set

    async def get_or_add_claim_set(self, claim_set: ClaimSet) -> ClaimSet:
        """按 Run 唯一键收敛并发创建。"""
        await self._session.execute(
            postgresql.insert(ClaimSetORM)
            .values(
                claim_set_id=claim_set.claim_set_id,
                run_id=claim_set.run_id,
                answer_status=claim_set.answer_status.value,
                created_at=claim_set.created_at,
            )
            .on_conflict_do_nothing(index_elements=[ClaimSetORM.run_id])
        )
        persisted = await self.get_by_run_id(claim_set.run_id)
        if persisted is None:  # pragma: no cover - INSERT/SELECT 同事务防御
            raise RuntimeError("ClaimSet 幂等写入后无法回读")
        return persisted

    async def add_claims(self, claims: list[Claim]) -> None:
        """批量保存 Claim。"""
        self._session.add_all(
            [
                ClaimORM(
                    claim_id=claim.claim_id,
                    claim_set_id=claim.claim_set_id,
                    sequence=claim.sequence,
                    text=claim.text,
                )
                for claim in claims
            ]
        )

    async def get_or_add_claims(self, claims: list[Claim]) -> list[Claim]:
        """按 ClaimSet/sequence 收敛并发，并返回数据库赢家的 Claim ID。"""
        if not claims:
            return []
        claim_set_ids = {item.claim_set_id for item in claims}
        if len(claim_set_ids) != 1:
            raise ValueError("Claim 批量幂等写入只能属于同一 ClaimSet")
        await self._session.execute(
            postgresql.insert(ClaimORM)
            .values(
                [
                    {
                        "claim_id": item.claim_id,
                        "claim_set_id": item.claim_set_id,
                        "sequence": item.sequence,
                        "text": item.text,
                    }
                    for item in claims
                ]
            )
            .on_conflict_do_nothing(index_elements=[ClaimORM.claim_set_id, ClaimORM.sequence])
        )
        persisted = await self.list_claims(claims[0].claim_set_id)
        by_sequence = {item.sequence: item for item in persisted}
        return [by_sequence[item.sequence] for item in claims]

    async def add_citations(self, citations: list[Citation]) -> None:
        """批量保存 Citation。"""
        self._session.add_all(
            [CitationORM(claim_id=c.claim_id, evidence_id=c.evidence_id) for c in citations]
        )

    async def get_or_add_citations(self, citations: list[Citation]) -> list[Citation]:
        """按 Citation 复合主键收敛重复写入。"""
        if not citations:
            return []
        await self._session.execute(
            postgresql.insert(CitationORM)
            .values(
                [{"claim_id": item.claim_id, "evidence_id": item.evidence_id} for item in citations]
            )
            .on_conflict_do_nothing(index_elements=[CitationORM.claim_id, CitationORM.evidence_id])
        )
        result: list[Citation] = []
        for claim_id in dict.fromkeys(item.claim_id for item in citations):
            result.extend(await self.list_citations(claim_id))
        expected = {(item.claim_id, item.evidence_id) for item in citations}
        return [item for item in result if (item.claim_id, item.evidence_id) in expected]

    async def get_by_run_id(self, run_id: str) -> ClaimSet | None:
        """按 Run 查询 ClaimSet；不存在返回 None。"""
        result = await self._session.execute(
            select(ClaimSetORM).where(ClaimSetORM.run_id == run_id),
        )
        orm = result.scalar_one_or_none()
        return _claim_set_to_domain(orm) if orm is not None else None

    async def list_claims(self, claim_set_id: str) -> list[Claim]:
        """按 ClaimSet 查询 Claim，按 sequence 升序返回。"""
        result = await self._session.execute(
            select(ClaimORM)
            .where(ClaimORM.claim_set_id == claim_set_id)
            .order_by(ClaimORM.sequence),
        )
        return [
            Claim(
                claim_id=row.claim_id,
                claim_set_id=row.claim_set_id,
                sequence=row.sequence,
                text=row.text,
            )
            for row in result.scalars().all()
        ]

    async def list_citations(self, claim_id: str) -> list[Citation]:
        """按 Claim 查询 Citation。"""
        result = await self._session.execute(
            select(CitationORM).where(CitationORM.claim_id == claim_id),
        )
        return [
            Citation(claim_id=row.claim_id, evidence_id=row.evidence_id)
            for row in result.scalars().all()
        ]
