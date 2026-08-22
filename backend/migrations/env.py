"""Alembic 迁移环境配置，使用异步 psycopg 引擎。"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from literature_agent.infrastructure.config import Settings
from literature_agent.infrastructure.persistence.models import Base

config = context.config
settings = Settings.from_env()

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# LangGraph 官方 raw-psycopg Adapter 直接拥有这些表，没有 SQLAlchemy ORM 映射。
# 它们仍由手写 Alembic revision 管理；排除 autogenerate 可避免把合法表误报为待删除。
_LANGGRAPH_CHECKPOINT_TABLES = {
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
}


def include_object(object_, name: str | None, type_: str, reflected: bool, compare_to):
    """从 autogenerate 排除由官方 Checkpointer 契约管理的 raw 表及索引。"""
    if type_ == "table" and name in _LANGGRAPH_CHECKPOINT_TABLES:
        return False
    return not (
        type_ == "index"
        and getattr(object_, "table", None) is not None
        and object_.table.name in _LANGGRAPH_CHECKPOINT_TABLES
    )


def run_migrations_offline() -> None:
    """以离线模式运行迁移。"""
    url = settings.database_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """在同步连接包装器内运行迁移。"""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """创建异步引擎并运行迁移。"""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = settings.database_url
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """以在线模式运行迁移。"""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
