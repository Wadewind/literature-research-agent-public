"""ClaimSet Repository 的内存假实现。"""

from literature_agent.application.ports.claim_set_repository import ClaimSetRepository
from literature_agent.domain.evidence import Citation, Claim, ClaimSet


class FakeClaimSetRepository(ClaimSetRepository):
    """不依赖数据库的 ClaimSet Repository 假实现。"""

    def __init__(self) -> None:
        self._claim_sets: dict[str, ClaimSet] = {}
        self._claims: dict[str, Claim] = {}
        self._citations: list[Citation] = []

    async def add_claim_set(self, claim_set: ClaimSet) -> ClaimSet:
        """将 ClaimSet 存入内存。"""
        self._claim_sets[claim_set.claim_set_id] = claim_set
        return claim_set

    async def get_or_add_claim_set(self, claim_set: ClaimSet) -> ClaimSet:
        existing = await self.get_by_run_id(claim_set.run_id)
        if existing is not None:
            return existing
        return await self.add_claim_set(claim_set)

    async def add_claims(self, claims: list[Claim]) -> None:
        """将 Claim 批量存入内存。"""
        for claim in claims:
            self._claims[claim.claim_id] = claim

    async def get_or_add_claims(self, claims: list[Claim]) -> list[Claim]:
        result: list[Claim] = []
        for claim in claims:
            existing = next(
                (
                    item
                    for item in self._claims.values()
                    if item.claim_set_id == claim.claim_set_id
                    and item.sequence == claim.sequence
                ),
                None,
            )
            if existing is None:
                self._claims[claim.claim_id] = claim
                existing = claim
            result.append(existing)
        return result

    async def add_citations(self, citations: list[Citation]) -> None:
        """将 Citation 批量存入内存。"""
        self._citations.extend(citations)

    async def get_or_add_citations(self, citations: list[Citation]) -> list[Citation]:
        for citation in citations:
            if citation not in self._citations:
                self._citations.append(citation)
        return list(citations)

    async def get_by_run_id(self, run_id: str) -> ClaimSet | None:
        """按 Run 返回 ClaimSet；不存在返回 None。"""
        for claim_set in self._claim_sets.values():
            if claim_set.run_id == run_id:
                return claim_set
        return None

    async def list_claims(self, claim_set_id: str) -> list[Claim]:
        """按 ClaimSet 返回 Claim，按 sequence 升序。"""
        result = [c for c in self._claims.values() if c.claim_set_id == claim_set_id]
        result.sort(key=lambda c: c.sequence)
        return result

    async def list_citations(self, claim_id: str) -> list[Citation]:
        """按 Claim 返回 Citation。"""
        return [c for c in self._citations if c.claim_id == claim_id]
