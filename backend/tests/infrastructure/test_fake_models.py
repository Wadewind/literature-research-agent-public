"""生产侧 Fake 模型的单元测试（切片 6：bag-of-words 哈希向量）。

Fake Embedding 升级为确定性 bag-of-words 哈希向量（hashing trick），
使词汇重叠的文本获得更高的余弦相似度，支撑不触网的检索测试与评测。
该实现只表达词汇重叠，不模拟语义泛化（已知限制，见阶段 Spec）。
"""

import math

import pytest

from literature_agent.infrastructure.models.fake_models import (
    EMBEDDING_COLUMN_DIMENSIONS,
    FakeEmbeddingModel,
    bag_of_words_vector,
)


def _cosine(a: list[float], b: list[float]) -> float:
    """计算两个向量的余弦相似度。"""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


async def test_embed_is_deterministic_and_matches_column_dimensions() -> None:
    """相同输入得到相同向量，默认维度与 chunks.embedding 列一致。"""
    model = FakeEmbeddingModel()
    first = await model.embed(["GraphWeave benchmark suite"])
    second = await model.embed(["GraphWeave benchmark suite"])
    assert first.vectors == second.vectors
    assert len(first.vectors[0]) == EMBEDDING_COLUMN_DIMENSIONS


async def test_embed_empty_list_returns_empty() -> None:
    """空列表直接返回空结果。"""
    model = FakeEmbeddingModel()
    result = await model.embed([])
    assert result.vectors == []


def test_bag_of_words_vector_is_l2_normalized() -> None:
    """非空文本的向量 L2 范数为 1。"""
    vector = bag_of_words_vector("message passing graph neural networks", 64)
    norm = math.sqrt(sum(x * x for x in vector))
    assert norm == pytest.approx(1.0)


def test_lexical_overlap_yields_higher_similarity() -> None:
    """词汇重叠多的文本对余弦相似度显著高于无关文本对。"""
    chunk = "The GraphWeave benchmark suite contains nine synthetic tasks."
    related = "How many tasks does the GraphWeave benchmark suite contain?"
    unrelated = "Zephyr scheduler reduces adaptation time for quadruped robots."
    chunk_vector = bag_of_words_vector(chunk, 1024)
    similar = _cosine(chunk_vector, bag_of_words_vector(related, 1024))
    dissimilar = _cosine(chunk_vector, bag_of_words_vector(unrelated, 1024))
    assert similar > 0.3
    assert similar > dissimilar * 3


def test_disjoint_vocabulary_yields_near_zero_similarity() -> None:
    """完全不重叠的词汇（去除停用词后）相似度接近 0。"""
    a = bag_of_words_vector("quantum entanglement superconducting qubits", 1024)
    b = bag_of_words_vector("molecular solubility fingerprint prediction", 1024)
    assert _cosine(a, b) == pytest.approx(0.0, abs=1e-6)


def test_stopword_only_text_yields_zero_vector() -> None:
    """只含停用词/空文本时返回零向量（调用方需容忍）。"""
    assert all(v == 0.0 for v in bag_of_words_vector("the a an of and", 16))
    assert all(v == 0.0 for v in bag_of_words_vector("", 16))


def test_tokenization_is_case_insensitive() -> None:
    """分词不区分大小写：大小写不同的同词文本得到相同向量。"""
    assert bag_of_words_vector("GraphWeave Benchmark", 64) == bag_of_words_vector(
        "graphweave benchmark", 64
    )
