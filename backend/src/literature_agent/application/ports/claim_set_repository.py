"""ClaimSet Repository 端口。"""

from typing import Protocol

from literature_agent.domain.evidence import Citation, Claim, ClaimSet


class ClaimSetRepository(Protocol):
    """ClaimSet / Claim / Citation 持久化的抽象端口。"""

    async def add_claim_set(self, claim_set: ClaimSet) -> ClaimSet:
        """保存 ClaimSet（``run_id`` 唯一：一个 Run 只提交一个）。"""
        ...

    async def add_claims(self, claims: list[Claim]) -> None:
        """批量保存 Claim（``(claim_set_id, sequence)`` 唯一）。"""
        ...

    async def add_citations(self, citations: list[Citation]) -> None:
        """批量保存 Citation（复合主键去重）。"""
        ...

    async def get_by_run_id(self, run_id: str) -> ClaimSet | None:
        """按 Run 查询 ClaimSet；不存在返回 None。"""
        ...

    async def list_claims(self, claim_set_id: str) -> list[Claim]:
        """按 ClaimSet 查询 Claim，按 ``sequence`` 升序返回。"""
        ...

    async def list_citations(self, claim_id: str) -> list[Citation]:
        """按 Claim 查询 Citation。"""
        ...
