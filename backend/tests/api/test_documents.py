"""文档内容查询 API 路由测试。"""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from literature_agent.api.dependencies import get_actor
from literature_agent.api.documents import get_document_query_service
from literature_agent.application.document_query_service import DocumentQueryService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.document_element import (
    DocumentElement,
    ElementSourceLocation,
    ElementType,
    compute_content_hash,
)
from literature_agent.domain.paper import create_paper
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.domain.parse_profile import ParseProfile
from literature_agent.domain.parse_revision import create_parse_revision
from literature_agent.domain.project import create_project
from literature_agent.main import create_app
from tests.fakes.fake_element_repository import FakeElementRepository
from tests.fakes.fake_paper_repository import FakePaperRepository
from tests.fakes.fake_paper_version_repository import FakePaperVersionRepository
from tests.fakes.fake_parse_revision_repository import FakeParseRevisionRepository
from tests.fakes.fake_project_repository import FakeProjectRepository, fake_session

_PROFILE = ParseProfile("fake", "1.0", {})


def _element(revision_id: str, sequence: int, element_id: str, **kwargs) -> DocumentElement:
    """构造测试 Element。"""
    text = kwargs.pop("text", f"文本{sequence}")
    element_type = kwargs.pop("element_type", ElementType.PARAGRAPH)
    return DocumentElement(
        element_id=element_id,
        revision_id=revision_id,
        element_type=element_type,
        sequence=sequence,
        text=text,
        content_hash=compute_content_hash(element_type.value, text, {}),
        **kwargs,
    )


@pytest_asyncio.fixture
async def client():
    """提供已注入 Fake 依赖、带完整文档数据的 TestClient。

    返回 ``(client, project_id, version_id, other_project_id)``。
    """
    project_repo = FakeProjectRepository()
    paper_repo = FakePaperRepository()
    version_repo = FakePaperVersionRepository()
    revision_repo = FakeParseRevisionRepository()
    element_repo = FakeElementRepository()

    project = create_project(owner_id="user-1", name="项目A", description="")
    other_project = create_project(owner_id="user-2", name="项目B", description="")
    await project_repo.add(project)
    await project_repo.add(other_project)

    paper = create_paper(owner_id="user-1", project_id=project.project_id)
    await paper_repo.add(paper)
    version = create_paper_version(
        paper_id=paper.paper_id,
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

    # 直接构造带当前指针的 Version
    version = replace(version, current_parse_revision_id=revision.revision_id)
    await version_repo.add(version)

    elements = [
        _element(revision.revision_id, 1, "00000000-0000-0000-0000-000000000001",
                 element_type=ElementType.SECTION_HEADING, text="1 引言", section_path="1"),
        _element(revision.revision_id, 2, "00000000-0000-0000-0000-000000000002",
                 text="段落一", section_path="1"),
        _element(revision.revision_id, 3, "00000000-0000-0000-0000-000000000003",
                 element_type=ElementType.TABLE, text=None, section_path="2"),
    ]
    await element_repo.add_many(elements)
    await element_repo.add_locations(
        [
            ElementSourceLocation(
                location_id="00000000-0000-0000-0000-00000000000a",
                element_id=elements[0].element_id, page=1, parser_ref="fake:p1:e1",
            ),
            ElementSourceLocation(
                location_id="00000000-0000-0000-0000-00000000000b",
                element_id=elements[1].element_id, page=1,
            ),
            ElementSourceLocation(
                location_id="00000000-0000-0000-0000-00000000000c",
                element_id=elements[2].element_id, page=2, bbox=[1.0, 2.0, 3.0, 4.0],
            ),
        ]
    )

    service = DocumentQueryService(
        session_factory=fake_session,
        project_repo_factory=lambda _s: project_repo,
        paper_repo_factory=lambda _s: paper_repo,
        paper_version_repo_factory=lambda _s: version_repo,
        parse_revision_repo_factory=lambda _s: revision_repo,
        element_repo_factory=lambda _s: element_repo,
    )

    app = create_app()
    app.dependency_overrides[get_actor] = lambda: ActorContext(owner_id="user-1")
    app.dependency_overrides[get_document_query_service] = lambda: service

    with TestClient(app) as test_client:
        yield {
            "client": test_client,
            "project_id": project.project_id,
            "version_id": version.version_id,
            "other_project_id": other_project.project_id,
            "version_repo": version_repo,
            "paper_repo": paper_repo,
        }

    app.dependency_overrides.clear()


def _ids(fx):
    """提取 fixture 中的常用字段。"""
    return fx["client"], fx["project_id"], fx["version_id"]


def test_get_document_returns_overview(client) -> None:
    """document 端点应返回 Revision 元数据和章节概览。"""
    test_client, project_id, version_id = _ids(client)

    response = test_client.get(
        f"/api/v1/projects/{project_id}/paper-versions/{version_id}/document"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["parser_name"] == "fake"
    assert data["status"] == "succeeded"
    assert data["element_count"] == 3
    assert data["degraded"] is False
    assert data["warnings"] == []
    assert data["sections"] == [{"section_path": "1", "title": "1 引言"}]


def test_list_elements_returns_locations(client) -> None:
    """elements 端点应返回带来源定位的 Element 列表。"""
    test_client, project_id, version_id = _ids(client)

    response = test_client.get(
        f"/api/v1/projects/{project_id}/paper-versions/{version_id}/elements"
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    assert data[0]["sequence"] == 1
    assert data[0]["locations"][0]["page"] == 1
    table = data[2]
    assert table["element_type"] == "table"
    assert table["locations"][0]["bbox"] == [1.0, 2.0, 3.0, 4.0]


def test_list_elements_filters_by_page_and_type(client) -> None:
    """页码与类型过滤应生效。"""
    test_client, project_id, version_id = _ids(client)

    response = test_client.get(
        f"/api/v1/projects/{project_id}/paper-versions/{version_id}/elements?page=2"
    )
    assert [e["sequence"] for e in response.json()] == [3]

    response = test_client.get(
        f"/api/v1/projects/{project_id}/paper-versions/{version_id}/elements?type=table"
    )
    assert [e["sequence"] for e in response.json()] == [3]

    response = test_client.get(
        f"/api/v1/projects/{project_id}/paper-versions/{version_id}/elements?section=1"
    )
    assert [e["sequence"] for e in response.json()] == [1, 2]


def test_list_elements_rejects_invalid_type(client) -> None:
    """非法 element type 应返回 400。"""
    test_client, project_id, version_id = _ids(client)

    response = test_client.get(
        f"/api/v1/projects/{project_id}/paper-versions/{version_id}/elements?type=unknown"
    )

    assert response.status_code == 400


def test_document_of_other_users_project_returns_404(client) -> None:
    """他人 Project 下的版本查询应返回 404。"""
    test_client = client["client"]
    version_id = client["version_id"]
    other_project_id = client["other_project_id"]

    response = test_client.get(
        f"/api/v1/projects/{other_project_id}/paper-versions/{version_id}/document"
    )

    assert response.status_code == 404


def test_unknown_version_returns_404(client) -> None:
    """不存在的版本应返回 404。"""
    test_client = client["client"]
    project_id = client["project_id"]

    response = test_client.get(
        f"/api/v1/projects/{project_id}/paper-versions/"
        "00000000-0000-0000-0000-000000000099/document"
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_document_not_ready_returns_404(client) -> None:
    """无当前 Revision 的版本应返回 404 document_not_ready。"""
    paper_repo = client["paper_repo"]
    version_repo = client["version_repo"]
    paper = await paper_repo.list_by_project(client["project_id"])
    version = create_paper_version(
        paper_id=paper[0].paper_id,
        file_hash="c" * 64,
        storage_key="k2",
        size_bytes=10,
        content_type="application/pdf",
    )
    await version_repo.add(version)

    response = client["client"].get(
        f"/api/v1/projects/{client['project_id']}/paper-versions/{version.version_id}/document"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "document_not_ready"
