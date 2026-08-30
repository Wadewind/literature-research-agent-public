"""SQLAlchemy 异步数据库连接管理。"""

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from literature_agent.infrastructure.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """根据配置创建异步数据库引擎。"""
    return create_async_engine(
        settings.database_url,
        echo=settings.database_echo,
        future=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    """创建异步会话工厂。"""
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
