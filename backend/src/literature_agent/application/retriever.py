"""Hybrid Retrieval 应用服务（切片 6）。

编排一次 Project 范围内的混合检索：经 ModelGateway 生成查询向量
（记录 model_invocations）→ 在同一只读短事务内执行语义/全文两路
SQL 检索（强过滤链在 SQL 内完成）→ domain 纯函数 RRF 合并、每篇
论文上限与 Token 预算截断 → 返回带各路排名与最终排序的结果。

本服务只做检索编排，不做授权判定（调用方提供 owner_id/project_id，
SQL 强过滤保证范围）；切片 8 的 rag_answer Run 接线时传入 run_id。
"""

import logging
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TypeVar

from literature_agent.application.model_gateway import ModelGateway
from literature_agent.application.ports.chunk_repository import ChunkRepository
from literature_agent.application.ports.session import Session
from literature_agent.domain.chunk import Chunk
from literature_agent.domain.retrieval import (
    RetrievedChunk,
    ScoredChunk,
    apply_per_paper_limit,
    apply_token_budget,
    rrf_merge,
)
from literature_agent.metrics import metrics
from literature_agent.observability import log_event

TSession = TypeVar("TSession", bound=Session)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """一条最终检索候选。

    属性:
        chunk: Chunk 领域实体（含 text/token_count/section_path/页码）。
        paper_id: 所属 Paper。
        version_id: 所属 PaperVersion（ProjectPaper 选定版本）。
        semantic_rank: 语义路排名（1 起）；未命中为 None。
        fts_rank: 全文路排名（1 起）；未命中为 None。
        rrf_score: RRF 合并得分。
        rank: 最终结果内排名（1 起，上限与预算截断后重新编号）。
    """

    chunk: Chunk
    paper_id: str
    version_id: str
    semantic_rank: int | None
    fts_rank: int | None
    rrf_score: float
    rank: int


class Retriever[TSession: Session]:
    """Project-scoped Hybrid Retrieval 编排服务。

    不变量:
        - 强过滤只在 SQL 内完成（Repository 两路查询），不在应用层
          事后删除越权结果；
        - 查询向量生成经 ModelGateway 记录调用（run_id 可空）；
        - 模型调用不发生在数据库事务内；两路检索在同一只读短事务中；
        - 合并、每篇上限与预算截断全部为确定性 domain 纯函数。
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        chunk_repo_factory: Callable[[TSession], ChunkRepository],
        model_gateway: ModelGateway[TSession],
        top_k: int = 20,
        per_paper_limit: int = 5,
        token_budget: int = 3000,
    ) -> None:
        """初始化 Retriever。

        参数:
            session_factory: 返回异步上下文管理器的工厂（只读短事务）。
            chunk_repo_factory: 根据 session 创建 ChunkRepository 的工厂。
            model_gateway: 模型调用入口（统一计时与调用记录）。
            top_k: 各路检索 Top-K。
            per_paper_limit: 每篇论文进入最终结果的上限。
            token_budget: 最终结果的总 Token 预算（按 chunk token_count 累计）。
        """
        if top_k < 1:
            raise ValueError(f"top_k 必须 >= 1: {top_k}")
        if per_paper_limit < 1:
            raise ValueError(f"per_paper_limit 必须 >= 1: {per_paper_limit}")
        if token_budget < 0:
            raise ValueError(f"token_budget 必须 >= 0: {token_budget}")
        self._session_factory = session_factory
        self._chunk_repo_factory = chunk_repo_factory
        self._model_gateway = model_gateway
        self._top_k = top_k
        self._per_paper_limit = per_paper_limit
        self._token_budget = token_budget

    async def retrieve(
        self,
        *,
        owner_id: str,
        project_id: str,
        query: str,
        selected_paper_ids: list[str] | None = None,
        run_id: str | None = None,
    ) -> list[RetrievalResult]:
        """执行一次混合检索，返回合并截断后的有序候选。

        参数:
            owner_id: 所有者（SQL 强过滤链的第一环）。
            project_id: 检索所属 Project。
            query: 原始问题；空查询（含纯空白）直接报错。
            selected_paper_ids: selected_papers 模式的 Paper 子集；
                None 表示整个 Project 范围。
            run_id: 关联的 rag_answer Run（切片 8 接线时传入）。

        异常:
            ValueError: 查询为空。
            ModelError: 查询向量生成失败（已记录后原样抛出）。
        """
        query = query.strip()
        if not query:
            raise ValueError("查询不能为空")
        started = time.monotonic()

        # 模型调用不发生在数据库事务内；空结果（零向量）同样允许检索
        embedding = await self._model_gateway.embed([query], run_id=run_id)
        query_vector = embedding.vectors[0]

        async with self._session_factory() as session:
            chunk_repo = self._chunk_repo_factory(session)
            semantic = await chunk_repo.search_semantic(
                owner_id=owner_id,
                project_id=project_id,
                query_vector=query_vector,
                limit=self._top_k,
                paper_ids=selected_paper_ids,
            )
            fts = await chunk_repo.search_fulltext(
                owner_id=owner_id,
                project_id=project_id,
                query=query,
                limit=self._top_k,
                paper_ids=selected_paper_ids,
            )

        result = self._merge_and_rank(
            semantic,
            fts,
            project_id=project_id,
            run_id=run_id,
        )
        scope = "selected_papers" if selected_paper_ids is not None else "project"
        metrics.record_retrieval(scope, time.monotonic() - started, len(result))
        return result

    async def retrieve_for_scope(
        self,
        *,
        owner_id: str,
        query: str,
        version_scope: list[tuple[str, str]],
        chunk_set_scope: list[str] | None = None,
        run_id: str | None = None,
    ) -> list[RetrievalResult]:
        """按 Run 固化的版本范围快照执行混合检索（切片 8）。

        与 ``retrieve`` 的差别：不按 ``project_papers`` 当前收录关系
        过滤，只按显式 ``(paper_id, version_id)`` 快照集合过滤——
        Paper 被移出 Project 后，本次 Run 仍按快照检索完（快照语义
        优先）。owner 校验与 ready ChunkSet 过滤保留。

        参数:
            owner_id: 所有者（SQL 过滤保留）。
            query: 原始问题；空查询（含纯空白）直接报错。
            version_scope: Run ``input_payload`` 固化的版本范围快照；
                空快照直接返回空列表，不调用模型。
            run_id: 关联的 rag_answer Run（查询向量调用记录）。

        异常:
            ValueError: 查询为空。
            ModelError: 查询向量生成失败（已记录后原样抛出）。
        """
        query = query.strip()
        if not query:
            raise ValueError("查询不能为空")
        started = time.monotonic()
        if not version_scope:
            metrics.record_retrieval("version_snapshot", time.monotonic() - started, 0)
            return []

        embedding = await self._model_gateway.embed([query], run_id=run_id)
        query_vector = embedding.vectors[0]

        async with self._session_factory() as session:
            chunk_repo = self._chunk_repo_factory(session)
            semantic = await chunk_repo.search_semantic_by_scope(
                owner_id=owner_id,
                query_vector=query_vector,
                limit=self._top_k,
                version_scope=version_scope,
                chunk_set_scope=chunk_set_scope,
            )
            fts = await chunk_repo.search_fulltext_by_scope(
                owner_id=owner_id,
                query=query,
                limit=self._top_k,
                version_scope=version_scope,
                chunk_set_scope=chunk_set_scope,
            )

        result = self._merge_and_rank(semantic, fts, run_id=run_id)
        metrics.record_retrieval("version_snapshot", time.monotonic() - started, len(result))
        return result

    def _merge_and_rank(
        self,
        semantic: list[RetrievedChunk],
        fts: list[RetrievedChunk],
        *,
        project_id: str | None = None,
        run_id: str | None = None,
    ) -> list[RetrievalResult]:
        """两路候选 RRF 合并、每篇上限与预算截断、重新编号排名。"""
        candidates = rrf_merge(
            [
                ScoredChunk(
                    chunk_id=r.chunk.chunk_id,
                    paper_id=r.paper_id,
                    token_count=r.chunk.token_count,
                )
                for r in semantic
            ],
            [
                ScoredChunk(
                    chunk_id=r.chunk.chunk_id,
                    paper_id=r.paper_id,
                    token_count=r.chunk.token_count,
                )
                for r in fts
            ],
        )
        merged_count = len(candidates)
        candidates = apply_per_paper_limit(candidates, self._per_paper_limit)
        candidates = apply_token_budget(candidates, self._token_budget)

        by_chunk_id = {r.chunk.chunk_id: r for r in [*semantic, *fts]}
        results = [
            RetrievalResult(
                chunk=by_chunk_id[c.chunk_id].chunk,
                paper_id=c.paper_id,
                version_id=by_chunk_id[c.chunk_id].version_id,
                semantic_rank=c.semantic_rank,
                fts_rank=c.fts_rank,
                rrf_score=c.rrf_score,
                rank=index,
            )
            for index, c in enumerate(candidates, start=1)
        ]
        # 日志只记录候选数量摘要，不记录问题文本或 Chunk 内容
        log_event(
            logger,
            logging.INFO,
            "retrieval_completed",
            operation="hybrid",
            project_id=project_id,
            run_id=run_id,
            semantic_count=len(semantic),
            fulltext_count=len(fts),
            merged_count=merged_count,
            evidence_count=len(results),
        )
        return results
