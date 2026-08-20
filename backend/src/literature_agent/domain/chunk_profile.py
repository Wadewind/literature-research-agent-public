"""Chunk Profile 与确定性 profile 哈希。

一个 ChunkSet 同时固定 Chunk 切分参数与 Embedding 参数：
任一参数变化都会产生新的 ``profile_hash``，从而生成新的 ChunkSet，
旧索引保留直到无引用后再清理。哈希模式与 ``parse_profile`` 同构。
"""

import hashlib
import json
from dataclasses import dataclass

_DEFAULT_MAX_TOKENS = 512
_DEFAULT_OVERLAP_TOKENS = 64
_DEFAULT_TOKENIZER = "cl100k_base"


@dataclass(frozen=True, slots=True)
class ChunkProfile:
    """一次切分（含后续 Embedding）的配置画像。

    属性:
        max_tokens: 单个 Chunk 的目标 token 上限（实验起点 512，
            切片 6 检索实验可校准）。
        overlap_tokens: 相邻 Chunk 的重叠 token 数（按整 Element 回带）。
        tokenizer: token 计数使用的 tiktoken 编码名称。
        include_section_prefix: 是否把当前章节标题拼入 Chunk 文本开头。
        embedding_provider: 当前活动 Embedding Provider 标识。
        embedding_model: 当前活动 Embedding 模型。
        embedding_dimensions: 向量维度。
    """

    max_tokens: int = _DEFAULT_MAX_TOKENS
    overlap_tokens: int = _DEFAULT_OVERLAP_TOKENS
    tokenizer: str = _DEFAULT_TOKENIZER
    include_section_prefix: bool = True
    embedding_provider: str = ""
    embedding_model: str = ""
    embedding_dimensions: int = 0

    def __post_init__(self) -> None:
        """校验参数合法性。"""
        if self.max_tokens <= 0:
            raise ValueError("max_tokens 必须为正整数")
        if self.overlap_tokens < 0:
            raise ValueError("overlap_tokens 不能为负数")
        if self.overlap_tokens >= self.max_tokens:
            raise ValueError("overlap_tokens 必须小于 max_tokens")
        if not self.tokenizer:
            raise ValueError("tokenizer 不能为空")

    @property
    def config(self) -> dict:
        """返回参与哈希与持久化的规范化配置字典。"""
        return {
            "max_tokens": self.max_tokens,
            "overlap_tokens": self.overlap_tokens,
            "tokenizer": self.tokenizer,
            "include_section_prefix": self.include_section_prefix,
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "embedding_dimensions": self.embedding_dimensions,
        }

    @property
    def profile_hash(self) -> str:
        """返回该 Profile 的确定性 SHA-256 哈希。

        Chunk 与 Embedding 参数共同参与一个哈希：相同语义的配置
        必然得到相同哈希，任一参数变化都产生新 ChunkSet。
        """
        canonical = json.dumps(
            self.config,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
