"""集成测试共享 fixtures。"""

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from literature_agent.domain.project import create_project
from literature_agent.infrastructure.persistence.models import Base
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)


@pytest_asyncio.fixture
async def db_engine():
    """启动 Testcontainers PostgreSQL（pgvector 镜像）并创建 schema。"""
    with PostgresContainer("pgvector/pgvector:pg18") as postgres:
        url = postgres.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql+psycopg://"
        )
        engine = create_async_engine(url, echo=False)
        async with engine.begin() as conn:
            # vector 扩展必须先于含 vector 列的建表（与迁移顺序一致）
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
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
