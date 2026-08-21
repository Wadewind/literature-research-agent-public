"""生产侧确定性 Fake 模型实现（``AGENT_EMBEDDING_BACKEND=fake`` 等场景）。

与 ``FakeDocumentParser`` 同一定位：供本地开发、闭环演示与不触网的
端到端测试使用，不访问真实 Provider。向量由文本 SHA-256 哈希派生，
相同输入必然得到相同向量。

注意与 ``tests/fakes/`` 下的测试 Fake 分离：这里是 Worker/本地运行
装配的一部分，维度必须与 chunks.embedding 列（迁移固定 1024）一致。
"""

import hashlib

from literature_agent.application.ports.chat_model import ChatModel
from literature_agent.application.ports.embedding_model import EmbeddingModel
from literature_agent.domain.model_types import (
    ChatMessage,
    ChatResult,
    EmbeddingResult,
    ModelUsage,
)

# chunks.embedding 列维度（迁移 f2a7b3c9d4e1 固定）；fake backend 不可配
EMBEDDING_COLUMN_DIMENSIONS = 1024

_DEFAULT_CHAT_RESPONSE = '{"answer_status": "insufficient_evidence", "claims": []}'


def _hash_vector(text: str, dimensions: int) -> list[float]:
    """由文本哈希生成确定性的伪向量（值域 [-1, 1]）。"""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [
        round(digest[i % len(digest)] / 255 * 2 - 1, 6) for i in range(dimensions)
    ]


class FakeEmbeddingModel(EmbeddingModel):
    """不依赖外部服务的 Embedding 假实现（确定性、维度与列一致）。"""

    provider = "fake"
    model = "fake-embedding"

    def __init__(self, dimensions: int = EMBEDDING_COLUMN_DIMENSIONS) -> None:
        """初始化 Fake Embedding。

        参数:
            dimensions: 输出向量维度；生产装配固定与列维度一致（1024）。
        """
        self._dimensions = dimensions

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """返回确定性向量；空列表直接返回空结果，不发起请求。"""
        vectors = [_hash_vector(text, self._dimensions) for text in texts]
        prompt_tokens = sum(max(1, len(text) // 4) for text in texts)
        return EmbeddingResult(
            vectors=vectors,
            model=self.model,
            usage=ModelUsage(prompt_tokens=prompt_tokens),
        )


class FakeChatModel(ChatModel):
    """不依赖外部服务的 Chat 假实现（固定结构化响应，占位到切片 8 接线）。"""

    provider = "fake"
    model = "fake-chat"

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """返回固定的最小结构化响应。"""
        return ChatResult(
            content=_DEFAULT_CHAT_RESPONSE,
            model=self.model,
            usage=ModelUsage(prompt_tokens=10, completion_tokens=5),
        )
