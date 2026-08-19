"""集成测试共享 fixtures。"""

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
async def project(session: AsyncSession) -> str:
    """在数据库中创建一个测试 Project 并返回其 ID。"""
    project = create_project(owner_id="user-1", name="测试项目", description="")
    repo = SqlalchemyProjectRepository(session)
    await repo.add(project)
    await session.commit()
    return project.project_id
