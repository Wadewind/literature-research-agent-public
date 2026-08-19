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
from literature_agent.domain.run import Run, RunStatus, create_run
from literature_agent.infrastructure.parsing.fake_parser import FakeDocumentParser
from tests.fakes.fake_element_repository import FakeElementRepository
from tests.fakes.fake_event_repository import FakeEventRepository
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
def parser() -> _StubParser:
    return _StubParser()


def _make_executor(
    run_repo, event_repo, version_repo, revision_repo, element_repo, parser
) -> IngestionExecutor:
    """构建使用 Fake 依赖的 IngestionExecutor。"""
    return IngestionExecutor(
        session_factory=fake_session,
        run_repo_factory=lambda _s: run_repo,
        event_repo_factory=lambda _s: event_repo,
        paper_version_repo_factory=lambda _s: version_repo,
        parse_revision_repo_factory=lambda _s: revision_repo,
        element_repo_factory=lambda _s: element_repo,
        parser=parser,
        profile=_PROFILE,
    )


def _make_service(executor: IngestionExecutor, run_repo, event_repo) -> RunExecutionService:
    """构建接入真实执行器的 RunExecutionService。"""
    return RunExecutionService(
        session_factory=fake_session,
        run_repo_factory=lambda _s: run_repo,
        event_repo_factory=lambda _s: event_repo,
        executor=executor.execute,
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


async def _add_uploaded_run(run_repo, event_repo, version_id: str) -> Run:
    """模拟上传后的 Run：QUEUED、run_created 事件已写入、sequence 推进到 2。"""
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
    run_repo, event_repo, version_repo, revision_repo, element_repo, parser
) -> None:
    """完整闭环：QUEUED → SUCCEEDED，Revision/Element/定位/当前指针齐全。"""
    version_id = await _add_version(version_repo)
    run = await _add_uploaded_run(run_repo, event_repo, version_id)
    executor = _make_executor(
        run_repo, event_repo, version_repo, revision_repo, element_repo, parser
    )
    service = _make_service(executor, run_repo, event_repo)

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
    run_repo, event_repo, version_repo, revision_repo, element_repo, parser
) -> None:
    """相同 version + profile 已有成功 Revision 时复用，不再调用 Parser。"""
    version_id = await _add_version(version_repo)
    existing = create_parse_revision(
        version_id, _PROFILE.parser_name, _PROFILE.parser_version, _PROFILE.profile_hash
    )
    from datetime import UTC, datetime

    existing = existing.mark_succeeded(datetime.now(UTC))
    await revision_repo.add(existing)

    run = await _add_uploaded_run(run_repo, event_repo, version_id)
    executor = _make_executor(
        run_repo, event_repo, version_repo, revision_repo, element_repo, parser
    )
    service = _make_service(executor, run_repo, event_repo)

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


async def test_parser_failure_marks_run_and_revision_failed(
    run_repo, event_repo, version_repo, revision_repo, element_repo
) -> None:
    """Parser 抛错：Revision FAILED、Run FAILED、无 Element、无当前指针。"""
    parser = _StubParser(fail=True)
    version_id = await _add_version(version_repo)
    run = await _add_uploaded_run(run_repo, event_repo, version_id)
    executor = _make_executor(
        run_repo, event_repo, version_repo, revision_repo, element_repo, parser
    )
    service = _make_service(executor, run_repo, event_repo)

    outcome = await service.execute(run.run_id, correlation_id="job-1")

    assert outcome == ExecutionOutcome.FAILED
    loaded_run = await run_repo.get_by_id(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.FAILED

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
    assert event_types == ["run_created", "run_started", "parse_started", "run_failed"]


async def test_cancel_during_parse_skips_result_commit(
    run_repo, event_repo, version_repo, revision_repo, element_repo
) -> None:
    """解析期间收到取消请求：提交前检查命中，推进 CANCELLED，不提交结果。"""
    version_id = await _add_version(version_repo)
    run = await _add_uploaded_run(run_repo, event_repo, version_id)

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
        run_repo, event_repo, version_repo, revision_repo, element_repo, parser
    )
    service = _make_service(executor, run_repo, event_repo)

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
    run_repo, event_repo, version_repo, revision_repo, element_repo
) -> None:
    """上次失败的 Revision 行被复用重置，重跑成功后不留下第二行。"""
    parser = _StubParser(fail=True)
    version_id = await _add_version(version_repo)
    run = await _add_uploaded_run(run_repo, event_repo, version_id)
    executor = _make_executor(
        run_repo, event_repo, version_repo, revision_repo, element_repo, parser
    )
    service = _make_service(executor, run_repo, event_repo)
    assert await service.execute(run.run_id, correlation_id="job-1") == ExecutionOutcome.FAILED

    # 修复 Parser 后重跑同一 version（新 Run）
    parser._fail = False
    run2 = await _add_uploaded_run(run_repo, event_repo, version_id)
    outcome = await service.execute(run2.run_id, correlation_id="job-2")

    assert outcome == ExecutionOutcome.COMPLETED
    revisions = [
        r for r in revision_repo._revisions.values() if r.version_id == version_id
    ]
    assert len(revisions) == 1
    assert revisions[0].status == ParseRevisionStatus.SUCCEEDED
