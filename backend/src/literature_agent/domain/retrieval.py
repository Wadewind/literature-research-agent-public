"""Hybrid Retrieval 领域纯函数（切片 6）。

包含两路检索结果的 RRF 合并、每篇论文结果上限与总 Token 预算截断。
全部为确定性纯函数，不依赖数据库与外部服务，便于单测。
"""

from dataclasses import dataclass

from literature_agent.domain.chunk import Chunk

# RRF 平滑常数（2026-08-20 定稿：k=60）
RRF_K = 60


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    """单路检索的有序候选（rank 由所在列表顺序表达，从 1 开始）。

    属性:
        chunk_id: Chunk 标识符。
        paper_id: 所属 Paper（用于每篇上限过滤）。
        token_count: Chunk token 数（用于预算截断）。
    """

    chunk_id: str
    paper_id: str
    token_count: int


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    """RRF 合并后的候选。

    属性:
        chunk_id: Chunk 标识符。
        paper_id: 所属 Paper。
        token_count: Chunk token 数。
        semantic_rank: 向量检索路排名（1 起）；未命中为 None。
        fts_rank: 全文检索路排名（1 起）；未命中为 None。
        rrf_score: RRF 合并得分。
    """

    chunk_id: str
    paper_id: str
    token_count: int
    semantic_rank: int | None
    fts_rank: int | None
    rrf_score: float


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """Repository 单路检索返回的 Chunk 及其归属（经强过滤链确认）。

    属性:
        chunk: Chunk 领域实体。
        paper_id: 所属 Paper。
        version_id: 所属 PaperVersion（ProjectPaper 选定的版本）。
    """

    chunk: Chunk
    paper_id: str
    version_id: str


def rrf_merge(
    semantic: list[ScoredChunk],
    fts: list[ScoredChunk],
    *,
    k: int = RRF_K,
) -> list[RankedCandidate]:
    """Reciprocal Rank Fusion 合并两路有序候选。

    ``rrf_score = Σ 1/(k + rank)``，rank 从 1 开始；只命中一路时
    另一路不贡献得分且 rank 记为 None。排序按得分降序，平局时依次
    按 semantic rank（未命中排后）、fts rank、chunk_id 稳定排序。

    参数:
        semantic: 向量检索路候选，按相关性降序。
        fts: 全文检索路候选，按 ts_rank 降序。
        k: RRF 平滑常数。

    返回:
        按合并得分降序的候选列表。
    """
    if k < 1:
        raise ValueError(f"RRF k 必须 >= 1: {k}")
    info: dict[str, ScoredChunk] = {}
    semantic_ranks: dict[str, int] = {}
    fts_ranks: dict[str, int] = {}
    for rank, item in enumerate(semantic, start=1):
        info.setdefault(item.chunk_id, item)
        semantic_ranks[item.chunk_id] = rank
    for rank, item in enumerate(fts, start=1):
        info.setdefault(item.chunk_id, item)
        fts_ranks[item.chunk_id] = rank

    candidates = [
        RankedCandidate(
            chunk_id=chunk_id,
            paper_id=info[chunk_id].paper_id,
            token_count=info[chunk_id].token_count,
            semantic_rank=semantic_ranks.get(chunk_id),
            fts_rank=fts_ranks.get(chunk_id),
            rrf_score=(1 / (k + semantic_ranks[chunk_id]) if chunk_id in semantic_ranks else 0.0)
            + (1 / (k + fts_ranks[chunk_id]) if chunk_id in fts_ranks else 0.0),
        )
        for chunk_id in info
    ]
    candidates.sort(
        key=lambda c: (
            -c.rrf_score,
            c.semantic_rank if c.semantic_rank is not None else len(semantic) + 1,
            c.fts_rank if c.fts_rank is not None else len(fts) + 1,
            c.chunk_id,
        )
    )
    return candidates


def apply_per_paper_limit(
    candidates: list[RankedCandidate],
    limit: int,
) -> list[RankedCandidate]:
    """每篇论文最多保留 ``limit`` 条候选，保持原排序。"""
    if limit < 1:
        raise ValueError(f"per_paper_limit 必须 >= 1: {limit}")
    counts: dict[str, int] = {}
    kept: list[RankedCandidate] = []
    for candidate in candidates:
        count = counts.get(candidate.paper_id, 0)
        if count >= limit:
            continue
        counts[candidate.paper_id] = count + 1
        kept.append(candidate)
    return kept


def apply_token_budget(
    candidates: list[RankedCandidate],
    budget: int,
) -> list[RankedCandidate]:
    """按 Chunk ``token_count`` 累计截断，保持原排序。

    逐个累计 token 数，加入后超出预算的候选被跳过（后续更小的
    候选仍可入选）；预算为 0 时返回空列表。
    """
    if budget < 0:
        raise ValueError(f"token_budget 必须 >= 0: {budget}")
    used = 0
    kept: list[RankedCandidate] = []
    for candidate in candidates:
        if used + candidate.token_count > budget:
            continue
        used += candidate.token_count
        kept.append(candidate)
    return kept
