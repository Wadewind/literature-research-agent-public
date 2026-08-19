"""Run 事件 SSE 流端点测试。"""

import threading
import time
from collections.abc import AsyncIterator

import pytest_asyncio
from fastapi.testclient import TestClient

from literature_agent.api.dependencies import get_actor
from literature_agent.api.runs import get_run_service
from literature_agent.application.run_service import RunService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.event import create_event
from literature_agent.domain.run import Run, RunStatus, create_run
from tests.fakes.fake_event_repository import FakeEventRepository
from tests.fakes.fake_project_repository import fake_session
from tests.fakes.fake_run_repository import FakeRunRepository


class _NoopNotifier:
    """测试用通知器：不发布任何通知，SSE 完全依赖轮询兜底。"""

    async def notify(self, run_id: str) -> None:
        """忽略通知。"""

    def subscribe(self, run_id: str) -> AsyncIterator[None]:
        """返回永不产出的订阅。"""

        async def _never() -> AsyncIterator[None]:
            return
            yield

        return _never()

    async def aclose(self) -> None:
        """无资源可释放。"""


@pytest_asyncio.fixture
async def client(monkeypatch):
    """提供注入 Fake 依赖的 TestClient；轮询间隔调小以加速收敛。"""
    from literature_agent.main import create_app

    monkeypatch.setattr(
        "literature_agent.api.runs._SSE_POLL_INTERVAL_SECONDS", 0.05
    )
    # 心跳间隔保持默认 15s：测试在 1s 内完成，不会混入心跳注释帧

    fake_run_repo = FakeRunRepository()
    fake_event_repo = FakeEventRepository()
    service = RunService(
        session_factory=fake_session,
        run_repo_factory=lambda _session: fake_run_repo,
        event_repo_factory=lambda _session: fake_event_repo,
    )

    app = create_app()
    app.dependency_overrides[get_actor] = lambda: ActorContext(owner_id="user-1")
    app.dependency_overrides[get_run_service] = lambda: service

    with TestClient(app) as test_client:
        # 用 Noop 通知器替换 Valkey 实现，验证轮询兜底路径
        app.state.app_state.event_notifier = _NoopNotifier()
        yield test_client, fake_run_repo, fake_event_repo

    app.dependency_overrides.clear()


def _seed_run(
    fake_run_repo: FakeRunRepository,
    fake_event_repo: FakeEventRepository,
    status: RunStatus,
    event_count: int,
) -> Run:
    """准备指定状态与事件数量的 Run。"""
    run = create_run(project_id="p1", owner_id="user-1", run_type="ingestion")
    run = Run(
        run_id=run.run_id,
        project_id=run.project_id,
        owner_id=run.owner_id,
        run_type=run.run_type,
        status=status,
        input_payload=run.input_payload,
        result_payload=run.result_payload,
        event_sequence=run.event_sequence,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )
    fake_run_repo._runs[run.run_id] = run
    for seq in range(1, event_count + 1):
        fake_event_repo._events.append(
            create_event(run.run_id, seq, f"event_{seq}", "system", "corr-1")
        )
    return run


def _collect_frames(response, max_frames: int = 10) -> list[str]:
    """从 SSE 响应收集事件帧（空行分隔），忽略心跳注释帧。"""
    frames: list[str] = []
    buffer = ""
    for chunk in response.iter_text():
        buffer += chunk
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            if frame.startswith(":"):
                continue
            frames.append(frame)
            if len(frames) >= max_frames:
                return frames
    return frames


def test_sse_replays_history_and_closes_on_terminal(client) -> None:
    """终态 Run：流重放全部历史事件后主动关闭。"""
    test_client, fake_run_repo, fake_event_repo = client
    run = _seed_run(fake_run_repo, fake_event_repo, RunStatus.SUCCEEDED, 3)

    with test_client.stream("GET", f"/api/v1/runs/{run.run_id}/events/stream") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        frames = _collect_frames(resp)

    assert len(frames) == 3
    assert "id: 1" in frames[0]
    assert "event: event_1" in frames[0]
    assert "id: 3" in frames[2]


def test_sse_resumes_from_last_event_id(client) -> None:
    """Last-Event-ID 断线续传：只重放其后的事件，不重不漏。"""
    test_client, fake_run_repo, fake_event_repo = client
    run = _seed_run(fake_run_repo, fake_event_repo, RunStatus.SUCCEEDED, 4)

    with test_client.stream(
        "GET",
        f"/api/v1/runs/{run.run_id}/events/stream",
        headers={"Last-Event-ID": "2"},
    ) as resp:
        frames = _collect_frames(resp)

    assert len(frames) == 2
    assert "id: 3" in frames[0]
    assert "id: 4" in frames[1]


def test_sse_poll_converges_without_notification(client) -> None:
    """通知丢失（Noop）时，轮询兜底仍能收到流中途新增的事件并正常收束。"""
    from dataclasses import replace

    test_client, fake_run_repo, fake_event_repo = client
    run = _seed_run(fake_run_repo, fake_event_repo, RunStatus.RUNNING, 1)

    def _append_later() -> None:
        # 模拟 Worker 陆续提交：新事件 + 终态（真实系统两者同事务）
        time.sleep(0.2)
        fake_event_repo._events.append(
            create_event(run.run_id, 2, "event_2", "system", "corr-1")
        )
        time.sleep(0.2)
        fake_event_repo._events.append(
            create_event(run.run_id, 3, "run_completed", "system", "corr-1")
        )
        fake_run_repo._runs[run.run_id] = replace(run, status=RunStatus.SUCCEEDED)

    threading.Thread(target=_append_later, daemon=True).start()

    with test_client.stream("GET", f"/api/v1/runs/{run.run_id}/events/stream") as resp:
        frames = _collect_frames(resp)

    assert len(frames) == 3
    assert "id: 1" in frames[0]
    assert "id: 2" in frames[1]
    assert "event: run_completed" in frames[2]


def test_sse_run_not_found(client) -> None:
    """不存在或越权的 Run 返回 404，不建立流。"""
    test_client, _, _ = client

    response = test_client.get("/api/v1/runs/no-such-run/events/stream")

    assert response.status_code == 404


def test_sse_invalid_last_event_id(client) -> None:
    """非法 Last-Event-ID 返回 400。"""
    test_client, fake_run_repo, fake_event_repo = client
    run = _seed_run(fake_run_repo, fake_event_repo, RunStatus.RUNNING, 1)

    response = test_client.get(
        f"/api/v1/runs/{run.run_id}/events/stream",
        headers={"Last-Event-ID": "abc"},
    )

    assert response.status_code == 400
