"""index-status API 路由测试（切片 5）。"""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from literature_agent.api.dependencies import get_actor
from literature_agent.api.documents import get_document_query_service
from literature_agent.application.document_query_service import DocumentQueryService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.chunk import Chunk, create_chunk_set
from literature_agent.domain.chunk_profile import ChunkProfile
from literature_agent.domain.paper import create_paper
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.domain.parse_profile import ParseProfile
from literature_agent.domain.parse_revision import create_parse_revision
from literature_agent.domain.project import create_project
from literature_agent.domain.project_paper import create_project_paper
from literature_agent.domain.run import RunType, create_run
from literature_agent.main import create_app
from tests.fakes.fake_chunk_repository import FakeChunkRepository
from tests.fakes.fake_chunk_set_repository import FakeChunkSetRepository
from tests.fakes.fake_element_repository import FakeElementRepository
from tests.fakes.fake_paper_repository import FakePaperRepository
from tests.fakes.fake_paper_version_repository import FakePaperVersionRepository
from tests.fakes.fake_parse_revision_repository import FakeParseRevisionRepository
from tests.fakes.fake_project_paper_repository import FakeProjectPaperRepository
from tests.fakes.fake_project_repository import FakeProjectRepository, fake_session
from tests.fakes.fake_run_repository import FakeRunRepository

_PROFILE = ParseProfile("fake", "1.0", {})
_CHUNK_PROFILE = ChunkProfile(
    embedding_provider="fake",
    embedding_model="fake-embedding",
    embedding_dimensions=1024,
)


@pytest_asyncio.fixture
async def client():
    """提供已注入 Fake 依赖、带收录关系的 TestClient。

    返回 dict：client、project_id、version_id、revision_id、
    other_project_id 与各 Fake 仓储（供用例布置 ChunkSet/Run/新版本）。
    """
    project_repo = FakeProjectRepository()
    paper_repo = FakePaperRepository()
    version_repo = FakePaperVersionRepository()
    revision_repo = FakeParseRevisionRepository()
    relation_repo = FakeProjectPaperRepository()
    chunk_set_repo = FakeChunkSetRepository()
    chunk_repo = FakeChunkRepository()
    run_repo = FakeRunRepository()

    project = create_project(owner_id="user-1", name="项目A", description="")
    other_project = create_project(owner_id="user-2", name="项目B", description="")
    await project_repo.add(project)
    await project_repo.add(other_project)

    paper = create_paper(owner_id="user-1")
    await paper_repo.add(paper)
    version = create_paper_version(
        paper_id=paper.paper_id,
        owner_id="user-1",
        file_hash="a" * 64,
        storage_key="k",
        size_bytes=10,
        content_type="application/pdf",
    )
    revision = create_parse_revision(
        version.version_id,
        _PROFILE.parser_name,
        _PROFILE.parser_version,
        _PROFILE.profile_hash,
    ).mark_succeeded(datetime.now(UTC))
    await revision_repo.add(revision)
    version = replace(version, current_parse_revision_id=revision.revision_id)
    await version_repo.add(version)
    await relation_repo.add(
        create_project_paper(project.project_id, paper.paper_id, version.version_id)
    )

    service = DocumentQueryService(
        session_factory=fake_session,
        project_repo_factory=lambda _s: project_repo,
        paper_repo_factory=lambda _s: paper_repo,
        paper_version_repo_factory=lambda _s: version_repo,
        project_paper_repo_factory=lambda _s: relation_repo,
        parse_revision_repo_factory=lambda _s: revision_repo,
        element_repo_factory=lambda _s: FakeElementRepository(),
        chunk_set_repo_factory=lambda _s: chunk_set_repo,
        chunk_repo_factory=lambda _s: chunk_repo,
        run_repo_factory=lambda _s: run_repo,
    )

    app = create_app()

    async def actor_override() -> ActorContext:
        return ActorContext(owner_id="user-1")

    async def service_override() -> DocumentQueryService:
        return service

    app.dependency_overrides[get_actor] = actor_override
    app.dependency_overrides[get_document_query_service] = service_override

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        yield {
            "client": test_client,
            "project_id": project.project_id,
            "version_id": version.version_id,
            "revision_id": revision.revision_id,
            "other_project_id": other_project.project_id,
            "paper_repo": paper_repo,
            "version_repo": version_repo,
            "relation_repo": relation_repo,
            "chunk_set_repo": chunk_set_repo,
            "chunk_repo": chunk_repo,
            "run_repo": run_repo,
        }

    app.dependency_overrides.clear()


def _url(fx) -> str:
    """构造 index-status 端点路径。"""
    return (
        f"/api/v1/projects/{fx['project_id']}"
        f"/paper-versions/{fx['version_id']}/index-status"
    )


async def _add_indexing_run(fx) -> str:
    """布置一条 indexing Run，返回 run_id。"""
    run = create_run(
        project_id=fx["project_id"],
        owner_id="user-1",
        run_type=RunType.INDEXING,
        input_payload={
            "parse_revision_id": fx["revision_id"],
            "version_id": fx["version_id"],
        },
    )
    await fx["run_repo"].add(run)
    return run.run_id


async def test_index_status_no_chunk_set_returns_null(client) -> None:
    """尚无 ChunkSet：chunk_set 为 null，indexing_run_id 指向最近一次 indexing Run。"""
    run_id = await _add_indexing_run(client)

    response = await client["client"].get(_url(client))

    assert response.status_code == 200
    data = response.json()
    assert data["revision_id"] == client["revision_id"]
    assert data["chunk_set"] is None
    assert data["indexing_run_id"] == run_id


async def test_index_status_ready_chunk_set(client) -> None:
    """ready ChunkSet：返回计数、向量完成数与 profile_hash。"""
    chunk_set = create_chunk_set(
        client["revision_id"], _CHUNK_PROFILE.profile_hash, _CHUNK_PROFILE.config
    ).mark_ready(datetime.now(UTC))
    await client["chunk_set_repo"].add(chunk_set)
    chunks = [
        Chunk(
            chunk_id=str(uuid4()),
            chunk_set_id=chunk_set.chunk_set_id,
            sequence=i,
            text=f"块{i}",
            token_count=5,
            content_hash="h" * 64,
            embedding=[0.0] * 1024 if i == 1 else None,
        )
        for i in (1, 2)
    ]
    await client["chunk_repo"].add_many(chunks)
    run_id = await _add_indexing_run(client)

    response = await client["client"].get(_url(client))

    assert response.status_code == 200
    data = response.json()
    assert data["chunk_set"] == {
        "chunk_set_id": chunk_set.chunk_set_id,
        "status": "ready",
        "chunk_count": 2,
        "embedded_count": 1,
        "profile_hash": _CHUNK_PROFILE.profile_hash,
    }
    assert data["indexing_run_id"] == run_id


async def test_index_status_running_chunk_set(client) -> None:
    """running 状态的 ChunkSet 原样透出，indexing_run_id 可为 null。"""
    chunk_set = create_chunk_set(
        client["revision_id"], _CHUNK_PROFILE.profile_hash, _CHUNK_PROFILE.config
    )
    await client["chunk_set_repo"].add(chunk_set)

    response = await client["client"].get(_url(client))

    assert response.status_code == 200
    data = response.json()
    assert data["chunk_set"]["status"] == "running"
    assert data["chunk_set"]["chunk_count"] == 0
    assert data["chunk_set"]["embedded_count"] == 0
    assert data["indexing_run_id"] is None


async def test_index_status_failed_chunk_set(client) -> None:
    """failed 状态的 ChunkSet 原样透出。"""
    chunk_set = create_chunk_set(
        client["revision_id"], _CHUNK_PROFILE.profile_hash, _CHUNK_PROFILE.config
    ).mark_failed({"type": "ModelRateLimitError", "message": "x"}, datetime.now(UTC))
    await client["chunk_set_repo"].add(chunk_set)

    response = await client["client"].get(_url(client))

    assert response.status_code == 200
    assert response.json()["chunk_set"]["status"] == "failed"


async def test_index_status_unknown_version_returns_404(client) -> None:
    """不存在的版本应返回 404。"""
    response = await client["client"].get(
        f"/api/v1/projects/{client['project_id']}/paper-versions/"
        "00000000-0000-0000-0000-000000000099/index-status"
    )

    assert response.status_code == 404


async def test_index_status_of_other_users_project_returns_404(client) -> None:
    """他人 Project 下的 index-status 查询应返回 404。"""
    response = await client["client"].get(
        f"/api/v1/projects/{client['other_project_id']}"
        f"/paper-versions/{client['version_id']}/index-status"
    )

    assert response.status_code == 404


async def test_index_status_document_not_ready_returns_404(client) -> None:
    """无当前 Revision 的版本应返回 404 document_not_ready。"""
    paper = create_paper(owner_id="user-1")
    await client["paper_repo"].add(paper)
    version = create_paper_version(
        paper_id=paper.paper_id,
        owner_id="user-1",
        file_hash="c" * 64,
        storage_key="k2",
        size_bytes=10,
        content_type="application/pdf",
    )
    await client["version_repo"].add(version)
    await client["relation_repo"].add(
        create_project_paper(client["project_id"], paper.paper_id, version.version_id)
    )

    response = await client["client"].get(
        f"/api/v1/projects/{client['project_id']}"
        f"/paper-versions/{version.version_id}/index-status"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "document_not_ready"
