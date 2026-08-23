"""Retriever 应用服务测试（切片 6）。

覆盖编排逻辑：查询向量生成与调用记录、两路合并、空查询、
FTS 零命中的纯语义路径、每篇上限与预算截断、范围参数透传。
SQL 强过滤正确性由集成测试覆盖。
"""

from unittest.mock import Mock

import pytest

from literature_agent.application import retriever as retriever_module
from literature_agent.application.model_gateway import ModelGateway
from literature_agent.application.retriever import Retriever
from literature_agent.domain.chunk import Chunk
from literature_agent.domain.model_errors import ModelRateLimitError
from literature_agent.domain.model_invocation import InvocationStatus, ModelCapability
from literature_agent.domain.retrieval import RetrievedChunk
from tests.fakes.fake_chat_model import FakeChatModel
from tests.fakes.fake_chunk_repository import FakeChunkRepository
from tests.fakes.fake_embedding_model import FakeEmbeddingModel
from tests.fakes.fake_model_invocation_repository import FakeModelInvocationRepository
from tests.fakes.fake_project_repository import fake_session


def _retrieved(
    chunk_id: str,
    paper_id: str,
    *,
    token_count: int = 100,
    version_id: str = "v1",
) -> RetrievedChunk:
    """构造一路检索返回的候选。"""
    chunk = Chunk(
        chunk_id=chunk_id,
        chunk_set_id="cs-1",
        sequence=1,
        text=f"text of {chunk_id}",
        token_count=token_count,
        section_path="1 Intro",
        page_start=1,
        page_end=2,
        content_hash=f"hash-{chunk_id}",
    )
    return RetrievedChunk(chunk=chunk, paper_id=paper_id, version_id=version_id)


def _make_retriever(
    chunk_repo: FakeChunkRepository,
    invocation_repo: FakeModelInvocationRepository,
    *,
    embedding_model: FakeEmbeddingModel | None = None,
    **params,
) -> Retriever:
    """构造使用 Fake Port/Repository 的 Retriever。"""
    gateway = ModelGateway(
        embedding_model=embedding_model or FakeEmbeddingModel(),
        chat_model=FakeChatModel(),
        session_factory=fake_session,
        invocation_repo_factory=lambda _session: invocation_repo,
    )
    return Retriever(
        session_factory=fake_session,
        chunk_repo_factory=lambda _session: chunk_repo,
        model_gateway=gateway,
        **params,
    )


async def test_retrieve_merges_two_paths_and_records_invocation(monkeypatch) -> None:
    """编排：一次 embedding 调用（含 run_id 记录）、两路 RRF 合并、字段完整。"""
    chunk_repo = FakeChunkRepository()
    chunk_repo.semantic_results = [_retrieved("a", "p1"), _retrieved("b", "p1")]
    chunk_repo.fts_results = [_retrieved("b", "p1"), _retrieved("c", "p2")]
    invocation_repo = FakeModelInvocationRepository()
    embedding = FakeEmbeddingModel()
    retriever = _make_retriever(chunk_repo, invocation_repo, embedding_model=embedding)
    recorder = Mock()
    monkeypatch.setattr(retriever_module, "metrics", recorder)

    results = await retriever.retrieve(
        owner_id="user-1", project_id="proj-1", query="graph neural networks", run_id="run-9"
    )

    # b 双路命中排第一；a 仅语义路；c 仅全文路
    assert [r.chunk.chunk_id for r in results] == ["b", "a", "c"]
    b, a, c = results
    assert (b.semantic_rank, b.fts_rank, b.rank) == (2, 1, 1)
    assert (a.semantic_rank, a.fts_rank, a.rank) == (1, None, 2)
    assert (c.semantic_rank, c.fts_rank, c.rank) == (None, 2, 3)
    assert b.rrf_score == pytest.approx(1 / 62 + 1 / 61)
    assert b.paper_id == "p1"
    assert b.version_id == "v1"
    assert b.chunk.text == "text of b"
    # embedding 恰好调用一次，内容为原始问题
    assert embedding.calls == [["graph neural networks"]]
    record = invocation_repo.all()[0]
    assert record.run_id == "run-9"
    assert record.capability == ModelCapability.EMBEDDING
    assert record.status == InvocationStatus.SUCCEEDED
    # 两路查询各一次，Top-K 透传
    paths = [call["path"] for call in chunk_repo.search_calls]
    assert paths == ["semantic", "fulltext"]
    assert all(call["limit"] == 20 for call in chunk_repo.search_calls)
    assert recorder.record_retrieval.call_args.args[0] == "project"
    assert recorder.record_retrieval.call_args.args[2] == 3


async def test_empty_query_rejected_without_model_call() -> None:
    """空查询（含纯空白）直接报错，不调用模型也不访问数据库。"""
    chunk_repo = FakeChunkRepository()
    invocation_repo = FakeModelInvocationRepository()
    embedding = FakeEmbeddingModel()
    retriever = _make_retriever(chunk_repo, invocation_repo, embedding_model=embedding)

    with pytest.raises(ValueError, match="查询不能为空"):
        await retriever.retrieve(owner_id="user-1", project_id="proj-1", query="   ")

    assert embedding.calls == []
    assert invocation_repo.all() == []
    assert chunk_repo.search_calls == []


async def test_fts_zero_hits_falls_back_to_pure_semantic() -> None:
    """FTS 零命中时走纯语义路径：fts_rank 全为 None，按语义序返回。"""
    chunk_repo = FakeChunkRepository()
    chunk_repo.semantic_results = [_retrieved("a", "p1"), _retrieved("b", "p1")]
    chunk_repo.fts_results = []
    retriever = _make_retriever(chunk_repo, FakeModelInvocationRepository())

    results = await retriever.retrieve(owner_id="user-1", project_id="proj-1", query="q")

    assert [r.chunk.chunk_id for r in results] == ["a", "b"]
    assert all(r.fts_rank is None for r in results)


async def test_per_paper_limit_and_token_budget_applied() -> None:
    """每篇上限先于预算截断；最终 rank 在截断后重新编号。"""
    chunk_repo = FakeChunkRepository()
    chunk_repo.semantic_results = [
        _retrieved("a", "p1"),
        _retrieved("b", "p1"),
        _retrieved("c", "p2"),
    ]
    retriever = _make_retriever(
        chunk_repo,
        FakeModelInvocationRepository(),
        per_paper_limit=1,
        token_budget=150,
    )

    results = await retriever.retrieve(owner_id="user-1", project_id="proj-1", query="q")

    # 每篇上限后剩 a(p1)/c(p2)；预算 150 只装得下 a（100），c 超出被跳过
    assert [r.chunk.chunk_id for r in results] == ["a"]
    assert results[0].rank == 1


async def test_selected_paper_ids_passed_to_both_paths(monkeypatch) -> None:
    """selected_papers 范围原样透传给两路 SQL 查询。"""
    chunk_repo = FakeChunkRepository()
    retriever = _make_retriever(chunk_repo, FakeModelInvocationRepository())
    recorder = Mock()
    monkeypatch.setattr(retriever_module, "metrics", recorder)

    await retriever.retrieve(
        owner_id="user-1",
        project_id="proj-1",
        query="q",
        selected_paper_ids=["p1", "p2"],
    )

    assert chunk_repo.search_calls[0]["paper_ids"] == ["p1", "p2"]
    assert chunk_repo.search_calls[1]["paper_ids"] == ["p1", "p2"]
    assert recorder.record_retrieval.call_args.args[0] == "selected_papers"


async def test_embedding_failure_propagates_and_is_recorded() -> None:
    """Embedding 失败：记录 failed 后原样抛出，不发起检索。"""
    chunk_repo = FakeChunkRepository()
    invocation_repo = FakeModelInvocationRepository()
    embedding = FakeEmbeddingModel(error=ModelRateLimitError("限流"))
    retriever = _make_retriever(chunk_repo, invocation_repo, embedding_model=embedding)

    with pytest.raises(ModelRateLimitError):
        await retriever.retrieve(owner_id="user-1", project_id="proj-1", query="q")

    record = invocation_repo.all()[0]
    assert record.status == InvocationStatus.FAILED
    assert record.error_type == "ModelRateLimitError"
    assert chunk_repo.search_calls == []


def test_invalid_parameters_rejected() -> None:
    """非法检索参数在构造时直接报错。"""
    with pytest.raises(ValueError, match="top_k"):
        _make_retriever(FakeChunkRepository(), FakeModelInvocationRepository(), top_k=0)
    with pytest.raises(ValueError, match="per_paper_limit"):
        _make_retriever(
            FakeChunkRepository(), FakeModelInvocationRepository(), per_paper_limit=0
        )
    with pytest.raises(ValueError, match="token_budget"):
        _make_retriever(
            FakeChunkRepository(), FakeModelInvocationRepository(), token_budget=-1
        )


async def test_retrieve_for_scope_passes_snapshot_to_both_paths() -> None:
    """快照检索：version_scope/owner/run_id 原样透传给两路 scope 查询。"""
    chunk_repo = FakeChunkRepository()
    chunk_repo.semantic_results = [_retrieved("a", "p1", version_id="v1")]
    invocation_repo = FakeModelInvocationRepository()
    embedding = FakeEmbeddingModel()
    retriever = _make_retriever(chunk_repo, invocation_repo, embedding_model=embedding)
    scope = [("p1", "v1"), ("p2", "v2")]

    results = await retriever.retrieve_for_scope(
        owner_id="user-1", query="graph neural networks",
        version_scope=scope, run_id="run-9",
    )

    assert [r.chunk.chunk_id for r in results] == ["a"]
    assert embedding.calls == [["graph neural networks"]]
    assert invocation_repo.all()[0].run_id == "run-9"
    paths = [call["path"] for call in chunk_repo.search_calls]
    assert paths == ["semantic_by_scope", "fulltext_by_scope"]
    assert all(call["version_scope"] == scope for call in chunk_repo.search_calls)
    assert all(call["owner_id"] == "user-1" for call in chunk_repo.search_calls)


async def test_retrieve_for_scope_empty_snapshot_skips_model(monkeypatch) -> None:
    """空快照直接返回空结果，不调用模型也不访问数据库。"""
    chunk_repo = FakeChunkRepository()
    invocation_repo = FakeModelInvocationRepository()
    embedding = FakeEmbeddingModel()
    retriever = _make_retriever(chunk_repo, invocation_repo, embedding_model=embedding)
    recorder = Mock()
    monkeypatch.setattr(retriever_module, "metrics", recorder)

    results = await retriever.retrieve_for_scope(
        owner_id="user-1", query="q", version_scope=[], run_id="run-9"
    )

    assert results == []
    assert embedding.calls == []
    assert invocation_repo.all() == []
    assert chunk_repo.search_calls == []
    assert recorder.record_retrieval.call_args.args[0] == "version_snapshot"
    assert recorder.record_retrieval.call_args.args[2] == 0
