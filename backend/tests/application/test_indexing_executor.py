"""IndexingExecutor 应用服务测试。"""

from datetime import UTC, datetime

import pytest

from literature_agent.application.indexing_executor import IndexingExecutor
from literature_agent.application.run_execution_service import (
    ExecutionOutcome,
    RunExecutionService,
)
from literature_agent.domain.chunk import ChunkSetStatus, create_chunk_set
from literature_agent.domain.chunk_profile import ChunkProfile
from literature_agent.domain.document_element import normalize_parsed_document
from literature_agent.domain.event import create_event
from literature_agent.domain.parse_profile import ParseProfile
from literature_agent.domain.parse_revision import create_parse_revision
from literature_agent.domain.queue_outbox import OutboxStatus, create_outbox_entry
from literature_agent.domain.run import Run, RunStatus, RunType, create_run
from literature_agent.infrastructure.parsing.fake_parser import FakeDocumentParser
from tests.fakes.fake_attempt_repository import FakeAttemptRepository
from tests.fakes.fake_chunk_repository import FakeChunkRepository
from tests.fakes.fake_chunk_set_repository import FakeChunkSetRepository
from tests.fakes.fake_element_repository import FakeElementRepository
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_outbox_repository import FakeOutboxRepository
from tests.fakes.fake_parse_revision_repository import FakeParseRevisionRepository
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_run_repository import FakeRunRepository

_PROFILE = ChunkProfile(
    embedding_provider="test", embedding_model="m", embedding_dimensions=1024
)


@pytest.fixture
def run_repo() -> FakeRunRepository:
    return FakeRunRepository()


@pytest.fixture
def event_repo() -> FakeEventRepository:
    return FakeEventRepository()


@pytest.fixture
def revision_repo() -> FakeParseRevisionRepository:
    return FakeParseRevisionRepository()


@pytest.fixture
def element_repo() -> FakeElementRepository:
    return FakeElementRepository()


@pytest.fixture
def chunk_set_repo() -> FakeChunkSetRepository:
    return FakeChunkSetRepository()


@pytest.fixture
def chunk_repo() -> FakeChunkRepository:
    return FakeChunkRepository()


@pytest.fixture
def attempt_repo() -> FakeAttemptRepository:
    return FakeAttemptRepository()


@pytest.fixture
def outbox_repo() -> FakeOutboxRepository:
    return FakeOutboxRepository()


def _make_executor(
    run_repo, event_repo, revision_repo, element_repo,
    chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
) -> IndexingExecutor:
    """构建使用 Fake 依赖的 IndexingExecutor。"""
    return IndexingExecutor(
        session_factory=fake_session,
        run_repo_factory=lambda _s: run_repo,
        event_repo_factory=lambda _s: event_repo,
        parse_revision_repo_factory=lambda _s: revision_repo,
        element_repo_factory=lambda _s: element_repo,
        chunk_set_repo_factory=lambda _s: chunk_set_repo,
        chunk_repo_factory=lambda _s: chunk_repo,
        attempt_repo_factory=lambda _s: attempt_repo,
        outbox_repo_factory=lambda _s: outbox_repo,
        profile=_PROFILE,
    )


def _make_service(
    executor: IndexingExecutor, run_repo, event_repo, attempt_repo, outbox_repo
) -> RunExecutionService:
    """构建接入真实执行器的 RunExecutionService。"""
    return RunExecutionService(
        session_factory=fake_session,
        run_repo_factory=lambda _s: run_repo,
        event_repo_factory=lambda _s: event_repo,
        attempt_repo_factory=lambda _s: attempt_repo,
        outbox_repo_factory=lambda _s: outbox_repo,
        executor=executor.execute,
        worker_id="test-worker:1",
        heartbeat_interval_seconds=3600.0,
    )


async def _add_succeeded_revision(
    revision_repo: FakeParseRevisionRepository,
    element_repo: FakeElementRepository,
    *,
    with_elements: bool = True,
) -> str:
    """创建已成功的 Parse Revision，并用 Fake Parser 输出填充 Element。"""
    revision = create_parse_revision("version-1", "fake", "1.0", "p" * 64)
    revision = revision.mark_succeeded(datetime.now(UTC))
    await revision_repo.add(revision)
    if with_elements:
        parsed = await FakeDocumentParser().parse("key", ParseProfile("fake", "1.0", {}))
        elements, locations = normalize_parsed_document(revision.revision_id, parsed)
        await element_repo.add_many(elements)
        await element_repo.add_locations(locations)
    return revision.revision_id


async def _add_indexing_run(
    run_repo, event_repo, outbox_repo, revision_id: str
) -> Run:
    """模拟 ingestion 触发并派发后的 indexing Run：QUEUED、run_created、Outbox 已投递。"""
    run = create_run(
        project_id="p-1",
        owner_id="user-1",
        run_type=RunType.INDEXING,
        input_payload={"parse_revision_id": revision_id, "version_id": "version-1"},
    )
    await run_repo.add(run)
    await event_repo.add(
        create_event(
            run_id=run.run_id,
            sequence=1,
            event_type="run_created",
            actor_type="system",
            correlation_id="ingestion",
            payload={},
        )
    )
    await run_repo.update_status(run.run_id, RunStatus.QUEUED, RunStatus.QUEUED, 2)
    entry = create_outbox_entry(run.run_id)
    await outbox_repo.add(entry)
    await outbox_repo.try_mark_dispatched(entry.outbox_id, datetime.now(UTC))
    loaded = await run_repo.get_by_id(run.run_id)
    assert loaded is not None
    return loaded


def _events(event_repo: FakeEventRepository, run_id: str) -> list:
    """按 sequence 返回指定 Run 的事件。"""
    return sorted(
        [e for e in event_repo._events if e.run_id == run_id],
        key=lambda e: e.sequence,
    )


async def test_full_pipeline_completes_with_chunks(
    run_repo, event_repo, revision_repo, element_repo,
    chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
) -> None:
    """完整闭环：QUEUED → SUCCEEDED，ChunkSet ready、Chunk/映射齐全、事件序列正确。"""
    revision_id = await _add_succeeded_revision(revision_repo, element_repo)
    run = await _add_indexing_run(run_repo, event_repo, outbox_repo, revision_id)
    executor = _make_executor(
        run_repo, event_repo, revision_repo, element_repo,
        chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.COMPLETED
    loaded_run = await run_repo.get_by_id(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.SUCCEEDED

    chunk_set = await chunk_set_repo.get_by_revision_and_profile(
        revision_id, _PROFILE.profile_hash
    )
    assert chunk_set is not None
    assert chunk_set.status == ChunkSetStatus.READY
    assert chunk_set.completed_at is not None
    assert chunk_set.config == _PROFILE.config

    chunks = await chunk_repo.list_by_chunk_set(chunk_set.chunk_set_id)
    assert len(chunks) > 0
    assert [c.sequence for c in chunks] == list(range(1, len(chunks) + 1))
    # Fake Parser 的表格与题注在同一 Chunk
    assert any("准确率" in c.text and "表 1" in c.text for c in chunks)
    # 章节前缀拼入 Chunk 文本
    assert any(c.text.startswith("1 引言") for c in chunks)
    # Element 映射有序且指向真实 Element
    links = await chunk_repo.list_links([c.chunk_id for c in chunks])
    assert links
    element_ids = {e.element_id for e in element_repo._elements.values()}
    assert all(link.element_id in element_ids for link in links)

    event_types = [e.event_type for e in _events(event_repo, run.run_id)]
    assert event_types == [
        "run_created",
        "run_started",
        "indexing_started",
        "chunking_completed",
        "indexing_completed",
    ]
    completed = _events(event_repo, run.run_id)[-1]
    assert completed.payload["reused"] is False
    assert completed.payload["chunk_count"] == len(chunks)


async def test_ready_chunk_set_is_reused(
    run_repo, event_repo, revision_repo, element_repo,
    chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
) -> None:
    """相同 revision + profile 已有 ready ChunkSet 时直接复用，不重复切分。"""
    revision_id = await _add_succeeded_revision(revision_repo, element_repo)
    existing = create_chunk_set(revision_id, _PROFILE.profile_hash, _PROFILE.config)
    existing = existing.mark_ready(datetime.now(UTC))
    await chunk_set_repo.add(existing)

    run = await _add_indexing_run(run_repo, event_repo, outbox_repo, revision_id)
    executor = _make_executor(
        run_repo, event_repo, revision_repo, element_repo,
        chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.COMPLETED
    loaded_run = await run_repo.get_by_id(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.SUCCEEDED
    completed = [e for e in _events(event_repo, run.run_id)
                 if e.event_type == "indexing_completed"]
    assert completed[0].payload["reused"] is True
    # 未写 indexing_started，未产生新 Chunk
    assert "indexing_started" not in [e.event_type for e in _events(event_repo, run.run_id)]
    assert await chunk_repo.count_by_chunk_set(existing.chunk_set_id) == 0


async def test_failed_chunk_set_row_is_reset_and_rerun(
    run_repo, event_repo, revision_repo, element_repo,
    chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
) -> None:
    """上次失败的 ChunkSet 行被重置复用，重跑成功后不留下第二行。"""
    revision_id = await _add_succeeded_revision(revision_repo, element_repo)
    failed = create_chunk_set(revision_id, _PROFILE.profile_hash, _PROFILE.config)
    failed = failed.mark_failed({"type": "ValueError", "message": "x"}, datetime.now(UTC))
    await chunk_set_repo.add(failed)

    run = await _add_indexing_run(run_repo, event_repo, outbox_repo, revision_id)
    executor = _make_executor(
        run_repo, event_repo, revision_repo, element_repo,
        chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.COMPLETED
    rows = [
        c for c in chunk_set_repo._chunk_sets.values()
        if c.parse_revision_id == revision_id
    ]
    assert len(rows) == 1
    assert rows[0].chunk_set_id == failed.chunk_set_id
    assert rows[0].status == ChunkSetStatus.READY
    assert await chunk_repo.count_by_chunk_set(failed.chunk_set_id) > 0


async def test_empty_revision_produces_empty_ready_chunk_set(
    run_repo, event_repo, revision_repo, element_repo,
    chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
) -> None:
    """revision 已成功但零 Element（空文档）属合法：产生空 ChunkSet 并 ready。"""
    revision_id = await _add_succeeded_revision(
        revision_repo, element_repo, with_elements=False
    )
    run = await _add_indexing_run(run_repo, event_repo, outbox_repo, revision_id)
    executor = _make_executor(
        run_repo, event_repo, revision_repo, element_repo,
        chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.COMPLETED
    chunk_set = await chunk_set_repo.get_by_revision_and_profile(
        revision_id, _PROFILE.profile_hash
    )
    assert chunk_set is not None
    assert chunk_set.status == ChunkSetStatus.READY
    assert await chunk_repo.count_by_chunk_set(chunk_set.chunk_set_id) == 0
    completed = [e for e in _events(event_repo, run.run_id)
                 if e.event_type == "indexing_completed"]
    assert completed[0].payload["chunk_count"] == 0


async def test_missing_revision_fails_permanently(
    run_repo, event_repo, revision_repo, element_repo,
    chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
) -> None:
    """Parse Revision 不存在：永久输入错误，Run 直接 FAILED，不创建 ChunkSet。"""
    run = await _add_indexing_run(run_repo, event_repo, outbox_repo, "rev-not-exist")
    executor = _make_executor(
        run_repo, event_repo, revision_repo, element_repo,
        chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.FAILED
    loaded_run = await run_repo.get_by_id(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.FAILED
    failed = [e for e in _events(event_repo, run.run_id) if e.event_type == "run_failed"]
    assert failed[0].payload["error"]["type"] == "IndexingInputError"
    assert chunk_set_repo._chunk_sets == {}


async def test_unsucceeded_revision_fails_permanently(
    run_repo, event_repo, revision_repo, element_repo,
    chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
) -> None:
    """Parse Revision 尚未成功（running/failed）：永久输入错误，Run FAILED。"""
    revision = create_parse_revision("version-1", "fake", "1.0", "p" * 64)
    await revision_repo.add(revision)  # 保持 RUNNING
    run = await _add_indexing_run(run_repo, event_repo, outbox_repo, revision.revision_id)
    executor = _make_executor(
        run_repo, event_repo, revision_repo, element_repo,
        chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.FAILED
    loaded_run = await run_repo.get_by_id(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.FAILED


async def test_builder_failure_marks_chunk_set_and_retries(
    run_repo, event_repo, revision_repo, element_repo,
    chunk_set_repo, chunk_repo, attempt_repo, outbox_repo, monkeypatch,
) -> None:
    """ChunkBuilder 未知异常（临时错误）：ChunkSet FAILED、Run RETRY_WAIT、Outbox 重置。"""
    import literature_agent.application.indexing_executor as executor_module

    def _boom(elements, locations, profile):
        raise RuntimeError("tokenizer 加载失败")

    monkeypatch.setattr(executor_module, "build_chunks", _boom)

    revision_id = await _add_succeeded_revision(revision_repo, element_repo)
    run = await _add_indexing_run(run_repo, event_repo, outbox_repo, revision_id)
    executor = _make_executor(
        run_repo, event_repo, revision_repo, element_repo,
        chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.RETRY_SCHEDULED
    loaded_run = await run_repo.get_by_id(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.RETRY_WAIT
    chunk_set = await chunk_set_repo.get_by_revision_and_profile(
        revision_id, _PROFILE.profile_hash
    )
    assert chunk_set is not None
    assert chunk_set.status == ChunkSetStatus.FAILED
    assert chunk_set.error is not None
    assert chunk_set.error["type"] == "RuntimeError"
    entry = await outbox_repo.get_by_run_id(run.run_id)
    assert entry is not None
    assert entry.status == OutboxStatus.PENDING


async def test_cancel_during_build_skips_result_commit(
    run_repo, event_repo, revision_repo, element_repo,
    chunk_set_repo, chunk_repo, attempt_repo, outbox_repo, monkeypatch,
) -> None:
    """构建期间收到取消请求：提交前检查命中，推进 CANCELLED，不提交结果。"""
    import literature_agent.application.indexing_executor as executor_module

    revision_id = await _add_succeeded_revision(revision_repo, element_repo)
    run = await _add_indexing_run(run_repo, event_repo, outbox_repo, revision_id)

    original = executor_module.build_chunks

    def _cancelling_build(elements, locations, profile):
        """构建时模拟 API 并发写入 CANCEL_REQUESTED（直接改 Fake 仓储）。"""
        from dataclasses import replace

        loaded = run_repo._runs[run.run_id]
        run_repo._runs[run.run_id] = replace(
            loaded, status=RunStatus.CANCEL_REQUESTED
        )
        return original(elements, locations, profile)

    monkeypatch.setattr(executor_module, "build_chunks", _cancelling_build)
    executor = _make_executor(
        run_repo, event_repo, revision_repo, element_repo,
        chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.SKIPPED
    loaded_run = await run_repo.get_by_id(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.CANCELLED
    chunk_set = await chunk_set_repo.get_by_revision_and_profile(
        revision_id, _PROFILE.profile_hash
    )
    assert chunk_set is not None
    assert chunk_set.status == ChunkSetStatus.RUNNING
    assert await chunk_repo.count_by_chunk_set(chunk_set.chunk_set_id) == 0
    assert "indexing_completed" not in [e.event_type for e in _events(event_repo, run.run_id)]


async def test_defense_rejects_non_indexing_run(
    run_repo, event_repo, revision_repo, element_repo,
    chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
) -> None:
    """防御：IndexingExecutor 收到非 indexing 类型直接抛错。"""
    revision_id = await _add_succeeded_revision(revision_repo, element_repo)
    run = create_run(
        project_id="p-1",
        owner_id="user-1",
        run_type=RunType.INGESTION,
        input_payload={"parse_revision_id": revision_id},
    )
    executor = _make_executor(
        run_repo, event_repo, revision_repo, element_repo,
        chunk_set_repo, chunk_repo, attempt_repo, outbox_repo,
    )

    with pytest.raises(ValueError, match="非 indexing"):
        await executor.execute(run, correlation_id="job-1")
