"""IdempotencyKey PostgreSQL Repository 集成测试。"""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.application.ports.idempotency_repository import IdempotencyRecord
from literature_agent.domain.run import create_run
from literature_agent.infrastructure.persistence.idempotency_repository import (
    SqlalchemyIdempotencyRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)


@pytest.fixture
def repo(session: AsyncSession) -> SqlalchemyIdempotencyRepository:
    """提供基于真实会话的 Repository。"""
    return SqlalchemyIdempotencyRepository(session)


@pytest.fixture
def run_repo(session: AsyncSession) -> SqlalchemyRunRepository:
    """提供基于真实会话的 Run Repository。"""
    return SqlalchemyRunRepository(session)


@pytest.mark.asyncio
async def test_add_and_get_record(
    repo: SqlalchemyIdempotencyRepository,
    run_repo: SqlalchemyRunRepository,
    session: AsyncSession,
    project: str,
) -> None:
    """保存并查询幂等键记录。"""
    run = create_run(
        project_id=project,
        owner_id="user-1",
        run_type="ingestion",
    )
    await run_repo.add(run)
    await session.flush()

    record = IdempotencyRecord(
        owner_id="user-1",
        idempotency_key="key-1",
        project_id=project,
        request_hash="hash",
        run_id=run.run_id,
    )
    await repo.add(record)
    await session.commit()

    fetched = await repo.get("user-1", "key-1")

    assert fetched is not None
    assert fetched.run_id == run.run_id


@pytest.mark.asyncio
async def test_duplicate_key_raises_integrity_error(
    repo: SqlalchemyIdempotencyRepository,
    run_repo: SqlalchemyRunRepository,
    session: AsyncSession,
    project: str,
) -> None:
    """相同 owner_id + idempotency_key 应违反唯一约束。"""
    run = create_run(
        project_id=project,
        owner_id="user-1",
        run_type="ingestion",
    )
    await run_repo.add(run)
    await session.flush()

    record = IdempotencyRecord(
        owner_id="user-1",
        idempotency_key="key-2",
        project_id=project,
        request_hash="hash",
        run_id=run.run_id,
    )
    await repo.add(record)
    await session.flush()

    duplicate = IdempotencyRecord(
        owner_id="user-1",
        idempotency_key="key-2",
        project_id=project,
        request_hash="other",
        run_id=run.run_id,
    )
    with pytest.raises(IntegrityError):
        await repo.add(duplicate)
