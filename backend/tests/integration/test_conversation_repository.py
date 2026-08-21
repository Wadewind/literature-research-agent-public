"""Conversation/Message Repository 的 PostgreSQL 集成测试（切片 8）。

验证三张新表（conversations / conversation_scope_papers / messages）的
往返、唯一约束与单活跃 Run 条件更新在真实数据库上的语义。
"""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from literature_agent.domain.conversation import (
    MessageRole,
    ScopeMode,
    create_conversation,
    create_message,
    create_scope_paper,
)
from literature_agent.domain.evidence import AnswerStatus, create_claim_set
from literature_agent.domain.run import RunType, create_run
from literature_agent.infrastructure.persistence.claim_set_repository import (
    SqlalchemyClaimSetRepository,
)
from literature_agent.infrastructure.persistence.conversation_repository import (
    SqlalchemyConversationRepository,
)
from literature_agent.infrastructure.persistence.message_repository import (
    SqlalchemyMessageRepository,
)
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)


async def _add_run(session, project_id: str, owner_id: str = "user-1") -> str:
    """创建一个 rag_answer Run 并返回 run_id（active_run_id 的 FK 目标）。"""
    run = create_run(
        project_id=project_id,
        owner_id=owner_id,
        run_type=RunType.RAG_ANSWER,
        input_payload={},
    )
    await SqlalchemyRunRepository(session).add(run)
    await session.flush()
    return run.run_id


async def test_conversation_round_trip(session, project: str) -> None:
    """Conversation 存取往返：字段完整保留。"""
    repo = SqlalchemyConversationRepository(session)
    conversation = create_conversation(
        project_id=project,
        owner_id="user-1",
        title="往返测试",
        scope_mode=ScopeMode.PROJECT,
    )
    await repo.add(conversation)
    await session.commit()

    loaded = await repo.get_by_id(conversation.conversation_id)

    assert loaded is not None
    assert loaded.project_id == project
    assert loaded.owner_id == "user-1"
    assert loaded.title == "往返测试"
    assert loaded.scope_mode is ScopeMode.PROJECT
    assert loaded.active_run_id is None


async def test_list_by_project_orders_by_created_at(session, project: str) -> None:
    """list_by_project 按创建时间升序返回。"""
    repo = SqlalchemyConversationRepository(session)
    first = create_conversation(
        project_id=project, owner_id="user-1", title="一", scope_mode=ScopeMode.PROJECT
    )
    second = create_conversation(
        project_id=project, owner_id="user-1", title="二", scope_mode=ScopeMode.PROJECT
    )
    await repo.add(first)
    await repo.add(second)
    await session.commit()

    conversations = await repo.list_by_project(project)

    assert [c.conversation_id for c in conversations] == [
        first.conversation_id,
        second.conversation_id,
    ]


async def test_scope_papers_round_trip_and_composite_pk(session, project: str) -> None:
    """默认范围条目往返；重复 (conversation_id, paper_id) 违反复合主键。"""
    repo = SqlalchemyConversationRepository(session)
    conversation = create_conversation(
        project_id=project,
        owner_id="user-1",
        title=None,
        scope_mode=ScopeMode.SELECTED_PAPERS,
    )
    await repo.add(conversation)
    entries = [
        create_scope_paper(conversation.conversation_id, "paper-1", "v-1"),
        create_scope_paper(conversation.conversation_id, "paper-2", "v-2"),
    ]
    await repo.add_scope_papers(entries)
    await session.commit()

    loaded = await repo.list_scope_papers(conversation.conversation_id)
    assert {(e.paper_id, e.version_id) for e in loaded} == {
        ("paper-1", "v-1"),
        ("paper-2", "v-2"),
    }

    await repo.add_scope_papers(
        [create_scope_paper(conversation.conversation_id, "paper-1", "v-9")]
    )
    with pytest.raises(Exception, match="conversation_scope_papers"):
        await session.commit()


async def test_message_unique_sequence_per_conversation(session, project: str) -> None:
    """messages 的 (conversation_id, sequence) 唯一约束生效。"""
    repo = SqlalchemyConversationRepository(session)
    message_repo = SqlalchemyMessageRepository(session)
    conversation = create_conversation(
        project_id=project, owner_id="user-1", title=None, scope_mode=ScopeMode.PROJECT
    )
    await repo.add(conversation)
    await message_repo.add(
        create_message(
            conversation_id=conversation.conversation_id,
            sequence=1,
            role=MessageRole.USER,
            content="第一条",
        )
    )
    await session.commit()

    await message_repo.add(
        create_message(
            conversation_id=conversation.conversation_id,
            sequence=1,
            role=MessageRole.USER,
            content="重复序号",
        )
    )
    with pytest.raises(Exception, match="messages"):
        await session.commit()


async def test_message_queries(session, project: str) -> None:
    """list_by_conversation 升序、get_by_run_and_role、max_sequence。"""
    repo = SqlalchemyConversationRepository(session)
    message_repo = SqlalchemyMessageRepository(session)
    conversation = create_conversation(
        project_id=project, owner_id="user-1", title=None, scope_mode=ScopeMode.PROJECT
    )
    await repo.add(conversation)
    run_id = await _add_run(session, project)
    # claim_set_id 有外键约束：先落真实 ClaimSet
    claim_set = create_claim_set(run_id, AnswerStatus.ANSWERED)
    await SqlalchemyClaimSetRepository(session).add_claim_set(claim_set)
    await message_repo.add(
        create_message(
            conversation_id=conversation.conversation_id,
            sequence=1,
            role=MessageRole.USER,
            content="问题",
            run_id=run_id,
        )
    )
    await message_repo.add(
        create_message(
            conversation_id=conversation.conversation_id,
            sequence=2,
            role=MessageRole.ASSISTANT,
            content="回答",
            run_id=run_id,
            claim_set_id=claim_set.claim_set_id,
        )
    )
    await session.commit()

    messages = await message_repo.list_by_conversation(conversation.conversation_id)
    assert [m.sequence for m in messages] == [1, 2]
    assert [m.role for m in messages] == [MessageRole.USER, MessageRole.ASSISTANT]
    assistant = await message_repo.get_by_run_and_role(run_id, MessageRole.ASSISTANT)
    assert assistant is not None
    assert assistant.content == "回答"
    assert assistant.claim_set_id == claim_set.claim_set_id
    assert await message_repo.max_sequence(conversation.conversation_id) == 2
    assert await message_repo.max_sequence("conv-not-exist") == 0


async def test_try_claim_and_release_active_run(session, project: str) -> None:
    """认领/清理语义：重复认领失败；expected_run_id 不匹配时清理不生效。"""
    repo = SqlalchemyConversationRepository(session)
    conversation = create_conversation(
        project_id=project, owner_id="user-1", title=None, scope_mode=ScopeMode.PROJECT
    )
    await repo.add(conversation)
    run_a = await _add_run(session, project)
    run_b = await _add_run(session, project)
    await session.commit()

    assert await repo.try_claim_active_run(conversation.conversation_id, run_a)
    await session.commit()
    # 已有活跃 Run：再次认领失败
    assert not await repo.try_claim_active_run(conversation.conversation_id, run_b)
    # expected 不匹配：清理不生效
    assert not await repo.release_active_run(
        conversation.conversation_id, expected_run_id=run_b
    )
    # expected 匹配：清理成功，可重新认领
    assert await repo.release_active_run(
        conversation.conversation_id, expected_run_id=run_a
    )
    await session.commit()
    loaded = await repo.get_by_id(conversation.conversation_id)
    assert loaded is not None
    assert loaded.active_run_id is None
    assert await repo.try_claim_active_run(conversation.conversation_id, run_b)


@pytest.mark.asyncio
async def test_concurrent_try_claim_only_one_succeeds(db_engine, project) -> None:
    """并发认领：两个会话同时 try_claim，恰好一个成功。"""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    conversation_id: str | None = None
    run_ids: list[str] = []
    async with factory() as setup_session:
        repo = SqlalchemyConversationRepository(setup_session)
        conversation = create_conversation(
            project_id=project,
            owner_id="user-1",
            title=None,
            scope_mode=ScopeMode.PROJECT,
        )
        await repo.add(conversation)
        conversation_id = conversation.conversation_id
        run_ids = [
            await _add_run(setup_session, project),
            await _add_run(setup_session, project),
        ]
        await setup_session.commit()

    async def attempt_claim(run_id: str) -> bool:
        """独立会话尝试认领活跃 Run。"""
        async with factory() as session:
            claimed = await SqlalchemyConversationRepository(
                session
            ).try_claim_active_run(conversation_id, run_id)
            await session.commit()
            return claimed

    results = await asyncio.gather(*(attempt_claim(r) for r in run_ids))

    assert sorted(results) == [False, True]
    async with factory() as session:
        loaded = await SqlalchemyConversationRepository(session).get_by_id(
            conversation_id
        )
        assert loaded is not None
        assert loaded.active_run_id in run_ids


async def test_set_title_if_null(session, project: str) -> None:
    """标题回填：仅 null 时生效，已有标题不被覆盖。"""
    repo = SqlalchemyConversationRepository(session)
    conversation = create_conversation(
        project_id=project, owner_id="user-1", title=None, scope_mode=ScopeMode.PROJECT
    )
    await repo.add(conversation)
    await session.commit()

    await repo.set_title_if_null(conversation.conversation_id, "问题摘要")
    await session.commit()
    loaded = await repo.get_by_id(conversation.conversation_id)
    assert loaded is not None
    assert loaded.title == "问题摘要"

    await repo.set_title_if_null(conversation.conversation_id, "覆盖尝试")
    await session.commit()
    loaded = await repo.get_by_id(conversation.conversation_id)
    assert loaded is not None
    assert loaded.title == "问题摘要"
