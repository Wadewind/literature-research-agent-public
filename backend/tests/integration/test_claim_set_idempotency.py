import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker

from literature_agent.domain.evidence import AnswerStatus, create_claim, create_claim_set
from literature_agent.domain.project import create_project
from literature_agent.domain.run import RunType, create_run
from literature_agent.infrastructure.persistence.claim_set_repository import (
    SqlalchemyClaimSetRepository,
)
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)


async def test_concurrent_review_claim_set_and_claims_converge(db_engine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        project = create_project("user-1", "Review Claims", "")
        await SqlalchemyProjectRepository(session).add(project)
        run = create_run(project.project_id, "user-1", RunType.REVIEW)
        await SqlalchemyRunRepository(session).add(run)
        await session.commit()

    async def write_bundle():
        async with factory() as session:
            repo = SqlalchemyClaimSetRepository(session)
            claim_set = await repo.get_or_add_claim_set(
                create_claim_set(run.run_id, AnswerStatus.ANSWERED)
            )
            claims = await repo.get_or_add_claims(
                [create_claim(claim_set.claim_set_id, 1, "受 Evidence 支持的结论")]
            )
            await session.commit()
            return claim_set, claims[0]

    first, second = await asyncio.gather(write_bundle(), write_bundle())

    assert first[0].claim_set_id == second[0].claim_set_id
    assert first[1].claim_id == second[1].claim_id
    assert first[1].text == second[1].text == "受 Evidence 支持的结论"


async def test_claim_get_or_add_exposes_semantic_conflict(session, project: str) -> None:
    run = create_run(project, "user-1", RunType.REVIEW)
    await SqlalchemyRunRepository(session).add(run)
    await session.flush()
    repo = SqlalchemyClaimSetRepository(session)
    claim_set = await repo.get_or_add_claim_set(
        create_claim_set(run.run_id, AnswerStatus.ANSWERED)
    )
    first = await repo.get_or_add_claims(
        [create_claim(claim_set.claim_set_id, 1, "原始结论")]
    )
    second = await repo.get_or_add_claims(
        [create_claim(claim_set.claim_set_id, 1, "冲突结论")]
    )

    assert first[0].claim_id == second[0].claim_id
    assert second[0].text == "原始结论"
