"""Conversation/Evidence API 路由测试（切片 8）。"""

import asyncio
from datetime import UTC, datetime

import pytest_asyncio
from fastapi.testclient import TestClient

from literature_agent.api.conversations import get_conversation_service
from literature_agent.api.dependencies import get_actor
from literature_agent.application.conversation_service import ConversationService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.chunk import create_chunk_set
from literature_agent.domain.evidence import create_evidence
from literature_agent.domain.paper import create_paper
from literature_agent.domain.parse_revision import create_parse_revision
from literature_agent.domain.project import create_project
from literature_agent.domain.project_paper import create_project_paper
from literature_agent.main import create_app
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
from tests.fakes.fake_project_repository import (
    FakeProjectRepository,
    fake_session,
)
from tests.fakes.fake_run_repository import FakeRunRepository


class _Fakes:
    """一套共享的 Fake Repository（API 测试用）。"""

    def __init__(self) -> None:
        self.revision_repo = FakeParseRevisionRepository()
        self.project_repo = FakeProjectRepository()
        self.conversation_repo = FakeConversationRepository()
        self.message_repo = FakeMessageRepository()
        self.paper_repo = FakePaperRepository()
        self.project_paper_repo = FakeProjectPaperRepository()
        self.idempotency_repo = FakeIdempotencyRepository()
        self.run_repo = FakeRunRepository()
        self.event_repo = FakeEventRepository()
        self.outbox_repo = FakeOutboxRepository()
        self.chunk_set_repo = FakeChunkSetRepository(self.revision_repo)
        self.claim_set_repo = FakeClaimSetRepository()
        self.evidence_repo = FakeEvidenceRepository()

    def build_service(self) -> ConversationService:
        """构建使用 Fake 依赖的 ConversationService。"""
        return ConversationService(
            session_factory=fake_session,
            project_repo_factory=lambda _s: self.project_repo,
            conversation_repo_factory=lambda _s: self.conversation_repo,
            message_repo_factory=lambda _s: self.message_repo,
            paper_repo_factory=lambda _s: self.paper_repo,
            project_paper_repo_factory=lambda _s: self.project_paper_repo,
            idempotency_repo_factory=lambda _s: self.idempotency_repo,
            run_repo_factory=lambda _s: self.run_repo,
            event_repo_factory=lambda _s: self.event_repo,
            outbox_repo_factory=lambda _s: self.outbox_repo,
            chunk_set_repo_factory=lambda _s: self.chunk_set_repo,
            claim_set_repo_factory=lambda _s: self.claim_set_repo,
            evidence_repo_factory=lambda _s: self.evidence_repo,
        )


@pytest_asyncio.fixture
async def client():
    """提供已注入 Fake 依赖的 TestClient 与共享 Fakes。"""
    fakes = _Fakes()
    app = create_app()
    app.dependency_overrides[get_actor] = lambda: ActorContext(owner_id="user-1")
    app.dependency_overrides[get_conversation_service] = fakes.build_service

    with TestClient(app) as test_client:
        yield test_client, fakes

    app.dependency_overrides.clear()


def _seed(coro):
    """在同步测试中执行一步 Fake 仓储写入。"""
    return asyncio.run(coro)


def _add_project(fakes: _Fakes, *, archived: bool = False) -> str:
    """写入一个测试 Project，返回 project_id。"""
    project = create_project(owner_id="user-1", name="API 项目", description="")
    if archived:
        project = project.archive()
    _seed(fakes.project_repo.add(project))
    return project.project_id


def _add_paper(
    fakes: _Fakes,
    project_id: str,
    *,
    collected: bool = True,
    indexed: bool = True,
) -> tuple[str, str]:
    """写入 Paper + 收录关系 +（可选）ready ChunkSet，返回 (paper_id, version_id)。"""
    paper = create_paper(owner_id="user-1")
    _seed(fakes.paper_repo.add(paper))
    version_id = f"v-{paper.paper_id[:8]}"
    if collected:
        _seed(
            fakes.project_paper_repo.add(
                create_project_paper(project_id, paper.paper_id, version_id)
            )
        )
    if indexed:
        revision = create_parse_revision(version_id, "fake", "1.0", "p" * 64)
        revision = revision.mark_succeeded(datetime.now(UTC))
        _seed(fakes.revision_repo.add(revision))
        chunk_set = create_chunk_set(revision.revision_id, "h" * 64).mark_ready(
            datetime.now(UTC)
        )
        _seed(fakes.chunk_set_repo.add(chunk_set))
    return paper.paper_id, version_id


def _create_conversation(
    test_client: TestClient, project_id: str, **overrides
) -> dict:
    """经 API 创建会话并断言 201，返回响应体。"""
    body = {"scope_mode": "project", "title": None, "paper_ids": None}
    body.update(overrides)
    response = test_client.post(
        f"/api/v1/projects/{project_id}/conversations", json=body
    )
    assert response.status_code == 201
    return response.json()


def test_create_project_mode_conversation(client) -> None:
    """POST conversations（project 模式）返回 201，范围列表为空。"""
    test_client, fakes = client
    project_id = _add_project(fakes)

    response = test_client.post(
        f"/api/v1/projects/{project_id}/conversations",
        json={"scope_mode": "project", "title": "我的会话", "paper_ids": None},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["project_id"] == project_id
    assert data["owner_id"] == "user-1"
    assert data["title"] == "我的会话"
    assert data["scope_mode"] == "project"
    assert data["active_run_id"] is None
    assert data["scope_papers"] == []


def test_create_selected_papers_conversation(client) -> None:
    """POST conversations（selected_papers 模式）固化选中论文的范围版本。"""
    test_client, fakes = client
    project_id = _add_project(fakes)
    paper_id, version_id = _add_paper(fakes, project_id)

    response = test_client.post(
        f"/api/v1/projects/{project_id}/conversations",
        json={
            "scope_mode": "selected_papers",
            "title": None,
            "paper_ids": [paper_id],
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["scope_mode"] == "selected_papers"
    assert data["scope_papers"] == [
        {"paper_id": paper_id, "version_id": version_id}
    ]


def test_create_invalid_scope_returns_422(client) -> None:
    """selected_papers 含未收录 Paper：422 invalid_scope。"""
    test_client, fakes = client
    project_id = _add_project(fakes)

    response = test_client.post(
        f"/api/v1/projects/{project_id}/conversations",
        json={
            "scope_mode": "selected_papers",
            "title": None,
            "paper_ids": ["00000000-0000-0000-0000-000000000000"],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "invalid_scope"


def test_create_in_archived_project_returns_409(client) -> None:
    """已归档 Project 创建会话：409 project_archived。"""
    test_client, fakes = client
    project_id = _add_project(fakes, archived=True)

    response = test_client.post(
        f"/api/v1/projects/{project_id}/conversations",
        json={"scope_mode": "project", "title": None, "paper_ids": None},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "project_archived"


def test_list_conversations(client) -> None:
    """GET conversations 按创建顺序列出 Project 的会话。"""
    test_client, fakes = client
    project_id = _add_project(fakes)
    first = _create_conversation(test_client, project_id, title="会话一")
    second = _create_conversation(test_client, project_id, title="会话二")

    response = test_client.get(f"/api/v1/projects/{project_id}/conversations")

    assert response.status_code == 200
    data = response.json()
    assert [c["conversation_id"] for c in data] == [
        first["conversation_id"],
        second["conversation_id"],
    ]


def test_get_conversation_not_found_returns_404(client) -> None:
    """访问不存在（或他人）的会话返回 404 conversation_not_found。"""
    test_client, _ = client

    response = test_client.get(
        "/api/v1/conversations/00000000-0000-0000-0000-000000000000"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "conversation_not_found"


def test_post_message_returns_202(client) -> None:
    """POST messages：202 + {user_message_id, run_id, status: queued}。"""
    test_client, fakes = client
    project_id = _add_project(fakes)
    _add_paper(fakes, project_id)
    conversation = _create_conversation(test_client, project_id)

    response = test_client.post(
        f"/api/v1/conversations/{conversation['conversation_id']}/messages",
        json={"content": "什么是 RAG？"},
        headers={"Idempotency-Key": "key-1"},
    )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "queued"
    assert data["user_message_id"]
    assert data["run_id"]


def test_post_message_requires_idempotency_key(client) -> None:
    """缺 Idempotency-Key 请求头：400。"""
    test_client, fakes = client
    project_id = _add_project(fakes)
    _add_paper(fakes, project_id)
    conversation = _create_conversation(test_client, project_id)

    response = test_client.post(
        f"/api/v1/conversations/{conversation['conversation_id']}/messages",
        json={"content": "问题"},
    )

    assert response.status_code == 400


def test_post_message_busy_returns_409(client) -> None:
    """会话已有未完成回答 Run：第二个提问 409 conversation_busy。"""
    test_client, fakes = client
    project_id = _add_project(fakes)
    _add_paper(fakes, project_id)
    conversation = _create_conversation(test_client, project_id)
    url = f"/api/v1/conversations/{conversation['conversation_id']}/messages"
    first = test_client.post(
        url, json={"content": "第一问"}, headers={"Idempotency-Key": "key-1"}
    )
    assert first.status_code == 202

    response = test_client.post(
        url, json={"content": "第二问"}, headers={"Idempotency-Key": "key-2"}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "conversation_busy"


def test_post_message_not_indexed_returns_409(client) -> None:
    """范围内无任何 ready ChunkSet：409 project_not_indexed。"""
    test_client, fakes = client
    project_id = _add_project(fakes)
    _add_paper(fakes, project_id, indexed=False)
    conversation = _create_conversation(test_client, project_id)

    response = test_client.post(
        f"/api/v1/conversations/{conversation['conversation_id']}/messages",
        json={"content": "问题"},
        headers={"Idempotency-Key": "key-1"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "project_not_indexed"


def test_post_message_idempotent_replay(client) -> None:
    """相同幂等键 + 相同内容重放：返回相同 run_id，不产生第二条消息。"""
    test_client, fakes = client
    project_id = _add_project(fakes)
    _add_paper(fakes, project_id)
    conversation = _create_conversation(test_client, project_id)
    url = f"/api/v1/conversations/{conversation['conversation_id']}/messages"
    first = test_client.post(
        url, json={"content": "问题"}, headers={"Idempotency-Key": "key-1"}
    )
    assert first.status_code == 202

    replay = test_client.post(
        url, json={"content": "问题"}, headers={"Idempotency-Key": "key-1"}
    )

    assert replay.status_code == 202
    assert replay.json()["run_id"] == first.json()["run_id"]
    assert replay.json()["user_message_id"] == first.json()["user_message_id"]


def test_list_messages(client) -> None:
    """GET messages 按 sequence 升序返回，user 消息 claims 为 null。"""
    test_client, fakes = client
    project_id = _add_project(fakes)
    _add_paper(fakes, project_id)
    conversation = _create_conversation(test_client, project_id)
    test_client.post(
        f"/api/v1/conversations/{conversation['conversation_id']}/messages",
        json={"content": "问题"},
        headers={"Idempotency-Key": "key-1"},
    )

    response = test_client.get(
        f"/api/v1/conversations/{conversation['conversation_id']}/messages"
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["role"] == "user"
    assert data[0]["content"] == "问题"
    assert data[0]["sequence"] == 1
    assert data[0]["claims"] is None


def test_get_evidence(client) -> None:
    """GET evidence 返回详情（含 excerpt 与 version_id）。"""
    test_client, fakes = client
    project_id = _add_project(fakes)
    evidence = create_evidence(
        run_id="run-1",
        project_id=project_id,
        paper_id="paper-1",
        version_id="version-1",
        parse_revision_id="rev-1",
        chunk_id="chunk-1",
        section_path="1 引言",
        page_start=3,
        page_end=4,
        excerpt="证据摘录文本",
    )
    _seed(fakes.evidence_repo.add_many([evidence]))

    response = test_client.get(
        f"/api/v1/projects/{project_id}/evidence/{evidence.evidence_id}"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["evidence_id"] == evidence.evidence_id
    assert data["version_id"] == "version-1"
    assert data["page_start"] == 3
    assert data["excerpt"] == "证据摘录文本"


def test_get_evidence_not_found_returns_404(client) -> None:
    """访问不存在（或跨 Project）的 Evidence 返回 404 evidence_not_found。"""
    test_client, fakes = client
    project_id = _add_project(fakes)

    response = test_client.get(
        f"/api/v1/projects/{project_id}/evidence/"
        "00000000-0000-0000-0000-000000000000"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "evidence_not_found"
