"""Model Invocation Repository 的 PostgreSQL 集成测试。"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from literature_agent.domain.model_invocation import (
    InvocationStatus,
    ModelCapability,
    create_model_invocation,
)
from literature_agent.domain.run import create_run
from literature_agent.infrastructure.persistence.model_invocation_repository import (
    SqlalchemyModelInvocationRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)


@pytest.fixture
async def run_id(session: AsyncSession, project: str) -> str:
    """创建一个 Run 并返回其 ID（供 run_id 外键使用）。"""
    run = create_run(project_id=project, owner_id="user-1", run_type="rag_answer")
    await SqlalchemyRunRepository(session).add(run)
    await session.commit()
    return run.run_id


async def test_add_and_list_by_run(session: AsyncSession, run_id: str) -> None:
    """保存成功/失败记录后可按 Run 查询，字段完整往返。"""
    repo = SqlalchemyModelInvocationRepository(session)
    succeeded = create_model_invocation(
        run_id=run_id,
        capability=ModelCapability.EMBEDDING,
        provider="zhipu",
        model="embedding-3",
        status=InvocationStatus.SUCCEEDED,
        latency_ms=120,
        prompt_tokens=11,
    )
    await repo.add(succeeded)
    failed = create_model_invocation(
        run_id=run_id,
        capability=ModelCapability.CHAT,
        provider="deepseek",
        model="deepseek-v4-flash",
        status=InvocationStatus.FAILED,
        latency_ms=2000,
        error_type="ModelRateLimitError",
    )
    await repo.add(failed)
    await session.flush()

    records = await repo.list_by_run(run_id)

    assert len(records) == 2
    first, second = records
    assert first.invocation_id == succeeded.invocation_id
    assert first.capability == ModelCapability.EMBEDDING
    assert first.prompt_tokens == 11
    assert first.completion_tokens is None
    assert first.error_type is None
    assert second.status == InvocationStatus.FAILED
    assert second.error_type == "ModelRateLimitError"
    assert second.prompt_tokens is None


async def test_add_without_run(session: AsyncSession) -> None:
    """run_id 为空的记录可直接保存（执行器未接线场景）。"""
    repo = SqlalchemyModelInvocationRepository(session)
    invocation = create_model_invocation(
        run_id=None,
        capability=ModelCapability.CHAT,
        provider="fake",
        model="fake-chat",
        status=InvocationStatus.SUCCEEDED,
        latency_ms=1,
    )
    await repo.add(invocation)
    await session.flush()


async def test_list_by_run_empty(session: AsyncSession, run_id: str) -> None:
    """无记录的 Run 返回空列表。"""
    repo = SqlalchemyModelInvocationRepository(session)
    assert await repo.list_by_run(run_id) == []
