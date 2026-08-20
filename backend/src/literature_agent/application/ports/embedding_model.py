"""Embedding 模型端口。"""

from typing import Protocol

from literature_agent.domain.model_types import EmbeddingResult


class EmbeddingModel(Protocol):
    """Embedding 模型的抽象端口。

    实现只暴露批量向量生成与用量，不绑定具体 Provider SDK。
    ``provider``/``model`` 属性用于调用记录（ModelInvocation）。
    """

    provider: str
    model: str

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """批量生成向量并返回 Usage。

        参数:
            texts: 输入文本列表；空列表直接返回空结果，不发起请求。

        返回:
            与输入一一对应的向量及 token 用量。

        异常:
            ModelError: 调用失败，按子类区分临时/永久错误。
        """
        ...
