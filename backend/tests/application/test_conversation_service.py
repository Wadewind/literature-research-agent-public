"""ConversationService 应用服务测试（切片 8）。

覆盖：创建会话的 scope 校验全分支、提问提交（幂等键重放、归档/
busy/not_indexed、单活跃 Run 认领与终态自愈、版本范围快照固化、
标题回填）、消息列表的 Claim/Evidence 摘要组装、Evidence 详情授权。
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from literature_agent.application.conversation_service import ConversationService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.chunk import create_chunk_set
from literature_agent.domain.conversation import (
    MessageRole,
    ScopeMode,
    create_message,
)
from literature_agent.domain.evidence import (
    AnswerStatus,
    create_claim,
    create_claim_set,
    create_evidence,
)
from literature_agent.domain.exceptions import (
    ConversationBusyError,
    ConversationNotFoundError,
    EvidenceNotFoundError,
    IdempotencyConflictError,
    InvalidScopeError,
    ProjectArchivedError,
    ProjectNotFoundError,
    ProjectNotIndexedError,
)
from literature_agent.domain.paper import create_paper
from literature_agent.domain.parse_revision import create_parse_revision
from literature_agent.domain.project import create_project
from literature_agent.domain.project_paper import create_project_paper
from literature_agent.domain.run import RunStatus, RunType
from tests.fakes.fake_chunk_set_repository import FakeChunkSetRepository
from tests.fakes.fake_claim_set_repository import FakeClaimSetRepository
from tests.fakes.fake_conversation_repository import FakeConversationRepository
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_evidence_repository import FakeEvidenceRepository
from tests.fakes.fake_idempotency_repository import FakeIdempotencyRepository
from tests.fakes.fake_message_repository import FakeMessageRepository
from tests.fakes.fake_outbox_repository import FakeOutboxRepository
from tests.fakes.fake_paper_repository import FakePaperRepository
from tests.fakes.fake_parse_revision_repository import FakeParseRevisionRepository
from tests.fakes.fake_project_paper_repository import FakeProjectPaperRepository
from tests.fakes.fake_project_repository import FakeProjectRepository, fake_session
from tests.fakes.fake_run_repository import FakeRunRepository

_ACTOR = ActorContext(owner_id="user-1")


@dataclass
class _Fakes:
    """一套共享的 Fake Repository。"""

    project_repo: FakeProjectRepository
    conversation_repo: FakeConversationRepository
    message_repo: FakeMessageRepository
    paper_repo: FakePaperRepository
    project_paper_repo: FakeProjectPaperRepository
    idempotency_repo: FakeIdempotencyRepository
    run_repo: FakeRunRepository
    event_repo: FakeEventRepository
    outbox_repo: FakeOutboxRepository
    revision_repo: FakeParseRevisionRepository
    chunk_set_repo: FakeChunkSetRepository
    claim_set_repo: FakeClaimSetRepository
    evidence_repo: FakeEvidenceRepository


def _make_fakes() -> _Fakes:
    revision_repo = FakeParseRevisionRepository()
    return _Fakes(
        project_repo=FakeProjectRepository(),
        conversation_repo=FakeConversationRepository(),
        message_repo=FakeMessageRepository(),
        paper_repo=FakePaperRepository(),
        project_paper_repo=FakeProjectPaperRepository(),
        idempotency_repo=FakeIdempotencyRepository(),
        run_repo=FakeRunRepository(),
        event_repo=FakeEventRepository(),
        outbox_repo=FakeOutboxRepository(),
        revision_repo=revision_repo,
        chunk_set_repo=FakeChunkSetRepository(revision_repo),
        claim_set_repo=FakeClaimSetRepository(),
        evidence_repo=FakeEvidenceRepository(),
    )


def _make_service(f: _Fakes) -> ConversationService:
    """构建使用 Fake 依赖的 ConversationService。"""
    return ConversationService(
        session_factory=fake_session,
        project_repo_factory=lambda _s: f.project_repo,
        conversation_repo_factory=lambda _s: f.conversation_repo,
        message_repo_factory=lambda _s: f.message_repo,
        paper_repo_factory=lambda _s: f.paper_repo,
        project_paper_repo_factory=lambda _s: f.project_paper_repo,
        idempotency_repo_factory=lambda _s: f.idempotency_repo,
        run_repo_factory=lambda _s: f.run_repo,
        event_repo_factory=lambda _s: f.event_repo,
        outbox_repo_factory=lambda _s: f.outbox_repo,
        chunk_set_repo_factory=lambda _s: f.chunk_set_repo,
        claim_set_repo_factory=lambda _s: f.claim_set_repo,
        evidence_repo_factory=lambda _s: f.evidence_repo,
    )


async def _add_project(f: _Fakes, *, archived: bool = False) -> str:
    """添加一个测试 Project，返回 project_id。"""
    project = create_project(owner_id="user-1", name="测试项目", description="")
    if archived:
        project = project.archive()
    await f.project_repo.add(project)
    return project.project_id


async def _add_indexed_paper(
    f: _Fakes,
    project_id: str,
    *,
    owner_id: str = "user-1",
    archived: bool = False,
    collected: bool = True,
    indexed: bool = True,
) -> tuple[str, str]:
    """添加 Paper + 收录关系 +（可选）ready ChunkSet，返回 (paper_id, version_id)。"""
    paper = create_paper(owner_id=owner_id)
    if archived:
        paper = paper.archive()
    await f.paper_repo.add(paper)
    version_id = f"v-{paper.paper_id[:8]}"
    if collected:
        await f.project_paper_repo.add(
            create_project_paper(project_id, paper.paper_id, version_id)
        )
    if indexed:
        revision = create_parse_revision(version_id, "fake", "1.0", "p" * 64)
        revision = revision.mark_succeeded(datetime.now(UTC))
        await f.revision_repo.add(revision)
        chunk_set = create_chunk_set(revision.revision_id, "h" * 64).mark_ready(
            datetime.now(UTC)
        )
        await f.chunk_set_repo.add(chunk_set)
    return paper.paper_id, version_id


async def _create_project_conversation(
    service: ConversationService,
    project_id: str,
    *,
    title: str | None = None,
) -> str:
    """创建 project 模式会话，返回 conversation_id。"""
    view = await service.create_conversation(
        _ACTOR, project_id, title=title, scope_mode="project", paper_ids=None
    )
    return view.conversation.conversation_id


async def test_create_project_mode_conversation() -> None:
    """project 模式创建成功：范围列表为空，scope 固化。"""
    f = _make_fakes()
    service = _make_service(f)
    project_id = await _add_project(f)

    view = await service.create_conversation(
        _ACTOR, project_id, title="我的会话", scope_mode="project", paper_ids=None
    )

    assert view.conversation.project_id == project_id
    assert view.conversation.owner_id == "user-1"
    assert view.conversation.title == "我的会话"
    assert view.conversation.scope_mode is ScopeMode.PROJECT
    assert view.scope_papers == []


async def test_create_selected_papers_resolves_versions() -> None:
    """selected_papers 模式：创建时解析固化 selected_version_id。"""
    f = _make_fakes()
    service = _make_service(f)
    project_id = await _add_project(f)
    paper1, version1 = await _add_indexed_paper(f, project_id)
    paper2, version2 = await _add_indexed_paper(f, project_id)

    view = await service.create_conversation(
        _ACTOR,
        project_id,
        title=None,
        scope_mode="selected_papers",
        paper_ids=[paper2, paper1, paper2],  # 防御性去重
    )

    assert view.conversation.scope_mode is ScopeMode.SELECTED_PAPERS
    assert [(e.paper_id, e.version_id) for e in view.scope_papers] == [
        (paper2, version2),
        (paper1, version1),
    ]


async def test_create_rejects_invalid_scope_mode() -> None:
    """非法 scope_mode → InvalidScopeError。"""
    f = _make_fakes()
    service = _make_service(f)
    project_id = await _add_project(f)

    with pytest.raises(InvalidScopeError):
        await service.create_conversation(
            _ACTOR, project_id, title=None, scope_mode="everything", paper_ids=None
        )


async def test_create_project_mode_rejects_paper_ids() -> None:
    """project 模式携带 paper_ids → InvalidScopeError。"""
    f = _make_fakes()
    service = _make_service(f)
    project_id = await _add_project(f)
    paper1, _ = await _add_indexed_paper(f, project_id)

    with pytest.raises(InvalidScopeError):
        await service.create_conversation(
            _ACTOR, project_id, title=None, scope_mode="project", paper_ids=[paper1]
        )


async def test_create_selected_papers_requires_non_empty() -> None:
    """selected_papers 空 paper_ids → InvalidScopeError。"""
    f = _make_fakes()
    service = _make_service(f)
    project_id = await _add_project(f)

    with pytest.raises(InvalidScopeError):
        await service.create_conversation(
            _ACTOR, project_id, title=None, scope_mode="selected_papers", paper_ids=[]
        )


async def test_create_selected_papers_rejects_uncollected() -> None:
    """未收录进 Project 的 Paper → InvalidScopeError。"""
    f = _make_fakes()
    service = _make_service(f)
    project_id = await _add_project(f)
    paper1, _ = await _add_indexed_paper(f, project_id, collected=False)

    with pytest.raises(InvalidScopeError):
        await service.create_conversation(
            _ACTOR,
            project_id,
            title=None,
            scope_mode="selected_papers",
            paper_ids=[paper1],
        )


async def test_create_selected_papers_rejects_archived() -> None:
    """已归档 Paper → InvalidScopeError。"""
    f = _make_fakes()
    service = _make_service(f)
    project_id = await _add_project(f)
    paper1, _ = await _add_indexed_paper(f, project_id, archived=True)

    with pytest.raises(InvalidScopeError):
        await service.create_conversation(
            _ACTOR,
            project_id,
            title=None,
            scope_mode="selected_papers",
            paper_ids=[paper1],
        )


async def test_create_selected_papers_rejects_other_owner() -> None:
    """其他 owner 的 Paper → InvalidScopeError。"""
    f = _make_fakes()
    service = _make_service(f)
    project_id = await _add_project(f)
    # 他人 Paper 不可能被收录到本 Project；直接构造跨 owner 场景
    paper1, _ = await _add_indexed_paper(f, project_id, owner_id="user-2")

    with pytest.raises(InvalidScopeError):
        await service.create_conversation(
            _ACTOR,
            project_id,
            title=None,
            scope_mode="selected_papers",
            paper_ids=[paper1],
        )


async def test_create_rejects_archived_project() -> None:
    """已归档 Project 创建会话 → ProjectArchivedError。"""
    f = _make_fakes()
    service = _make_service(f)
    project_id = await _add_project(f, archived=True)

    with pytest.raises(ProjectArchivedError):
        await service.create_conversation(
            _ACTOR, project_id, title=None, scope_mode="project", paper_ids=None
        )


async def test_create_rejects_missing_or_foreign_project() -> None:
    """Project 不存在或属他人 → ProjectNotFoundError。"""
    f = _make_fakes()
    service = _make_service(f)
    other = create_project(owner_id="user-2", name="他人", description="")
    await f.project_repo.add(other)

    with pytest.raises(ProjectNotFoundError):
        await service.create_conversation(
            _ACTOR, "missing", title=None, scope_mode="project", paper_ids=None
        )
    with pytest.raises(ProjectNotFoundError):
        await service.create_conversation(
            _ACTOR, other.project_id, title=None, scope_mode="project", paper_ids=None
        )


async def test_list_and_get_conversation() -> None:
    """列表与详情：owner 隔离，详情含固化范围。"""
    f = _make_fakes()
    service = _make_service(f)
    project_id = await _add_project(f)
    paper1, version1 = await _add_indexed_paper(f, project_id)
    await service.create_conversation(
        _ACTOR, project_id, title=None, scope_mode="project", paper_ids=None
    )
    selected = await service.create_conversation(
        _ACTOR,
        project_id,
        title=None,
        scope_mode="selected_papers",
        paper_ids=[paper1],
    )

    conversations = await service.list_conversations(_ACTOR, project_id)
    assert len(conversations) == 2

    detail = await service.get_conversation(
        _ACTOR, selected.conversation.conversation_id
    )
    assert [(e.paper_id, e.version_id) for e in detail.scope_papers] == [
        (paper1, version1)
    ]

    with pytest.raises(ConversationNotFoundError):
        await service.get_conversation(_ACTOR, "missing")
    with pytest.raises(ConversationNotFoundError):
        await service.get_conversation(
            ActorContext(owner_id="user-2"), selected.conversation.conversation_id
        )


async def test_post_message_creates_run_snapshot_and_claims_active() -> None:
    """提交提问：User Message + Run（含版本快照）+ Event + Outbox +
    幂等记录 + active_run_id 认领在同一事务，标题从首条问题回填。"""
    f = _make_fakes()
    service = _make_service(f)
    project_id = await _add_project(f)
    paper1, version1 = await _add_indexed_paper(f, project_id)
    paper2, version2 = await _add_indexed_paper(f, project_id)
    conversation_id = await _create_project_conversation(service, project_id)

    result = await service.post_message(
        _ACTOR,
        conversation_id,
        content="GNN 的准确率是多少？",
        idempotency_key="key-1",
        correlation_id="test",
    )

    assert result.status == "queued"
    run = await f.run_repo.get_by_id(result.run_id)
    assert run is not None
    assert run.run_type == RunType.RAG_ANSWER.value
    assert run.input_payload["conversation_id"] == conversation_id
    assert run.input_payload["user_message_id"] == result.user_message_id
    # project 模式快照：提交时刻的全部收录版本
    assert run.input_payload["version_scope"] == [
        {"paper_id": paper1, "version_id": version1},
        {"paper_id": paper2, "version_id": version2},
    ]
    user_message = await f.message_repo.get_by_id(result.user_message_id)
    assert user_message is not None
    assert user_message.role is MessageRole.USER
    assert user_message.sequence == 1
    assert user_message.run_id == run.run_id
    events = await f.event_repo.list_by_run(run.run_id)
    assert [e.event_type for e in events] == ["run_created"]
    # run_created 已占用 sequence=1，Run 的 event_sequence 必须推进到 2
    # （否则 Worker 认领时 run_started 与 run_created 撞唯一约束）
    assert run.event_sequence == 2
    assert await f.outbox_repo.get_by_run_id(run.run_id) is not None
    # 活跃认领生效
    conversation = await f.conversation_repo.get_by_id(conversation_id)
    assert conversation is not None
    assert conversation.active_run_id == run.run_id
    # 标题回填（前 50 字符）
    assert conversation.title == "GNN 的准确率是多少？"


async def test_post_message_selected_scope_snapshot_is_fixed() -> None:
    """selected_papers 模式：快照取创建时固化的范围（不含后续新收录）。"""
    f = _make_fakes()
    service = _make_service(f)
    project_id = await _add_project(f)
    paper1, version1 = await _add_indexed_paper(f, project_id)
    view = await service.create_conversation(
        _ACTOR,
        project_id,
        title=None,
        scope_mode="selected_papers",
        paper_ids=[paper1],
    )
    # 创建后又收录了新 Paper 并已索引
    await _add_indexed_paper(f, project_id)

    result = await service.post_message(
        _ACTOR,
        view.conversation.conversation_id,
        content="问题",
        idempotency_key="key-1",
        correlation_id="test",
    )

    run = await f.run_repo.get_by_id(result.run_id)
    assert run is not None
    assert run.input_payload["version_scope"] == [
        {"paper_id": paper1, "version_id": version1}
    ]


async def test_post_message_busy_rejected() -> None:
    """已有未完成回答 Run：第二个提问 409 conversation_busy。"""
    f = _make_fakes()
    service = _make_service(f)
    project_id = await _add_project(f)
    await _add_indexed_paper(f, project_id)
    conversation_id = await _create_project_conversation(service, project_id)
    await service.post_message(
        _ACTOR, conversation_id, content="第一问",
        idempotency_key="key-1", correlation_id="test",
    )

    with pytest.raises(ConversationBusyError):
        await service.post_message(
            _ACTOR, conversation_id, content="第二问",
            idempotency_key="key-2", correlation_id="test",
        )

    # busy 拒绝不产生任何副作用
    assert len(await f.message_repo.list_by_conversation(conversation_id)) == 1
    assert len(f.run_repo._runs) == 1


async def test_post_message_self_heals_terminal_active_run() -> None:
    """活跃认领指向终态 Run（如 QUEUED 被直接取消）时自愈：可再次提问。"""
    f = _make_fakes()
    service = _make_service(f)
    project_id = await _add_project(f)
    await _add_indexed_paper(f, project_id)
    conversation_id = await _create_project_conversation(service, project_id)
    first = await service.post_message(
        _ACTOR, conversation_id, content="第一问",
        idempotency_key="key-1", correlation_id="test",
    )
    # 模拟 Run 未经执行器到达终态（QUEUED → CANCELLED）
    run = await f.run_repo.get_by_id(first.run_id)
    assert run is not None
    cancelled = run.transition_to(RunStatus.CANCELLED)
    await f.run_repo.update_status(
        run.run_id, RunStatus.QUEUED, RunStatus.CANCELLED, run.event_sequence
    )
    assert cancelled.status is RunStatus.CANCELLED

    second = await service.post_message(
        _ACTOR, conversation_id, content="第二问",
        idempotency_key="key-2", correlation_id="test",
    )

    assert second.run_id != first.run_id
    conversation = await f.conversation_repo.get_by_id(conversation_id)
    assert conversation is not None
    assert conversation.active_run_id == second.run_id


async def test_post_message_not_indexed_rejected() -> None:
    """范围内无任何 ready ChunkSet → ProjectNotIndexedError，无副作用。"""
    f = _make_fakes()
    service = _make_service(f)
    project_id = await _add_project(f)
    await _add_indexed_paper(f, project_id, indexed=False)  # 收录但未索引
    conversation_id = await _create_project_conversation(service, project_id)

    with pytest.raises(ProjectNotIndexedError):
        await service.post_message(
            _ACTOR, conversation_id, content="问题",
            idempotency_key="key-1", correlation_id="test",
        )

    assert await f.message_repo.list_by_conversation(conversation_id) == []
    assert f.run_repo._runs == {}


async def test_post_message_partial_indexed_not_blocked() -> None:
    """部分就绪不阻塞：范围内有一篇 ready 即可提问。"""
    f = _make_fakes()
    service = _make_service(f)
    project_id = await _add_project(f)
    await _add_indexed_paper(f, project_id, indexed=True)
    await _add_indexed_paper(f, project_id, indexed=False)
    conversation_id = await _create_project_conversation(service, project_id)

    result = await service.post_message(
        _ACTOR, conversation_id, content="问题",
        idempotency_key="key-1", correlation_id="test",
    )

    assert result.status == "queued"


async def test_post_message_rejects_archived_project() -> None:
    """所属 Project 已归档 → ProjectArchivedError。"""
    f = _make_fakes()
    service = _make_service(f)
    project_id = await _add_project(f)
    await _add_indexed_paper(f, project_id)
    conversation_id = await _create_project_conversation(service, project_id)
    project = await f.project_repo.get_by_id(project_id)
    assert project is not None
    await f.project_repo.update(project.archive())

    with pytest.raises(ProjectArchivedError):
        await service.post_message(
            _ACTOR, conversation_id, content="问题",
            idempotency_key="key-1", correlation_id="test",
        )


async def test_post_message_rejects_foreign_conversation() -> None:
    """他人会话提问 → ConversationNotFoundError。"""
    f = _make_fakes()
    service = _make_service(f)
    project_id = await _add_project(f)
    await _add_indexed_paper(f, project_id)
    conversation_id = await _create_project_conversation(service, project_id)

    with pytest.raises(ConversationNotFoundError):
        await service.post_message(
            ActorContext(owner_id="user-2"), conversation_id, content="问题",
            idempotency_key="key-1", correlation_id="test",
        )


async def test_post_message_idempotency_replay() -> None:
    """幂等键重放：同键同内容返回原结果，不产生新 Run/Message。"""
    f = _make_fakes()
    service = _make_service(f)
    project_id = await _add_project(f)
    await _add_indexed_paper(f, project_id)
    conversation_id = await _create_project_conversation(service, project_id)
    first = await service.post_message(
        _ACTOR, conversation_id, content="问题",
        idempotency_key="key-1", correlation_id="test",
    )

    replay = await service.post_message(
        _ACTOR, conversation_id, content="问题",
        idempotency_key="key-1", correlation_id="test",
    )

    assert replay == first
    assert len(await f.message_repo.list_by_conversation(conversation_id)) == 1
    assert len(f.run_repo._runs) == 1


async def test_post_message_idempotency_conflict() -> None:
    """同键不同内容 → IdempotencyConflictError。"""
    f = _make_fakes()
    service = _make_service(f)
    project_id = await _add_project(f)
    await _add_indexed_paper(f, project_id)
    conversation_id = await _create_project_conversation(service, project_id)
    await service.post_message(
        _ACTOR, conversation_id, content="问题一",
        idempotency_key="key-1", correlation_id="test",
    )

    with pytest.raises(IdempotencyConflictError):
        await service.post_message(
            _ACTOR, conversation_id, content="问题二",
            idempotency_key="key-1", correlation_id="test",
        )


async def test_post_message_validates_content_and_key() -> None:
    """空内容、空/超长幂等键 → ValueError。"""
    f = _make_fakes()
    service = _make_service(f)
    project_id = await _add_project(f)
    await _add_indexed_paper(f, project_id)
    conversation_id = await _create_project_conversation(service, project_id)

    with pytest.raises(ValueError, match="不能为空"):
        await service.post_message(
            _ACTOR, conversation_id, content="   ",
            idempotency_key="key-1", correlation_id="test",
        )
    with pytest.raises(ValueError, match="Idempotency-Key"):
        await service.post_message(
            _ACTOR, conversation_id, content="问题",
            idempotency_key="", correlation_id="test",
        )


async def test_list_messages_with_claims_and_citations() -> None:
    """消息列表：assistant 消息携带 claims（text + citations 摘要）。"""
    f = _make_fakes()
    service = _make_service(f)
    project_id = await _add_project(f)
    await _add_indexed_paper(f, project_id)
    conversation_id = await _create_project_conversation(service, project_id)
    posted = await service.post_message(
        _ACTOR, conversation_id, content="问题",
        idempotency_key="key-1", correlation_id="test",
    )

    # 模拟执行器提交的回答产物
    claim_set = create_claim_set(posted.run_id, AnswerStatus.ANSWERED)
    await f.claim_set_repo.add_claim_set(claim_set)
    claim = create_claim(claim_set.claim_set_id, 1, "GNN 准确率为 95%。")
    await f.claim_set_repo.add_claims([claim])
    evidence = create_evidence(
        run_id=posted.run_id,
        project_id=project_id,
        paper_id="paper-1",
        version_id="version-1",
        parse_revision_id="rev-1",
        chunk_id=str(uuid4()),
        section_path="3 Experiments",
        page_start=3,
        page_end=3,
        excerpt="The accuracy is 95%.",
    )
    await f.evidence_repo.add_many([evidence])
    from literature_agent.domain.evidence import Citation

    await f.claim_set_repo.add_citations(
        [Citation(claim_id=claim.claim_id, evidence_id=evidence.evidence_id)]
    )
    await f.message_repo.add(
        create_message(
            conversation_id=conversation_id,
            sequence=2,
            role=MessageRole.ASSISTANT,
            content="GNN 准确率为 95%。",
            run_id=posted.run_id,
            claim_set_id=claim_set.claim_set_id,
        )
    )

    views = await service.list_messages(_ACTOR, conversation_id)

    assert [v.message.sequence for v in views] == [1, 2]
    assert views[0].claims is None
    assistant = views[1]
    assert assistant.claims is not None
    assert assistant.claims[0].text == "GNN 准确率为 95%。"
    citation = assistant.claims[0].citations[0]
    assert citation.evidence_id == evidence.evidence_id
    assert citation.paper_id == "paper-1"
    assert citation.version_id == "version-1"
    assert (citation.page_start, citation.page_end) == (3, 3)
    assert citation.section_path == "3 Experiments"
    assert citation.excerpt == "The accuracy is 95%."


async def test_get_evidence_authorization() -> None:
    """Evidence 详情：属该 Project 返回；跨 Project/越权/不存在 404。"""
    f = _make_fakes()
    service = _make_service(f)
    project_id = await _add_project(f)
    other_project_id = await _add_project(f)
    evidence = create_evidence(
        run_id="run-1",
        project_id=project_id,
        paper_id="paper-1",
        version_id="version-1",
        parse_revision_id="rev-1",
        chunk_id=str(uuid4()),
        section_path=None,
        page_start=1,
        page_end=2,
        excerpt="摘录",
    )
    await f.evidence_repo.add_many([evidence])

    found = await service.get_evidence(_ACTOR, project_id, evidence.evidence_id)
    assert found.evidence_id == evidence.evidence_id

    with pytest.raises(EvidenceNotFoundError):
        await service.get_evidence(_ACTOR, other_project_id, evidence.evidence_id)
    with pytest.raises(EvidenceNotFoundError):
        await service.get_evidence(_ACTOR, project_id, "missing")
    with pytest.raises(ProjectNotFoundError):
        await service.get_evidence(
            ActorContext(owner_id="user-2"), project_id, evidence.evidence_id
        )
