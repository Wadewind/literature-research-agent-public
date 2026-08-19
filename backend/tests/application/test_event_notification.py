"""事件通知注入点测试：commit 后通知、失败不影响业务。"""

from collections.abc import AsyncIterator

from literature_agent.application.run_service import RunService
from literature_agent.domain.actor import ActorContext
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_run_repository import FakeRunRepository

_ACTOR = ActorContext(owner_id="user-1")


class _RecordingNotifier:
    """记录通知调用的测试通知器。"""

    def __init__(self) -> None:
        self.notified: list[str] = []

    async def notify(self, run_id: str) -> None:
        self.notified.append(run_id)

    def subscribe(self, run_id: str) -> AsyncIterator[None]:
        async def _never() -> AsyncIterator[None]:
            return
            yield

        return _never()

    async def aclose(self) -> None:
        pass


class _FailingNotifier(_RecordingNotifier):
    """publish 永远失败的通知器。"""

    async def notify(self, run_id: str) -> None:
        raise ConnectionError("Valkey 不可用")


def _make_service(notifier) -> tuple[RunService, FakeRunRepository]:
    """构建注入指定通知器的 RunService。"""
    run_repo = FakeRunRepository()
    service = RunService(
        session_factory=fake_session,
        run_repo_factory=lambda _s: run_repo,
        event_repo_factory=lambda _s: FakeEventRepository(),
        event_notifier=notifier,
    )
    return service, run_repo


async def test_notify_after_create_run_commit() -> None:
    """创建 Run 提交后发出一次通知。"""
    notifier = _RecordingNotifier()
    service, _ = _make_service(notifier)

    run = await service.create_run(_ACTOR, "p-1", "ingestion", {}, "corr-1")

    assert notifier.notified == [run.run_id]


async def test_notify_after_cancel_commit() -> None:
    """取消 Run 提交后发出通知。"""
    notifier = _RecordingNotifier()
    service, _ = _make_service(notifier)
    run = await service.create_run(_ACTOR, "p-1", "ingestion", {}, "corr-1")
    notifier.notified.clear()

    await service.cancel_run(_ACTOR, run.run_id, "corr-2")

    assert notifier.notified == [run.run_id]


async def test_failing_notifier_does_not_break_business() -> None:
    """通知发布失败只记日志，不影响创建结果。"""
    service, run_repo = _make_service(_FailingNotifier())

    run = await service.create_run(_ACTOR, "p-1", "ingestion", {}, "corr-1")

    loaded = await run_repo.get_by_id(run.run_id)
    assert loaded is not None
    assert loaded.run_id == run.run_id
