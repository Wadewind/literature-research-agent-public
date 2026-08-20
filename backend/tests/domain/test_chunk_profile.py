"""ChunkProfile 领域测试。"""

import pytest

from literature_agent.domain.chunk_profile import ChunkProfile


def test_default_values() -> None:
    """默认 profile：max_tokens=512、overlap=64、cl100k_base、含章节前缀。"""
    profile = ChunkProfile()
    assert profile.max_tokens == 512
    assert profile.overlap_tokens == 64
    assert profile.tokenizer == "cl100k_base"
    assert profile.include_section_prefix is True


def test_profile_hash_is_deterministic() -> None:
    """相同语义的配置必然得到相同哈希。"""
    a = ChunkProfile(embedding_provider="p", embedding_model="m", embedding_dimensions=1024)
    b = ChunkProfile(embedding_provider="p", embedding_model="m", embedding_dimensions=1024)
    assert a.profile_hash == b.profile_hash
    assert len(a.profile_hash) == 64


def test_chunk_params_participate_in_hash() -> None:
    """chunk 参数变化产生新哈希。"""
    base = ChunkProfile()
    assert base.profile_hash != ChunkProfile(max_tokens=256).profile_hash
    assert base.profile_hash != ChunkProfile(overlap_tokens=32).profile_hash
    assert base.profile_hash != ChunkProfile(tokenizer="o200k_base").profile_hash
    assert base.profile_hash != ChunkProfile(include_section_prefix=False).profile_hash


def test_embedding_params_participate_in_hash() -> None:
    """embedding 参数与 chunk 参数共同参与同一个哈希。"""
    base = ChunkProfile(embedding_provider="p", embedding_model="m", embedding_dimensions=1024)
    assert base.profile_hash != ChunkProfile(
        embedding_provider="p2", embedding_model="m", embedding_dimensions=1024
    ).profile_hash
    assert base.profile_hash != ChunkProfile(
        embedding_provider="p", embedding_model="m2", embedding_dimensions=1024
    ).profile_hash
    assert base.profile_hash != ChunkProfile(
        embedding_provider="p", embedding_model="m", embedding_dimensions=512
    ).profile_hash


def test_invalid_params_raise() -> None:
    """非法参数：非正 max_tokens、负 overlap、overlap >= max、空 tokenizer。"""
    with pytest.raises(ValueError):
        ChunkProfile(max_tokens=0)
    with pytest.raises(ValueError):
        ChunkProfile(overlap_tokens=-1)
    with pytest.raises(ValueError):
        ChunkProfile(max_tokens=64, overlap_tokens=64)
    with pytest.raises(ValueError):
        ChunkProfile(tokenizer="")
