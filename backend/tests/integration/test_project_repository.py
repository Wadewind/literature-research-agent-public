"""Project PostgreSQL Repository 集成测试。"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from literature_agent.domain.project import create_project
from literature_agent.infrastructure.persistence.models import Base
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)


@pytest_asyncio.fixture
async def db_engine():
    """启动 Testcontainers PostgreSQL 并创建 schema。"""
    with PostgresContainer("postgres:18") as postgres:
        url = postgres.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql+psycopg://"
        )
        engine = create_async_engine(url, echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield engine
        await engine.dispose()


@pytest_asyncio.fixture
async def session(db_engine):
    """提供已开启事务的异步会话，测试结束后回滚。"""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def repo(session: AsyncSession):
    """提供基于真实会话的 Repository。"""
    return SqlalchemyProjectRepository(session)


@pytest.mark.asyncio
async def test_add_and_get_project(
    repo: SqlalchemyProjectRepository,
    session: AsyncSession,
) -> None:
    """保存 Project 后应能按 ID 查询到。"""
    project = create_project(owner_id="user-1", name="测试项目", description="说明")

    await repo.add(project)
    await session.commit()

    fetched = await repo.get_by_id(project.project_id)
    assert fetched is not None
    assert fetched.project_id == project.project_id
    assert fetched.owner_id == "user-1"
    assert fetched.name == "测试项目"


@pytest.mark.asyncio
async def test_list_by_owner_isolation(
    repo: SqlalchemyProjectRepository,
    session: AsyncSession,
) -> None:
    """list_by_owner 只返回指定所有者的 Project。"""
    project_a = create_project(owner_id="user-a", name="A 项目", description="")
    project_b = create_project(owner_id="user-b", name="B 项目", description="")
    await repo.add(project_a)
    await repo.add(project_b)
    await session.commit()

    results = await repo.list_by_owner("user-a")

    assert len(results) == 1
    assert results[0].owner_id == "user-a"
    assert results[0].name == "A 项目"


@pytest.mark.asyncio
async def test_get_by_id_returns_none_for_missing(repo: SqlalchemyProjectRepository) -> None:
    """查询不存在的 Project 返回 None。"""
    fetched = await repo.get_by_id("00000000-0000-0000-0000-000000000000")

    assert fetched is None
