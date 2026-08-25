"""Review Project-scoped API 契约测试。"""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest_asyncio
from fastapi.testclient import TestClient

from literature_agent.api.dependencies import get_actor
from literature_agent.api.reviews import (
    get_outline_input_service,
    get_review_query_service,
    get_review_run_service,
    get_review_workflow_service,
)
from literature_agent.application.review_workflow_service import CreateReviewRunResult
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.exceptions import RunNotFoundError
from literature_agent.domain.review import (
    ReviewOutputType,
    ReviewStage,
    create_review_output,
)
from literature_agent.domain.run import RunStatus
from literature_agent.main import create_app


class _Workflow:
    correlation_id = None

    async def create_review_run(self, actor, project_id, question, key, correlation_id):
        self.correlation_id = correlation_id
        assert actor.owner_id == "user-1"
        assert (project_id, question, key, correlation_id) == (
            "project-1",
            "研究问题",
            "create-1",
            "review-create-1",
        )
        return CreateReviewRunResult("review-1", "queued")


class _Query:
    def __init__(self) -> None:
        self.output_calls = []
        self.output_value = create_review_output(
            review_run_id="review-1",
            output_type=ReviewOutputType.EVIDENCE_MATRIX,
            output_key="evidence-matrix",
            version=1,
            schema_version="evidence-matrix.v1",
            payload={"rows": []},
            idempotency_key="matrix",
        )

    async def output(self, actor, project_id, run_id, output_type, output_key):
        self.output_calls.append((output_type, output_key))
        if actor.owner_id != "user-1" or project_id != "project-1" or run_id != "review-1":
            raise RunNotFoundError(run_id)
        return (
            self.output_value
            if output_type is ReviewOutputType.EVIDENCE_MATRIX and output_key == "evidence-matrix"
            else None
        )

    @staticmethod
    def _scope(actor, project_id, run_id):
        if actor.owner_id != "user-1" or project_id != "project-1" or run_id != "review-1":
            raise RunNotFoundError(run_id)

    async def detail(self, actor, project_id, run_id):
        self._scope(actor, project_id, run_id)
        return _Row(run_id="review-1"), _Row(run_id="review-1"), [], None

    async def list_reviews(self, actor, project_id):
        if actor.owner_id != "user-1" or project_id != "project-1":
            return []
        return [(_ListRun(), _ListReview())]

    async def sources(self, actor, project_id, run_id):
        self._scope(actor, project_id, run_id)
        return [_Row(source_id="source-1")]

    async def artifacts(self, actor, project_id, run_id):
        self._scope(actor, project_id, run_id)
        return [_Row(artifact_id="artifact-1")]

    async def sections(self, actor, project_id, run_id):
        self._scope(actor, project_id, run_id)
        return [
            create_review_output(
                review_run_id="review-1",
                output_type=ReviewOutputType.SECTION,
                output_key="section:methods",
                version=1,
                schema_version="section.v1",
                payload={"section_key": "methods", "claims": []},
                idempotency_key="section-methods",
            )
        ]

    async def artifact_content(self, actor, project_id, run_id, artifact_id):
        self._scope(actor, project_id, run_id)
        if artifact_id != "artifact-1":
            raise RunNotFoundError(artifact_id)
        return _Artifact(), b"# Review\n"


class _OutlineInput:
    def __init__(self) -> None:
        self.kwargs = None

    async def submit(self, **kwargs):
        self.kwargs = kwargs
        return _SubmitResult(True)


@dataclass(frozen=True)
class _SubmitResult:
    accepted: bool


class _RunService:
    def __init__(self) -> None:
        self.cancelled = False
        self.correlation_id = None

    async def cancel_run(self, *_args):
        self.cancelled = True
        self.correlation_id = _args[-1]

    async def list_events(self, actor, run_id, after_sequence, limit):
        assert (actor.owner_id, run_id, after_sequence, limit) == (
            "user-1",
            "review-1",
            2,
            5,
        )
        return [_Event()]


@dataclass(frozen=True)
class _Row:
    run_id: str | None = None
    source_id: str | None = None
    artifact_id: str | None = None


@dataclass(frozen=True)
class _ListRun:
    run_id: str = "review-1"
    status: RunStatus = RunStatus.QUEUED
    created_at: datetime = datetime(2026, 8, 23, tzinfo=UTC)
    updated_at: datetime = datetime(2026, 8, 23, tzinfo=UTC)


@dataclass(frozen=True)
class _ListReview:
    research_question: str = "研究问题"
    current_stage: ReviewStage = ReviewStage.FORMULATE_SEARCH_STRATEGY


@dataclass(frozen=True)
class _Artifact:
    artifact_id: str = "artifact-1"
    media_type: str = "text/markdown"
    content_hash: str = "a" * 64


@dataclass(frozen=True)
class _Event:
    sequence: int = 3
    event_type: str = "review_artifact_created"


@pytest_asyncio.fixture
async def client():
    app = create_app()
    query = _Query()
    outline = _OutlineInput()
    runs = _RunService()
    app.dependency_overrides[get_actor] = lambda: ActorContext("user-1")
    workflow = _Workflow()
    app.dependency_overrides[get_review_workflow_service] = lambda: workflow
    app.dependency_overrides[get_review_query_service] = lambda: query
    app.dependency_overrides[get_outline_input_service] = lambda: outline
    app.dependency_overrides[get_review_run_service] = lambda: runs
    with TestClient(app) as test_client:
        yield test_client, query, outline, runs, workflow
    app.dependency_overrides.clear()


def test_create_requires_idempotency_key_and_returns_created(client) -> None:
    test_client, *_ = client
    missing = test_client.post(
        "/api/v1/projects/project-1/reviews", json={"research_question": "研究问题"}
    )
    assert missing.status_code == 400

    response = test_client.post(
        "/api/v1/projects/project-1/reviews",
        json={"research_question": "研究问题"},
        headers={
            "Idempotency-Key": "create-1",
            "X-Correlation-ID": "review-create-1",
        },
    )
    assert response.status_code == 202
    assert response.json() == {"run_id": "review-1", "status": "queued", "reused": False}


def test_list_reviews_is_project_scoped(client) -> None:
    test_client, *_ = client
    response = test_client.get("/api/v1/projects/project-1/reviews")
    assert response.status_code == 200
    assert response.json() == [
        {
            "run_id": "review-1",
            "status": "queued",
            "research_question": "研究问题",
            "current_stage": "formulate_search_strategy",
            "created_at": "2026-08-23T00:00:00+00:00",
            "updated_at": "2026-08-23T00:00:00+00:00",
        }
    ]
    assert test_client.get("/api/v1/projects/project-2/reviews").json() == []


def test_matrix_is_project_scoped_and_missing_outline_is_404(client) -> None:
    test_client, query, *_ = client
    response = test_client.get("/api/v1/projects/project-1/reviews/review-1/evidence-matrix")
    assert response.status_code == 200
    assert response.json()["payload"] == {"rows": []}
    assert query.output_calls[-1] == (
        ReviewOutputType.EVIDENCE_MATRIX,
        "evidence-matrix",
    )

    hidden = test_client.get("/api/v1/projects/other/reviews/review-1/evidence-matrix")
    assert hidden.status_code == 404
    outline = test_client.get("/api/v1/projects/project-1/reviews/review-1/outline")
    assert outline.status_code == 404
    assert query.output_calls[-1] == (ReviewOutputType.OUTLINE, "outline")


def test_outline_input_passes_request_and_output_versions(client) -> None:
    test_client, _, outline, _, _ = client
    response = test_client.post(
        "/api/v1/projects/project-1/reviews/review-1/outline-input",
        headers={
            "Idempotency-Key": "input-1",
            "X-Correlation-ID": "outline-input-1",
        },
        json={
            "request_id": "request-1",
            "request_version": 1,
            "outline_output_id": "outline-1",
            "action": "approve",
            "payload": {},
        },
    )
    assert response.status_code == 200
    assert outline.kwargs["outline_output_id"] == "outline-1"
    assert outline.kwargs["owner_id"] == "user-1"
    assert outline.kwargs["idempotency_key"] == "input-1"
    assert outline.kwargs["correlation_id"] == "outline-input-1"


def test_detail_sources_artifacts_download_cancel_and_event_cursor(client) -> None:
    test_client, _, _, runs, _ = client
    base = "/api/v1/projects/project-1/reviews/review-1"

    assert test_client.get(base).json()["run"]["run_id"] == "review-1"
    assert test_client.get(f"{base}/sources").json() == [
        {"run_id": None, "source_id": "source-1", "artifact_id": None}
    ]
    assert test_client.get(f"{base}/artifacts").json()[0]["artifact_id"] == "artifact-1"
    content = test_client.get(f"{base}/artifacts/artifact-1/content")
    assert content.content == b"# Review\n"
    assert content.headers["etag"] == '"' + "a" * 64 + '"'
    events = test_client.get(f"{base}/events?after_sequence=2&limit=5")
    assert events.json() == [{"sequence": 3, "event_type": "review_artifact_created"}]
    assert test_client.get(f"{base}/events?after_sequence=-1").status_code == 422
    assert (
        test_client.post(
            f"{base}/cancel", headers={"X-Correlation-ID": "review-cancel-1"}
        ).status_code
        == 202
    )
    assert runs.cancelled
    assert runs.correlation_id == "review-cancel-1"


def test_sections_are_project_scoped_and_return_only_structured_outputs(client) -> None:
    test_client, *_ = client
    base = "/api/v1/projects/project-1/reviews/review-1"

    response = test_client.get(f"{base}/sections")

    assert response.status_code == 200
    assert response.json()[0]["output_type"] == "section"
    assert response.json()[0]["payload"] == {"section_key": "methods", "claims": []}
    assert test_client.get(
        "/api/v1/projects/project-2/reviews/review-1/sections"
    ).status_code == 404
