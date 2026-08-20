"""EmbeddingModel 的确定性假实现。

向量由文本 SHA-256 哈希派生，维度可配，相同输入必然得到相同向量，
供不访问真实 Provider 的测试与本地开发使用。
"""

import hashlib

from literature_agent.application.ports.embedding_model import EmbeddingModel
from literature_agent.domain.model_types import EmbeddingResult, ModelUsage


def _hash_vector(text: str, dimensions: int) -> list[float]:
    """由文本哈希生成确定性的伪向量（值域 [-1, 1]）。"""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [
        round(digest[i % len(digest)] / 255 * 2 - 1, 6) for i in range(dimensions)
    ]


class FakeEmbeddingModel(EmbeddingModel):
    """不依赖外部服务的 Embedding 假实现。"""

    provider = "fake"

    def __init__(self, dimensions: int = 8, error: Exception | None = None) -> None:
        """初始化 Fake Embedding。

        参数:
            dimensions: 输出向量维度。
            error: 非 None 时每次 embed 抛出该异常（测试失败路径用）。
        """
        self.model = "fake-embedding"
        self._dimensions = dimensions
        self._error = error
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """记录调用并返回确定性向量；空列表直接返回空结果。"""
        self.calls.append(list(texts))
        if self._error is not None:
            raise self._error
        vectors = [_hash_vector(text, self._dimensions) for text in texts]
        prompt_tokens = sum(max(1, len(text) // 4) for text in texts)
        return EmbeddingResult(
            vectors=vectors,
            model=self.model,
            usage=ModelUsage(prompt_tokens=prompt_tokens),
        )
