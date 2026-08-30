"""Phase 2 固定问题集的完整、无网络评测 runner。

运行方式::

    cd backend
    .venv/bin/python tests/evaluation/run_phase2_eval.py \
      --json-output /tmp/phase-02-evaluation.json

评测经真实 IngestionService、IngestionExecutor、IndexingExecutor、Retriever、
RagAnswerExecutor 和 Citation Validator 执行。生产 Fake Parser 会忽略 PDF 内容，
无法评测 manifest 的论文/页码目标，因此本 runner 使用生产 pypdf fallback parser；
Embedding 与 Chat 均使用生产 Fake Adapter，全程不联网、不产生 Provider 费用。
"""

import argparse
import asyncio
import json
import sys
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version as package_version
from pathlib import Path
from time import perf_counter
from typing import Any, cast

from metrics import (
    CitationTarget,
    LocatedItem,
    QuestionEvaluation,
    summarize,
    target_is_covered,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from literature_agent.application.conversation_service import ConversationService
from literature_agent.application.evidence_service import EvidenceService
from literature_agent.application.indexing_executor import IndexingExecutor
from literature_agent.application.ingestion_executor import IngestionExecutor
from literature_agent.application.ingestion_service import IngestionService
from literature_agent.application.model_gateway import ModelGateway
from literature_agent.application.rag_answer_executor import RagAnswerExecutor
from literature_agent.application.retriever import RetrievalResult, Retriever
from literature_agent.application.run_dispatcher import RunDispatcher
from literature_agent.application.run_execution_service import (
    ExecutionOutcome,
    RunExecutionService,
)
from literature_agent.domain.actor import ActorContext
from literature_agent.domain.answer_schema import ClaimDraft, RagAnswerOutput
from literature_agent.domain.chunk_profile import ChunkProfile
from literature_agent.domain.citation_validator import validate_citations
from literature_agent.domain.parse_profile import ParseProfile
from literature_agent.domain.project import create_project
from literature_agent.domain.run import RunStatus, RunType
from literature_agent.domain.tokenization import OFFLINE_TOKENIZER
from literature_agent.infrastructure.config import Settings
from literature_agent.infrastructure.models.fake_models import (
    FakeChatModel,
    FakeEmbeddingModel,
)
from literature_agent.infrastructure.parsing.pypdf_parser import PypdfDocumentParser
from literature_agent.infrastructure.persistence.attempt_repository import (
    SqlalchemyAttemptRepository,
)
from literature_agent.infrastructure.persistence.chunk_repository import (
    SqlalchemyChunkRepository,
)
from literature_agent.infrastructure.persistence.chunk_set_repository import (
    SqlalchemyChunkSetRepository,
)
from literature_agent.infrastructure.persistence.claim_set_repository import (
    SqlalchemyClaimSetRepository,
)
from literature_agent.infrastructure.persistence.conversation_repository import (
    SqlalchemyConversationRepository,
)
from literature_agent.infrastructure.persistence.element_repository import (
    SqlalchemyElementRepository,
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
from literature_agent.infrastructure.persistence.message_repository import (
    SqlalchemyMessageRepository,
)
from literature_agent.infrastructure.persistence.model_invocation_repository import (
    SqlalchemyModelInvocationRepository,
)
from literature_agent.infrastructure.persistence.models import Base
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
from literature_agent.infrastructure.persistence.run_repository import (
    SqlalchemyRunRepository,
)
from literature_agent.infrastructure.storage.local_storage import LocalFileStorage

EVAL_DIR = Path(__file__).parent
OWNER_ID = "phase-02-eval-owner"


class RecordingRetriever:
    """记录 RagAnswerExecutor 实际消费的候选，不重复发起检索。"""

    def __init__(self, delegate: Retriever) -> None:
        self._delegate = delegate
        self.by_run: dict[str, list[RetrievalResult]] = {}
        self.duration_seconds_by_run: dict[str, float] = {}

    async def retrieve_for_scope(
        self,
        *,
        owner_id: str,
        query: str,
        version_scope: list[tuple[str, str]],
        run_id: str | None = None,
    ) -> list[RetrievalResult]:
        started = perf_counter()
        results = await self._delegate.retrieve_for_scope(
            owner_id=owner_id,
            query=query,
            version_scope=version_scope,
            run_id=run_id,
        )
        if run_id is not None:
            self.by_run[run_id] = results
            self.duration_seconds_by_run[run_id] = perf_counter() - started
        return results


def _conversation_service(session_factory) -> ConversationService:
    return ConversationService(
        session_factory=session_factory,
        project_repo_factory=SqlalchemyProjectRepository,
        conversation_repo_factory=SqlalchemyConversationRepository,
        message_repo_factory=SqlalchemyMessageRepository,
        paper_repo_factory=SqlalchemyPaperRepository,
        project_paper_repo_factory=SqlalchemyProjectPaperRepository,
        idempotency_repo_factory=SqlalchemyIdempotencyRepository,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
        claim_set_repo_factory=SqlalchemyClaimSetRepository,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
    )


def _ingestion_service(session_factory, storage: LocalFileStorage) -> IngestionService:
    return IngestionService(
        max_upload_size_bytes=20 * 1024 * 1024,
        session_factory=session_factory,
        project_repo_factory=SqlalchemyProjectRepository,
        paper_repo_factory=SqlalchemyPaperRepository,
        paper_version_repo_factory=SqlalchemyPaperVersionRepository,
        project_paper_repo_factory=SqlalchemyProjectPaperRepository,
        idempotency_repo_factory=SqlalchemyIdempotencyRepository,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        storage=storage,
    )


def _execution_stack(session_factory, storage: LocalFileStorage, settings: Settings):
    gateway = ModelGateway(
        embedding_model=FakeEmbeddingModel(),
        chat_model=FakeChatModel(),
        session_factory=session_factory,
        invocation_repo_factory=SqlalchemyModelInvocationRepository,
    )
    parser_profile = ParseProfile("pypdf", package_version("pypdf"), {})
    ingestion = IngestionExecutor(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        paper_repo_factory=SqlalchemyPaperRepository,
        paper_version_repo_factory=SqlalchemyPaperVersionRepository,
        parse_revision_repo_factory=SqlalchemyParseRevisionRepository,
        element_repo_factory=SqlalchemyElementRepository,
        attempt_repo_factory=SqlalchemyAttemptRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        parser=PypdfDocumentParser(storage),
        profile=parser_profile,
    )
    chunk_profile = ChunkProfile(
        max_tokens=settings.chunk_max_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
        embedding_provider="fake",
        embedding_model="fake-embedding",
        embedding_dimensions=1024,
        tokenizer=OFFLINE_TOKENIZER,
    )
    indexing = IndexingExecutor(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        parse_revision_repo_factory=SqlalchemyParseRevisionRepository,
        element_repo_factory=SqlalchemyElementRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
        chunk_repo_factory=SqlalchemyChunkRepository,
        attempt_repo_factory=SqlalchemyAttemptRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        profile=chunk_profile,
        model_gateway=gateway,
        embedding_batch_size=settings.embedding_batch_size,
    )
    retriever = Retriever(
        session_factory=session_factory,
        chunk_repo_factory=SqlalchemyChunkRepository,
        model_gateway=gateway,
        top_k=settings.retrieval_top_k,
        per_paper_limit=settings.retrieval_per_paper_limit,
        token_budget=settings.retrieval_token_budget,
    )
    recording_retriever = RecordingRetriever(retriever)
    evidence_service = EvidenceService(
        session_factory=session_factory,
        evidence_repo_factory=SqlalchemyEvidenceRepository,
        chunk_set_repo_factory=SqlalchemyChunkSetRepository,
    )
    rag_answer = RagAnswerExecutor(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        conversation_repo_factory=SqlalchemyConversationRepository,
        message_repo_factory=SqlalchemyMessageRepository,
        claim_set_repo_factory=SqlalchemyClaimSetRepository,
        attempt_repo_factory=SqlalchemyAttemptRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        retriever=cast(Any, recording_retriever),
        evidence_service=evidence_service,
        model_gateway=gateway,
        answer_max_output_tokens=settings.answer_max_output_tokens,
        context_token_budget=settings.retrieval_token_budget,
        tokenizer=OFFLINE_TOKENIZER,
    )
    dispatcher = RunDispatcher(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        executors={
            RunType.INGESTION: ingestion.execute,
            RunType.INDEXING: indexing.execute,
            RunType.RAG_ANSWER: rag_answer.execute,
        },
    )
    execution_service = RunExecutionService(
        session_factory=session_factory,
        run_repo_factory=SqlalchemyRunRepository,
        event_repo_factory=SqlalchemyEventRepository,
        attempt_repo_factory=SqlalchemyAttemptRepository,
        outbox_repo_factory=SqlalchemyOutboxRepository,
        executor=dispatcher.execute,
        worker_id="phase-02-evaluation",
        heartbeat_interval_seconds=3600,
    )
    return execution_service, recording_retriever, parser_profile, chunk_profile


async def _execute_required(
    service: RunExecutionService, run_id: str, correlation_id: str
) -> None:
    outcome = await service.execute(run_id, correlation_id=correlation_id)
    if outcome != ExecutionOutcome.COMPLETED:
        raise RuntimeError(f"Run {run_id} 未成功完成：{outcome.value}")


async def _import_corpus(
    *,
    manifest: dict[str, Any],
    actor: ActorContext,
    project_id: str,
    session_factory,
    ingestion_service: IngestionService,
    execution_service: RunExecutionService,
) -> dict[str, tuple[str, str]]:
    corpus_ids: dict[str, tuple[str, str]] = {}
    for corpus_id, info in manifest["corpus"].items():
        path = EVAL_DIR / info["file"]
        uploaded = await ingestion_service.upload_paper_file(
            actor,
            project_id,
            filename=path.name,
            content_type="application/pdf",
            content=path.read_bytes(),
            idempotency_key=f"phase-02-eval-upload-{corpus_id}",
            correlation_id=f"phase-02-eval-upload-{corpus_id}",
        )
        if uploaded.run_id is None:
            raise RuntimeError(f"评测语料 {corpus_id} 未创建 ingestion Run")
        await _execute_required(
            execution_service, uploaded.run_id, f"phase-02-eval-ingest-{corpus_id}"
        )
        async with session_factory() as session:
            version = await SqlalchemyPaperVersionRepository(session).get_by_id(
                uploaded.version_id
            )
            if version is None or version.current_parse_revision_id is None:
                raise RuntimeError(f"评测语料 {corpus_id} 解析后缺少 Revision")
            indexing_run_id = await SqlalchemyRunRepository(
                session
            ).get_latest_indexing_run_id(version.current_parse_revision_id)
        if indexing_run_id is None:
            raise RuntimeError(f"评测语料 {corpus_id} 未创建 indexing Run")
        await _execute_required(
            execution_service, indexing_run_id, f"phase-02-eval-index-{corpus_id}"
        )
        corpus_ids[corpus_id] = (uploaded.paper_id, uploaded.version_id)
        print(f"已通过正常导入/索引链路处理 {corpus_id}")
    return corpus_ids


def _targets(question: dict[str, Any], corpus_ids) -> list[CitationTarget]:
    return [
        CitationTarget(
            paper_id=corpus_ids[item["paper"]][0],
            pages=tuple(item.get("pages", [])),
        )
        for item in question["expected"].get("must_cite", [])
    ]


async def _evaluate_question(
    *,
    question: dict[str, Any],
    actor: ActorContext,
    project_id: str,
    corpus_ids: dict[str, tuple[str, str]],
    conversation_service: ConversationService,
    execution_service: RunExecutionService,
    recording_retriever: RecordingRetriever,
    session_factory,
) -> QuestionEvaluation:
    scope = question["scope"]
    selected_papers = (
        [corpus_ids[item][0] for item in scope.get("papers", [])]
        if scope["mode"] == "selected_papers"
        else None
    )
    conversation = await conversation_service.create_conversation(
        actor,
        project_id,
        title=question["id"],
        scope_mode=scope["mode"],
        paper_ids=selected_papers,
    )
    posted = await conversation_service.post_message(
        actor,
        conversation.conversation.conversation_id,
        content=question["question"],
        idempotency_key=f"phase-02-eval-question-{question['id']}",
        correlation_id=f"phase-02-eval-question-{question['id']}",
    )
    await _execute_required(
        execution_service, posted.run_id, f"phase-02-eval-answer-{question['id']}"
    )

    retrieval_results = recording_retriever.by_run.get(posted.run_id, [])
    retrieval_items = [
        LocatedItem(item.paper_id, item.chunk.page_start, item.chunk.page_end)
        for item in retrieval_results
    ]
    targets = _targets(question, corpus_ids)

    async with session_factory() as session:
        run = await SqlalchemyRunRepository(session).get_by_id(posted.run_id)
        if run is None or run.status != RunStatus.SUCCEEDED:
            raise RuntimeError(f"问题 {question['id']} 的 RAG Run 未成功")
        claim_set = await SqlalchemyClaimSetRepository(session).get_by_run_id(
            posted.run_id
        )
        if claim_set is None:
            raise RuntimeError(f"问题 {question['id']} 缺少 ClaimSet")
        claim_repo = SqlalchemyClaimSetRepository(session)
        claims = await claim_repo.list_claims(claim_set.claim_set_id)
        evidence = await SqlalchemyEvidenceRepository(session).list_by_run(posted.run_id)
        citations_by_claim: dict[str, list[str]] = {}
        for claim in claims:
            citations_by_claim[claim.claim_id] = [
                citation.evidence_id
                for citation in await claim_repo.list_citations(claim.claim_id)
            ]

    output = RagAnswerOutput(
        answer_status=claim_set.answer_status,
        claims=[
            ClaimDraft(
                text=claim.text,
                evidence_ids=citations_by_claim[claim.claim_id],
            )
            for claim in claims
        ],
    )
    validation = validate_citations(output, evidence=evidence, run_id=posted.run_id)
    evidence_by_id = {item.evidence_id: item for item in evidence}
    cited_items = [
        LocatedItem(item.paper_id, item.page_start, item.page_end)
        for evidence_ids in citations_by_claim.values()
        for evidence_id in evidence_ids
        if (item := evidence_by_id.get(evidence_id)) is not None
    ]
    version_scope = {
        (item["paper_id"], item["version_id"])
        for item in run.input_payload["version_scope"]
    }
    scope_valid = all(
        (item.paper_id, item.version_id) in version_scope for item in retrieval_results
    ) and all((item.paper_id, item.version_id) in version_scope for item in evidence)

    result = QuestionEvaluation(
        question_id=question["id"],
        category=question["category"],
        expected_status=question["expected"]["answer_status"],
        actual_status=claim_set.answer_status.value,
        retrieval_hits=sum(target_is_covered(target, retrieval_items) for target in targets),
        retrieval_targets=len(targets),
        citation_hits=sum(target_is_covered(target, cited_items) for target in targets),
        citation_targets=len(targets),
        citation_valid=validation.passed,
        scope_valid=scope_valid,
    )
    print(
        f"{result.question_id}: expected={result.expected_status} "
        f"actual={result.actual_status} retrieval={result.retrieval_hits}/"
        f"{result.retrieval_targets} citations={result.citation_hits}/"
        f"{result.citation_targets} validator={'PASS' if result.citation_valid else 'FAIL'} "
        f"scope={'PASS' if result.scope_valid else 'FAIL'}"
    )
    return result


async def _run(json_output: Path | None) -> int:
    settings = Settings.from_env()
    manifest = json.loads((EVAL_DIR / "manifest.json").read_text(encoding="utf-8"))
    started_at = datetime.now(UTC)

    with PostgresContainer("pgvector/pgvector:pg18") as postgres, tempfile.TemporaryDirectory(
        prefix="agent-service-phase2-eval-"
    ) as storage_root:
        database_url = postgres.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql+psycopg://"
        )
        engine = create_async_engine(database_url, echo=False)
        async with engine.begin() as connection:
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        storage = LocalFileStorage(storage_root)
        actor = ActorContext(owner_id=OWNER_ID)
        async with session_factory() as session:
            project = create_project(OWNER_ID, "Phase 2 固定评测", "")
            await SqlalchemyProjectRepository(session).add(project)
            await session.commit()

        ingestion_service = _ingestion_service(session_factory, storage)
        execution_service, recording_retriever, parser_profile, chunk_profile = (
            _execution_stack(session_factory, storage, settings)
        )
        import_started = perf_counter()
        corpus_ids = await _import_corpus(
            manifest=manifest,
            actor=actor,
            project_id=project.project_id,
            session_factory=session_factory,
            ingestion_service=ingestion_service,
            execution_service=execution_service,
        )
        import_duration_seconds = perf_counter() - import_started
        conversation_service = _conversation_service(session_factory)
        results = []
        rag_duration_seconds_by_question: dict[str, float] = {}
        retrieval_duration_seconds_by_question: dict[str, float] = {}
        for question in manifest["questions"]:
            question_started = perf_counter()
            results.append(
                await _evaluate_question(
                    question=question,
                    actor=actor,
                    project_id=project.project_id,
                    corpus_ids=corpus_ids,
                    conversation_service=conversation_service,
                    execution_service=execution_service,
                    recording_retriever=recording_retriever,
                    session_factory=session_factory,
                )
            )
            rag_duration_seconds_by_question[question["id"]] = (
                perf_counter() - question_started
            )
            latest_run_id = next(reversed(recording_retriever.duration_seconds_by_run))
            retrieval_duration_seconds_by_question[question["id"]] = (
                recording_retriever.duration_seconds_by_run[latest_run_id]
            )
        summary = summarize(results)
        async with session_factory() as session:
            chunk_count = int(
                (await session.execute(text("SELECT count(*) FROM chunks"))).scalar_one()
            )
            element_count = int(
                (
                    await session.execute(text("SELECT count(*) FROM document_elements"))
                ).scalar_one()
            )
            postgres_version = str(
                (await session.execute(text("SHOW server_version"))).scalar_one()
            )
        await engine.dispose()

    report = {
        "schema_version": 1,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "manifest_version": manifest["version"],
        "providers": {
            "parser": {
                "provider": parser_profile.parser_name,
                "version": parser_profile.parser_version,
                "mode": "deterministic_local_fallback",
            },
            "embedding": {"provider": "fake", "model": "fake-embedding"},
            "chat": {"provider": "fake", "model": "fake-chat"},
        },
        "parameters": {
            "chunk_max_tokens": chunk_profile.max_tokens,
            "chunk_overlap_tokens": chunk_profile.overlap_tokens,
            "retrieval_top_k": settings.retrieval_top_k,
            "retrieval_per_paper_limit": settings.retrieval_per_paper_limit,
            "retrieval_token_budget": settings.retrieval_token_budget,
        },
        "summary": asdict(summary),
        "measurements": {
            "corpus_count": len(corpus_ids),
            "element_count": element_count,
            "chunk_count": chunk_count,
            "embedding_dimensions": chunk_profile.embedding_dimensions,
            "cache_state": "cold_ephemeral_database_and_storage",
            "postgres_version": postgres_version,
            "import_parse_index_total_seconds": round(import_duration_seconds, 6),
            "retrieval_seconds_by_question": {
                question_id: round(duration, 6)
                for question_id, duration in retrieval_duration_seconds_by_question.items()
            },
            "rag_total_seconds_by_question": {
                question_id: round(duration, 6)
                for question_id, duration in rag_duration_seconds_by_question.items()
            },
        },
        "questions": [asdict(item) for item in results],
        "limitations": [
            "pypdf fallback 只提供页级 Paragraph，不提供 Docling 版面与章节结构。",
            "Fake Embedding 只表达词汇重叠，不代表真实语义检索质量。",
            "Fake Chat 只保证结构化引用合法，不判断 Evidence 是否足以回答问题。",
            "本报告不包含 Groundedness、性能或真实 Provider 质量结论。",
        ],
    }
    if json_output is not None:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"JSON 报告：{json_output}")
    print("汇总：" + json.dumps(report["summary"], ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 Phase 2 固定 RAG/Citation 评测")
    parser.add_argument(
        "--json-output",
        type=Path,
        help="可选：把含逐题结果的 JSON 报告写入指定路径",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.json_output))


if __name__ == "__main__":
    sys.exit(main())
