"""EmbeddingModel 的确定性假实现。

复用生产侧 ``bag_of_words_vector``（bag-of-words 哈希向量），与
``AGENT_EMBEDDING_BACKEND=fake`` 行为一致：词汇重叠的文本余弦相似度
更高，相同输入必然得到相同向量；只表达词汇重叠，不模拟语义泛化。
维度可配（生产装配固定 1024，与 chunks.embedding 列一致）。
"""

from literature_agent.application.ports.embedding_model import EmbeddingModel
from literature_agent.domain.model_types import EmbeddingResult, ModelUsage
from literature_agent.infrastructure.models.fake_models import bag_of_words_vector


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
        vectors = [bag_of_words_vector(text, self._dimensions) for text in texts]
        prompt_tokens = sum(max(1, len(text) // 4) for text in texts)
        return EmbeddingResult(
            vectors=vectors,
            model=self.model,
            usage=ModelUsage(prompt_tokens=prompt_tokens),
        )
