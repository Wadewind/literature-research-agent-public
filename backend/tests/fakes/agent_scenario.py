"""Phase 5 Agent 分层行为测试共用的最小 PostgreSQL 场景。"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from literature_agent.application.agent_session_service import AgentSessionService
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.chunk import create_chunk_set
from literature_agent.domain.mcp_configuration import McpCatalog
from literature_agent.domain.paper import create_paper
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.domain.parse_revision import create_parse_revision
from literature_agent.domain.project import Project, create_project
from literature_agent.domain.project_paper import create_project_paper
from literature_agent.domain.review import (
    ReviewOutput,
    ReviewOutputType,
    create_review_output,
    create_review_run,
)
from literature_agent.domain.run import RunType, create_run
from literature_agent.domain.skill_configuration import SkillVersion
from literature_agent.infrastructure.persistence.agent_repository import (
    SqlalchemyAgentRepository,
)
from literature_agent.infrastructure.persistence.chunk_set_repository import (
    SqlalchemyChunkSetRepository,
)
from literature_agent.infrastructure.persistence.claim_set_repository import (
    SqlalchemyClaimSetRepository,
)
from literature_agent.infrastructure.persistence.event_repository import (
    SqlalchemyEventRepository,
)
from literature_agent.infrastructure.persistence.evidence_repository import (
    SqlalchemyEvidenceRepository,
)
from literature_agent.infrastructure.persistence.idempotency_repository import (
    SqlalchemyIdempotencyRepository,
)
from literature_agent.infrastructure.persistence.mcp_profile_repository import (
    SqlalchemyMcpProfileRepository,
)
from literature_agent.infrastructure.persistence.outbox_repository import (
    SqlalchemyOutboxRepository,
)
from literature_agent.infrastructure.persistence.paper_repository import (
    SqlalchemyPaperRepository,
)
from literature_agent.infrastructure.persistence.paper_version_repository import (
    SqlalchemyPaperVersionRepository,
)
from literature_agent.infrastructure.persistence.parse_revision_repository import (
    SqlalchemyParseRevisionRepository,
)
from literature_agent.infrastructure.persistence.project_paper_repository import (
    SqlalchemyProjectPaperRepository,
)
from literature_agent.infrastructure.persistence.project_repository import (
    SqlalchemyProjectRepository,
)
from literature_agent.infrastructure.persistence.review_repository import (
    SqlalchemyReviewRepository,
)
from literature_agent.infrastructure.persistence.run_repository import SqlalchemyRunRepository
from literature_agent.infrastructure.persistence.skill_repository import SqlalchemySkillRepository


@dataclass(frozen=True, slots=True)
class AgentScenario:
    factory: async_sessionmaker[AsyncSession]
    actor: ActorContext
    project: Project
    matrix: ReviewOutput
    chunk_set_id: str


async def seed_agent_scenario(db_engine, *, owner_id: str = "agent-owner") -> AgentScenario:
    """建立一个含 READY ChunkSet 与 Evidence Matrix 的最小 Project。"""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    actor = ActorContext(owner_id=owner_id)
    async with factory() as session:
        project = create_project(owner_id=owner_id, name="Agent 分层测试", description="")
        await SqlalchemyProjectRepository(session).add(project)
        paper = create_paper(owner_id)
        await SqlalchemyPaperRepository(session).add(paper)
        await session.flush()
        version = create_paper_version(
            paper.paper_id,
            owner_id,
            "a" * 64,
            "papers/a.pdf",
            10,
            "application/pdf",
        )
        await SqlalchemyPaperVersionRepository(session).add(version)
        await session.flush()
        await SqlalchemyProjectPaperRepository(session).add(
            create_project_paper(project.project_id, paper.paper_id, version.version_id)
        )
        revision = create_parse_revision(version.version_id, "fake", "1.0", "b" * 64)
        await SqlalchemyParseRevisionRepository(session).add(
            revision.mark_succeeded(datetime.now(UTC))
        )
        await session.flush()
        chunk_set = create_chunk_set(revision.revision_id, "c" * 64).mark_ready(datetime.now(UTC))
        await SqlalchemyChunkSetRepository(session).add(chunk_set)
        review_run = create_run(project.project_id, owner_id, RunType.REVIEW)
        await SqlalchemyRunRepository(session).add(review_run)
        await session.flush()
        review_repo = SqlalchemyReviewRepository(session)
        await review_repo.add_review_run(
            create_review_run(
                run_id=review_run.run_id,
                research_question="研究问题",
                workflow_version="review.v1",
                model_profile_version="model.v1",
                prompt_versions={"matrix": "matrix.v1"},
                config_snapshot={},
            )
        )
        await session.flush()
        matrix = create_review_output(
            review_run_id=review_run.run_id,
            output_type=ReviewOutputType.EVIDENCE_MATRIX,
            output_key="evidence-matrix",
            version=1,
            schema_version="evidence-matrix.v1",
            payload={"rows": []},
            idempotency_key="matrix-1",
        )
        await review_repo.add_output(matrix)
        await session.commit()
    return AgentScenario(factory, actor, project, matrix, chunk_set.chunk_set_id)


def make_agent_service(
    session_factory,
    *,
    mcp_catalog: McpCatalog | None = None,
    platform_skills: tuple[SkillVersion, ...] = (),
) -> AgentSessionService:
    """用真实 Repository 组装 AgentSessionService。"""
    return AgentSessionService(
        session_factory=session_factory,
        project_repo_factory=SqlalchemyProjectRepository,
        agent_repo_factory=SqlalchemyAgentRepository,
        paper_repo_factory=SqlalchemyPaperRepository,
        project_paper_repo_factory=SqlalchemyProjectPaperRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
        review_repo_factory=SqlalchemyReviewRepository,
        idempotency_repo_factory=SqlalchemyIdempotencyRepository,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        claim_set_repo_factory=SqlalchemyClaimSetRepository,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        mcp_profile_repo_factory=SqlalchemyMcpProfileRepository,
        mcp_catalog=mcp_catalog,
        skill_repo_factory=SqlalchemySkillRepository,
        platform_skills=platform_skills,
    )
