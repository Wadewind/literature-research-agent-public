"""Hybrid Retrieval 领域纯函数测试（切片 6）。

覆盖 RRF 合并（k=60）、每篇论文结果上限与总 Token 预算截断。
"""

import pytest

from literature_agent.domain.retrieval import (
    RRF_K,
    ScoredChunk,
    apply_per_paper_limit,
    apply_token_budget,
    rrf_merge,
)


def _scored(chunk_id: str, paper_id: str = "paper-1", token_count: int = 100) -> ScoredChunk:
    """构造单路检索候选。"""
    return ScoredChunk(chunk_id=chunk_id, paper_id=paper_id, token_count=token_count)


def test_rrf_single_list_hit_uses_k60() -> None:
    """单路命中：score = 1/(60 + rank)，另一路 rank 为 None。"""
    merged = rrf_merge([_scored("a"), _scored("b")], [])
    assert [c.chunk_id for c in merged] == ["a", "b"]
    assert merged[0].rrf_score == pytest.approx(1 / (RRF_K + 1))
    assert merged[1].rrf_score == pytest.approx(1 / (RRF_K + 2))
    assert merged[0].semantic_rank == 1
    assert merged[0].fts_rank is None
    assert merged[1].semantic_rank == 2


def test_rrf_dual_hit_boosts_ranking() -> None:
    """双路命中的候选得分叠加，超过任何单路第一名。"""
    merged = rrf_merge(
        [_scored("a"), _scored("b")],
        [_scored("c"), _scored("a")],
    )
    # a: 1/61 + 1/62；c: 1/61；b: 1/62
    assert [c.chunk_id for c in merged] == ["a", "c", "b"]
    assert merged[0].rrf_score == pytest.approx(1 / 61 + 1 / 62)
    assert merged[0].semantic_rank == 1
    assert merged[0].fts_rank == 2


def test_rrf_score_respects_k_parameter() -> None:
    """k 参数参与得分计算（k=1 时 rank1 得分 1/2）。"""
    merged = rrf_merge([_scored("a")], [], k=1)
    assert merged[0].rrf_score == pytest.approx(0.5)


def test_rrf_tie_break_is_deterministic() -> None:
    """得分相同：先按 semantic rank（未命中排后），再按 fts rank，再按 chunk_id。"""
    merged = rrf_merge(
        [_scored("b"), _scored("a")],
        [_scored("c"), _scored("d")],
    )
    # b/a 得分相同（1/61、1/62），c/d 同理；两路之间 semantic rank 1 优先
    assert [c.chunk_id for c in merged] == ["b", "c", "a", "d"]
    # 同路同分并列时按 chunk_id 稳定排序
    merged = rrf_merge([], [_scored("y"), _scored("x")])
    assert [c.chunk_id for c in merged] == ["y", "x"]


def test_rrf_paper_and_token_count_carried_from_first_seen() -> None:
    """合并结果携带候选的 paper_id 与 token_count。"""
    merged = rrf_merge(
        [ScoredChunk(chunk_id="a", paper_id="p1", token_count=42)],
        [ScoredChunk(chunk_id="a", paper_id="p1", token_count=42)],
    )
    assert merged[0].paper_id == "p1"
    assert merged[0].token_count == 42


def test_per_paper_limit_keeps_order_and_caps_each_paper() -> None:
    """每篇论文最多保留 limit 条，保持原排序，其他论文不受影响。"""
    merged = rrf_merge(
        [
            _scored("a", "p1"),
            _scored("b", "p1"),
            _scored("c", "p2"),
            _scored("d", "p1"),
        ],
        [],
    )
    limited = apply_per_paper_limit(merged, limit=2)
    assert [c.chunk_id for c in limited] == ["a", "b", "c"]


def test_per_paper_limit_rejects_non_positive() -> None:
    """limit < 1 直接报错。"""
    with pytest.raises(ValueError, match="per_paper_limit"):
        apply_per_paper_limit([], limit=0)


def test_token_budget_truncates_cumulatively() -> None:
    """按 token_count 累计截断：超出预算的候选被跳过，后续小候选仍可入选。"""
    merged = rrf_merge(
        [
            _scored("a", token_count=100),
            _scored("b", token_count=500),
            _scored("c", token_count=150),
        ],
        [],
    )
    truncated = apply_token_budget(merged, budget=250)
    assert [c.chunk_id for c in truncated] == ["a", "c"]


def test_token_budget_zero_returns_empty() -> None:
    """预算为 0 时不返回任何候选；负预算报错。"""
    merged = rrf_merge([_scored("a")], [])
    assert apply_token_budget(merged, budget=0) == []
    with pytest.raises(ValueError, match="token_budget"):
        apply_token_budget(merged, budget=-1)
