"""ClaimSet Repository 的 PostgreSQL 适配器。"""

from sqlalchemy import select
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

    async def add_citations(self, citations: list[Citation]) -> None:
        """批量保存 Citation。"""
        self._session.add_all(
            [
                CitationORM(claim_id=c.claim_id, evidence_id=c.evidence_id)
                for c in citations
            ]
        )

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
