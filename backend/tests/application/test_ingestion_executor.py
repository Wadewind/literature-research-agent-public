"""IngestionExecutor 应用服务测试。"""

import pytest

from literature_agent.application.ingestion_executor import IngestionExecutor
from literature_agent.application.run_execution_service import (
    ExecutionOutcome,
    RunExecutionService,
)
from literature_agent.domain.event import create_event
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.domain.parse_profile import ParseProfile
from literature_agent.domain.parse_revision import (
    ParseRevisionStatus,
    create_parse_revision,
)
from literature_agent.domain.queue_outbox import OutboxStatus, create_outbox_entry
from literature_agent.domain.run import Run, RunStatus, create_run
from literature_agent.infrastructure.parsing.fake_parser import FakeDocumentParser
from tests.fakes.fake_attempt_repository import FakeAttemptRepository
from tests.fakes.fake_element_repository import FakeElementRepository
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_outbox_repository import FakeOutboxRepository
from tests.fakes.fake_paper_version_repository import FakePaperVersionRepository
from tests.fakes.fake_parse_revision_repository import FakeParseRevisionRepository
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_run_repository import FakeRunRepository

_PROFILE = ParseProfile("fake", "1.0", {})


class _StubParser:
    """记录调用次数并可配置为失败的 Parser 桩。"""

    def __init__(self, fail: bool = False) -> None:
        self._fail = fail
        self._delegate = FakeDocumentParser()
        self.calls = 0

    async def parse(self, storage_key: str, profile: ParseProfile):
        """记录一次调用，按需抛错或委托 Fake Parser。"""
        self.calls += 1
        if self._fail:
            raise ValueError("PDF 已损坏")
        return await self._delegate.parse(storage_key, profile)


@pytest.fixture
def run_repo() -> FakeRunRepository:
    return FakeRunRepository()


@pytest.fixture
def event_repo() -> FakeEventRepository:
    return FakeEventRepository()


@pytest.fixture
def version_repo() -> FakePaperVersionRepository:
    return FakePaperVersionRepository()


@pytest.fixture
def revision_repo() -> FakeParseRevisionRepository:
    return FakeParseRevisionRepository()


@pytest.fixture
def element_repo() -> FakeElementRepository:
    return FakeElementRepository()


@pytest.fixture
def attempt_repo() -> FakeAttemptRepository:
    return FakeAttemptRepository()


@pytest.fixture
def outbox_repo() -> FakeOutboxRepository:
    return FakeOutboxRepository()


@pytest.fixture
def parser() -> _StubParser:
    return _StubParser()


def _make_executor(
    run_repo, event_repo, version_repo, revision_repo, element_repo,
    attempt_repo, outbox_repo, parser,
    parser_timeout_seconds: float = 300.0,
) -> IngestionExecutor:
    """构建使用 Fake 依赖的 IngestionExecutor。"""
    return IngestionExecutor(
        session_factory=fake_session,
        run_repo_factory=lambda _s: run_repo,
        event_repo_factory=lambda _s: event_repo,
        paper_version_repo_factory=lambda _s: version_repo,
        parse_revision_repo_factory=lambda _s: revision_repo,
        element_repo_factory=lambda _s: element_repo,
        attempt_repo_factory=lambda _s: attempt_repo,
        outbox_repo_factory=lambda _s: outbox_repo,
        parser=parser,
        profile=_PROFILE,
        parser_timeout_seconds=parser_timeout_seconds,
    )


def _make_service(
    executor: IngestionExecutor, run_repo, event_repo, attempt_repo, outbox_repo
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


async def _add_version(version_repo: FakePaperVersionRepository) -> str:
    """创建一个 PaperVersion 并返回其 ID。"""
    version = create_paper_version(
        paper_id="paper-1",
        file_hash="a" * 64,
        storage_key="user-1/proj/paper-1/paper.pdf",
        size_bytes=100,
        content_type="application/pdf",
    )
    await version_repo.add(version)
    return version.version_id


async def _add_uploaded_run(run_repo, event_repo, outbox_repo, version_id: str) -> Run:
    """模拟上传并派发后的 Run：QUEUED、run_created 事件、Outbox 已投递。"""
    run = create_run(
        project_id="p-1",
        owner_id="user-1",
        run_type="ingestion",
        input_payload={"paper_id": "paper-1", "version_id": version_id},
    )
    await run_repo.add(run)
    await event_repo.add(
        create_event(
            run_id=run.run_id,
            sequence=1,
            event_type="run_created",
            actor_type="user",
            correlation_id="upload",
            payload={},
        )
    )
    await run_repo.update_status(run.run_id, RunStatus.QUEUED, RunStatus.QUEUED, 2)
    entry = create_outbox_entry(run.run_id)
    await outbox_repo.add(entry)
    from datetime import UTC, datetime

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


async def test_full_pipeline_completes_with_elements(
    run_repo, event_repo, version_repo, revision_repo, element_repo,
    attempt_repo, outbox_repo, parser,
) -> None:
    """完整闭环：QUEUED → SUCCEEDED，Revision/Element/定位/当前指针齐全。"""
    version_id = await _add_version(version_repo)
    run = await _add_uploaded_run(run_repo, event_repo, outbox_repo, version_id)
    executor = _make_executor(
        run_repo, event_repo, version_repo, revision_repo, element_repo,
        attempt_repo, outbox_repo, parser,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.COMPLETED
    loaded_run = await run_repo.get_by_id(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.SUCCEEDED

    revision = await revision_repo.get_by_version_and_profile(
        version_id, _PROFILE.profile_hash
    )
    assert revision is not None
    assert revision.status == ParseRevisionStatus.SUCCEEDED
    assert revision.completed_at is not None

    elements = await element_repo.list_by_revision(revision.revision_id)
    assert len(elements) == 8
    assert [e.sequence for e in elements] == list(range(1, 9))
    # 表格带题注、父子结构
    table = [e for e in elements if e.element_type.value == "table"][0]
    caption = [e for e in elements if e.element_type.value == "caption"][0]
    assert caption.parent_element_id == table.element_id
    # 跨页段落有两个来源定位
    spanning = [e for e in elements if e.sequence == 4][0]
    locations = await element_repo.list_locations([spanning.element_id])
    assert sorted(loc.page for loc in locations) == [1, 2]

    version = await version_repo.get_by_id(version_id)
    assert version is not None
    assert version.current_parse_revision_id == revision.revision_id

    event_types = [e.event_type for e in _events(event_repo, run.run_id)]
    assert event_types == [
        "run_created",
        "run_started",
        "parse_started",
        "parse_completed",
        "normalize_completed",
        "result_committed",
    ]
    assert [e.sequence for e in _events(event_repo, run.run_id)] == [1, 2, 3, 4, 5, 6]


async def test_existing_succeeded_revision_is_reused(
    run_repo, event_repo, version_repo, revision_repo, element_repo,
    attempt_repo, outbox_repo, parser,
) -> None:
    """相同 version + profile 已有成功 Revision 时复用，不再调用 Parser。"""
    version_id = await _add_version(version_repo)
    existing = create_parse_revision(
        version_id, _PROFILE.parser_name, _PROFILE.parser_version, _PROFILE.profile_hash
    )
    from datetime import UTC, datetime

    existing = existing.mark_succeeded(datetime.now(UTC))
    await revision_repo.add(existing)

    run = await _add_uploaded_run(run_repo, event_repo, outbox_repo, version_id)
    executor = _make_executor(
        run_repo, event_repo, version_repo, revision_repo, element_repo,
        attempt_repo, outbox_repo, parser,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.COMPLETED
    assert parser.calls == 0
    loaded_run = await run_repo.get_by_id(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.SUCCEEDED
    version = await version_repo.get_by_id(version_id)
    assert version is not None
    assert version.current_parse_revision_id == existing.revision_id
    committed = [e for e in _events(event_repo, run.run_id) if e.event_type == "result_committed"]
    assert committed[0].payload["reused"] is True


async def test_transient_parser_failure_schedules_retry(
    run_repo, event_repo, version_repo, revision_repo, element_repo,
    attempt_repo, outbox_repo,
) -> None:
    """Parser 临时错误：Revision FAILED、Run RETRY_WAIT、Outbox 重置待重投。"""
    parser = _StubParser(fail=True)
    version_id = await _add_version(version_repo)
    run = await _add_uploaded_run(run_repo, event_repo, outbox_repo, version_id)
    executor = _make_executor(
        run_repo, event_repo, version_repo, revision_repo, element_repo,
        attempt_repo, outbox_repo, parser,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.RETRY_SCHEDULED
    loaded_run = await run_repo.get_by_id(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.RETRY_WAIT

    revision = await revision_repo.get_by_version_and_profile(
        version_id, _PROFILE.profile_hash
    )
    assert revision is not None
    assert revision.status == ParseRevisionStatus.FAILED
    assert revision.error is not None
    assert revision.error["type"] == "ValueError"

    assert await element_repo.count_by_revision(revision.revision_id) == 0
    version = await version_repo.get_by_id(version_id)
    assert version is not None
    assert version.current_parse_revision_id is None

    event_types = [e.event_type for e in _events(event_repo, run.run_id)]
    assert event_types == [
        "run_created", "run_started", "parse_started", "run_retry_scheduled",
    ]

    # Outbox 记录重置为待投递，等待派发循环到点重投
    entry = await outbox_repo.get_by_run_id(run.run_id)
    assert entry is not None
    assert entry.status == OutboxStatus.PENDING
    assert entry.attempt_count == 1


async def test_cancel_during_parse_skips_result_commit(
    run_repo, event_repo, version_repo, revision_repo, element_repo,
    attempt_repo, outbox_repo,
) -> None:
    """解析期间收到取消请求：提交前检查命中，推进 CANCELLED，不提交结果。"""
    version_id = await _add_version(version_repo)
    run = await _add_uploaded_run(run_repo, event_repo, outbox_repo, version_id)

    class _CancellingParser(_StubParser):
        """解析时模拟 API 并发写入 CANCEL_REQUESTED。"""

        async def parse(self, storage_key: str, profile: ParseProfile):
            loaded = await run_repo.get_by_id(run.run_id)
            assert loaded is not None
            await run_repo.update_status(
                run.run_id, RunStatus.RUNNING, RunStatus.CANCEL_REQUESTED,
                loaded.event_sequence + 1,
            )
            return await super().parse(storage_key, profile)

    parser = _CancellingParser()
    executor = _make_executor(
        run_repo, event_repo, version_repo, revision_repo, element_repo,
        attempt_repo, outbox_repo, parser,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.SKIPPED
    loaded_run = await run_repo.get_by_id(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.CANCELLED

    # 未提交任何解析产物
    revision = await revision_repo.get_by_version_and_profile(
        version_id, _PROFILE.profile_hash
    )
    assert revision is not None
    assert revision.status == ParseRevisionStatus.RUNNING
    assert await element_repo.count_by_revision(revision.revision_id) == 0
    version = await version_repo.get_by_id(version_id)
    assert version is not None
    assert version.current_parse_revision_id is None
    assert "result_committed" not in [e.event_type for e in _events(event_repo, run.run_id)]


async def test_retry_after_failure_reuses_revision_row(
    run_repo, event_repo, version_repo, revision_repo, element_repo,
    attempt_repo, outbox_repo,
) -> None:
    """上次失败的 Revision 行被复用重置，重跑成功后不留下第二行。"""
    parser = _StubParser(fail=True)
    version_id = await _add_version(version_repo)
    run = await _add_uploaded_run(run_repo, event_repo, outbox_repo, version_id)
    executor = _make_executor(
        run_repo, event_repo, version_repo, revision_repo, element_repo,
        attempt_repo, outbox_repo, parser,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)
    first = await service.execute(run.run_id, correlation_id="job-1")
    assert first == ExecutionOutcome.RETRY_SCHEDULED

    # 修复 Parser 后重跑同一 version（新 Run）
    parser._fail = False
    run2 = await _add_uploaded_run(run_repo, event_repo, outbox_repo, version_id)
    outcome = await service.execute(run2.run_id, correlation_id="job-2")

    assert outcome == ExecutionOutcome.COMPLETED
    revisions = [
        r for r in revision_repo._revisions.values() if r.version_id == version_id
    ]
    assert len(revisions) == 1
    assert revisions[0].status == ParseRevisionStatus.SUCCEEDED


async def test_parser_timeout_schedules_retry_without_fallback(
    run_repo, event_repo, version_repo, revision_repo, element_repo,
    attempt_repo, outbox_repo,
) -> None:
    """解析超时（临时错误）：Revision FAILED(parser_timeout)，Run RETRY_WAIT 等待重试。"""
    import asyncio

    class _SlowParser:
        """永远超时的 Parser 桩。"""

        async def parse(self, storage_key: str, profile: ParseProfile):
            await asyncio.sleep(10)
            raise AssertionError("不应到达")

    version_id = await _add_version(version_repo)
    run = await _add_uploaded_run(run_repo, event_repo, outbox_repo, version_id)
    executor = _make_executor(
        run_repo, event_repo, version_repo, revision_repo, element_repo,
        attempt_repo, outbox_repo, _SlowParser(), parser_timeout_seconds=0.05,
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.RETRY_SCHEDULED
    loaded_run = await run_repo.get_by_id(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.RETRY_WAIT
    revision = await revision_repo.get_by_version_and_profile(
        version_id, _PROFILE.profile_hash
    )
    assert revision is not None
    assert revision.status == ParseRevisionStatus.FAILED
    assert revision.error is not None
    assert revision.error["type"] == "parser_timeout"


async def test_degraded_result_is_persisted_on_revision(
    run_repo, event_repo, version_repo, revision_repo, element_repo,
    attempt_repo, outbox_repo,
) -> None:
    """降级解析结果：Revision 持久化 degraded 标记与文档级警告。"""
    from dataclasses import replace

    class _DegradedParser:
        """返回降级 ParsedDocument 的 Parser 桩（模拟 pypdf 回退）。"""

        async def parse(self, storage_key: str, profile: ParseProfile):
            parsed = await FakeDocumentParser().parse(storage_key, profile)
            return replace(parsed, degraded=True, warnings=["layout_missing"])

    version_id = await _add_version(version_repo)
    run = await _add_uploaded_run(run_repo, event_repo, outbox_repo, version_id)
    executor = _make_executor(
        run_repo, event_repo, version_repo, revision_repo, element_repo,
        attempt_repo, outbox_repo, _DegradedParser(),
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.COMPLETED
    revision = await revision_repo.get_by_version_and_profile(
        version_id, _PROFILE.profile_hash
    )
    assert revision is not None
    assert revision.status == ParseRevisionStatus.SUCCEEDED
    assert revision.degraded is True
    assert revision.warnings == ["layout_missing"]


async def test_empty_text_document_gets_possibly_scanned_warning(
    run_repo, event_repo, version_repo, revision_repo, element_repo,
    attempt_repo, outbox_repo,
) -> None:
    """全文文本长度为 0：解析成功但带 possibly_scanned 警告。"""
    from dataclasses import replace

    from literature_agent.domain.document_element import ParsedDocument

    class _EmptyParser:
        """返回无文本元素的 Parser 桩（模拟扫描件）。"""

        async def parse(self, storage_key: str, profile: ParseProfile):
            parsed = await FakeDocumentParser().parse(storage_key, profile)
            empty = [replace(e, text=None) for e in parsed.elements]
            return ParsedDocument(elements=empty)

    version_id = await _add_version(version_repo)
    run = await _add_uploaded_run(run_repo, event_repo, outbox_repo, version_id)
    executor = _make_executor(
        run_repo, event_repo, version_repo, revision_repo, element_repo,
        attempt_repo, outbox_repo, _EmptyParser(),
    )
    service = _make_service(executor, run_repo, event_repo, attempt_repo, outbox_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.COMPLETED
    revision = await revision_repo.get_by_version_and_profile(
        version_id, _PROFILE.profile_hash
    )
    assert revision is not None
    assert revision.degraded is False
    assert revision.warnings == ["possibly_scanned"]
