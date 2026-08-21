"""切片 6 检索校准实验脚本（手动运行，不作为自动测试默认运行）。

运行方式::

    cd backend && .venv/bin/python tests/evaluation/run_retrieval_eval.py

流程：Testcontainers 启动 pgvector 库 → 4 篇评测语料 PDF 经 pypdf 解析
为 Element（编号标题识别为 section_heading）→ ChunkBuilder 切分 →
生产侧 Fake Embedding（bag-of-words 哈希向量）写库 → 对 manifest.json
中 answered 类问题（单篇事实 5 + 跨篇综合 3）跑 Retriever，计算期望
paper 的 Recall（期望 paper 且页码覆盖的 Chunk 是否进入最终候选）。

只报告实跑数字；Fake Embedding 只有词汇重叠能力，语义间隙导致的
Recall 不达标如实记录为已知限制，不调参硬凑。
"""

import asyncio
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from literature_agent.application.model_gateway import ModelGateway
from literature_agent.application.retriever import Retriever
from literature_agent.domain.chunk import Chunk, create_chunk_set
from literature_agent.domain.chunk_builder import build_chunks
from literature_agent.domain.chunk_profile import ChunkProfile
from literature_agent.domain.document_element import (
    ElementType,
    ParsedDocument,
    ParsedElement,
    ParsedLocation,
    normalize_parsed_document,
)
from literature_agent.domain.paper import create_paper
from literature_agent.domain.paper_version import create_paper_version
from literature_agent.domain.parse_profile import ParseProfile
from literature_agent.domain.parse_revision import create_parse_revision
from literature_agent.domain.project import create_project
from literature_agent.domain.project_paper import create_project_paper
from literature_agent.infrastructure.config import Settings
from literature_agent.infrastructure.models.fake_models import (
    FakeChatModel,
    FakeEmbeddingModel,
)
from literature_agent.infrastructure.persistence.chunk_repository import (
    SqlalchemyChunkRepository,
)
from literature_agent.infrastructure.persistence.chunk_set_repository import (
    SqlalchemyChunkSetRepository,
)
from literature_agent.infrastructure.persistence.model_invocation_repository import (
    SqlalchemyModelInvocationRepository,
)
from literature_agent.infrastructure.persistence.models import Base
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

EVAL_DIR = Path(__file__).parent
OWNER_ID = "eval-owner"
_PARSE_PROFILE = ParseProfile("pypdf", "eval", {})

# 编号章节标题行（如 "2 The GraphWeave Benchmark"）
_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*)\s+\S")


def _page_elements(page_number: int, text: str, start_sequence: int) -> list[ParsedElement]:
    """把一页的提取文本拆为 Element：编号标题行为 section_heading，
    其余连续非空行合并为段落（折行以空格连接）。"""
    elements: list[ParsedElement] = []
    sequence = start_sequence
    buffer: list[str] = []

    def flush_buffer() -> None:
        nonlocal sequence
        if not buffer:
            return
        sequence += 1
        elements.append(
            ParsedElement(
                element_type=ElementType.PARAGRAPH,
                sequence=sequence,
                text=" ".join(buffer),
                locations=[ParsedLocation(page=page_number)],
            )
        )
        buffer.clear()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            flush_buffer()
            continue
        heading = _HEADING_RE.match(line) if len(line) <= 100 else None
        if heading:
            flush_buffer()
            sequence += 1
            elements.append(
                ParsedElement(
                    element_type=ElementType.SECTION_HEADING,
                    sequence=sequence,
                    text=line,
                    section_path=heading.group(1),
                    locations=[ParsedLocation(page=page_number)],
                )
            )
            continue
        buffer.append(line)
    flush_buffer()
    return elements


def _parse_pdf(path: Path) -> ParsedDocument:
    """用 pypdf 按页提取文本，识别编号标题，组装为 Element 流。"""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    elements: list[ParsedElement] = []
    for page_number, page in enumerate(reader.pages, start=1):
        elements.extend(
            _page_elements(page_number, page.extract_text() or "", len(elements))
        )
    return ParsedDocument(elements=elements, degraded=True, warnings=["layout_missing"])


async def _index_corpus(session, project_id: str, profile: ChunkProfile) -> dict[str, str]:
    """把 4 篇语料 PDF 解析、切分、Embedding 并落库，返回 语料 ID → paper_id。"""
    manifest = json.loads((EVAL_DIR / "manifest.json").read_text(encoding="utf-8"))
    embedding_model = FakeEmbeddingModel()
    paper_ids: dict[str, str] = {}
    for corpus_id, info in manifest["corpus"].items():
        pdf_path = EVAL_DIR / info["file"]
        parsed = _parse_pdf(pdf_path)

        paper = create_paper(owner_id=OWNER_ID)
        await SqlalchemyPaperRepository(session).add(paper)
        await session.flush()
        file_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        version = create_paper_version(
            paper_id=paper.paper_id,
            owner_id=OWNER_ID,
            file_hash=file_hash,
            storage_key=f"eval/{info['file']}",
            size_bytes=pdf_path.stat().st_size,
            content_type="application/pdf",
        )
        await SqlalchemyPaperVersionRepository(session).add(version)
        await session.flush()
        revision = create_parse_revision(
            version.version_id,
            _PARSE_PROFILE.parser_name,
            _PARSE_PROFILE.parser_version,
            _PARSE_PROFILE.profile_hash,
        ).mark_succeeded(datetime.now(UTC))
        await SqlalchemyParseRevisionRepository(session).add(revision)
        await session.flush()

        elements, locations = normalize_parsed_document(revision.revision_id, parsed)
        drafts = build_chunks(elements, locations, profile)
        chunk_set = create_chunk_set(
            revision.revision_id, profile.profile_hash, profile.config
        ).mark_ready(datetime.now(UTC))
        await SqlalchemyChunkSetRepository(session).add(chunk_set)
        await session.flush()

        chunk_repo = SqlalchemyChunkRepository(session)
        chunks = [
            Chunk(
                chunk_id=str(uuid4()),
                chunk_set_id=chunk_set.chunk_set_id,
                sequence=draft.sequence,
                text=draft.text,
                token_count=draft.token_count,
                section_path=draft.section_path,
                page_start=draft.page_start,
                page_end=draft.page_end,
                content_hash=draft.content_hash,
            )
            for draft in drafts
        ]
        await chunk_repo.add_many(chunks)
        await session.flush()
        vectors = (await embedding_model.embed([c.text for c in chunks])).vectors
        await chunk_repo.save_embeddings(
            {c.chunk_id: v for c, v in zip(chunks, vectors, strict=True)}
        )
        await SqlalchemyProjectPaperRepository(session).add(
            create_project_paper(project_id, paper.paper_id, version.version_id)
        )
        await session.commit()
        paper_ids[corpus_id] = paper.paper_id
        print(f"已索引 {corpus_id}: {len(elements)} elements → {len(chunks)} chunks")
    return paper_ids


async def _run() -> int:
    settings = Settings.from_env()
    profile = ChunkProfile(
        max_tokens=settings.chunk_max_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
        embedding_provider="fake",
        embedding_model="fake-embedding",
        embedding_dimensions=1024,
    )
    manifest = json.loads((EVAL_DIR / "manifest.json").read_text(encoding="utf-8"))

    with PostgresContainer("pgvector/pgvector:pg18") as postgres:
        url = postgres.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql+psycopg://"
        )
        engine = create_async_engine(url, echo=False)
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with session_factory() as session:
            project = create_project(owner_id=OWNER_ID, name="检索评测", description="")
            await SqlalchemyProjectRepository(session).add(project)
            await session.commit()
            project_id = project.project_id
        async with session_factory() as session:
            paper_ids = await _index_corpus(session, project_id, profile)

        gateway = ModelGateway(
            embedding_model=FakeEmbeddingModel(),
            chat_model=FakeChatModel(),
            session_factory=session_factory,
            invocation_repo_factory=SqlalchemyModelInvocationRepository,
        )
        retriever = Retriever(
            session_factory=session_factory,
            chunk_repo_factory=SqlalchemyChunkRepository,
            model_gateway=gateway,
            top_k=settings.retrieval_top_k,
            per_paper_limit=settings.retrieval_per_paper_limit,
            token_budget=settings.retrieval_token_budget,
        )
        print(
            f"检索参数: top_k={settings.retrieval_top_k} "
            f"per_paper_limit={settings.retrieval_per_paper_limit} "
            f"token_budget={settings.retrieval_token_budget}；"
            f"chunk: max_tokens={profile.max_tokens} overlap={profile.overlap_tokens}"
        )

        total_entries = 0
        hit_entries = 0
        total_questions = 0
        full_recall_questions = 0
        for question in manifest["questions"]:
            if question["expected"]["answer_status"] != "answered":
                continue
            total_questions += 1
            scope = question["scope"]
            selected = (
                [paper_ids[c] for c in scope["papers"]]
                if scope["mode"] == "selected_papers"
                else None
            )
            results = await retriever.retrieve(
                owner_id=OWNER_ID,
                project_id=project_id,
                query=question["question"],
                selected_paper_ids=selected,
            )
            details: list[str] = []
            question_hits = 0
            for cite in question["expected"]["must_cite"]:
                expected_paper = paper_ids[cite["paper"]]
                expected_pages = cite.get("pages", [])
                hit = any(
                    r.paper_id == expected_paper
                    and (
                        not expected_pages
                        or r.chunk.page_start is None
                        or (
                            r.chunk.page_start <= max(expected_pages)
                            and (r.chunk.page_end or r.chunk.page_start) >= min(expected_pages)
                        )
                    )
                    for r in results
                )
                total_entries += 1
                hit_entries += int(hit)
                question_hits += int(hit)
                details.append(f"{cite['paper']}@p{expected_pages}:{'✓' if hit else '✗'}")
            full_recall = question_hits == len(question["expected"]["must_cite"])
            full_recall_questions += int(full_recall)
            print(
                f"{question['id']} [{'PASS' if full_recall else 'MISS'}] "
                f"candidates={len(results)} " + " ".join(details)
            )
        print(
            f"汇总: 题目级 Recall {full_recall_questions}/{total_questions}，"
            f"must_cite 条目级 Recall {hit_entries}/{total_entries}"
        )
        await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
