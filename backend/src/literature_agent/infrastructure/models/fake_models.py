"""生产侧确定性 Fake 模型实现（``AGENT_EMBEDDING_BACKEND=fake`` 等场景）。

与 ``FakeDocumentParser`` 同一定位：供本地开发、闭环演示与不触网的
端到端测试使用，不访问真实 Provider。

Embedding 向量采用确定性 bag-of-words 哈希（hashing trick，切片 6 由
纯哈希向量升级而来）：英文小写分词、去简化停用词、词经 SHA-256 映射
到固定维度桶累加词频、L2 归一化。词汇重叠多的文本余弦相似度更高，
使检索测试与评测在不触网的前提下具有确定性且有意义。

已知限制：该实现只表达**词汇重叠**，不模拟语义泛化（同义词、改写
不会提高相似度）；它用于验证检索管线与强过滤，不代表真实检索质量。

``tests/fakes/`` 下的测试 Fake 复用这里的 ``bag_of_words_vector``，
保证生产与测试假实现行为一致。维度必须与 chunks.embedding 列
（迁移固定 1024）一致。
"""

import hashlib
import json
import math
import re

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

# Prompt 中证据块的 ID 标记（与 RagAnswerExecutor 的证据块格式对应）
_EVIDENCE_ID_PATTERN = re.compile(r"evidence_id=([0-9a-fA-F-]{36})")
# 单条 Claim 引用的证据 ID 上限（保持响应小而确定）
_FAKE_MAX_CITATIONS_PER_CLAIM = 5

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")

# 简化英文停用词表：只去掉最常见功能词，让向量表达内容词重叠；
# 不追求完整停用词表（这是测试/开发假实现）
_STOPWORDS = frozenset({
    "a", "an", "the", "of", "and", "or", "in", "on", "for", "to", "is", "are",
    "was", "were", "be", "been", "by", "with", "as", "at", "from", "that",
    "this", "these", "those", "it", "its", "we", "our", "their", "which",
    "what", "how", "does", "do", "did", "many", "much", "into", "over",
})


def bag_of_words_vector(text: str, dimensions: int) -> list[float]:
    """生成确定性 bag-of-words 哈希向量（hashing trick）。

    小写分词（只保留字母数字词、去停用词与单字符），每个词经
    SHA-256 映射到 ``dimensions`` 个桶累加词频，最后 L2 归一化。
    相同输入必然得到相同向量；无有效词（空文本/纯停用词）返回零向量。
    """
    counts = [0.0] * dimensions
    for token in _TOKEN_PATTERN.findall(text.lower()):
        if token in _STOPWORDS or len(token) < 2:
            continue
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        counts[int.from_bytes(digest[:8], "big") % dimensions] += 1.0
    norm = math.sqrt(sum(value * value for value in counts))
    if norm == 0.0:
        return counts
    return [round(value / norm, 6) for value in counts]


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
        vectors = [bag_of_words_vector(text, self._dimensions) for text in texts]
        prompt_tokens = sum(max(1, len(text) // 4) for text in texts)
        return EmbeddingResult(
            vectors=vectors,
            model=self.model,
            usage=ModelUsage(prompt_tokens=prompt_tokens),
        )


class FakeChatModel(ChatModel):
    """不依赖外部服务的 Chat 假实现（确定性结构化响应，切片 8 接线）。

    默认行为由 Prompt 中的证据 ID 驱动（保证端到端确定性）：

    - 用户消息中含 ``evidence_id=<uuid>`` 标记（RagAnswerExecutor 的
      证据块格式）→ 返回合法的 ``answered`` JSON，一条 Claim 引用
      提取到的前若干个证据 ID；
    - 不含任何证据 ID → 返回合法的 ``insufficient_evidence`` JSON。

    修复重试场景（消息列表带反馈）行为相同：从全部 user 消息中提取
    证据 ID，输出不变。
    """

    provider = "fake"
    model = "fake-chat"

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """返回确定性的合法结构化响应。"""
        content = self._scripted_response(messages, json_schema)
        return ChatResult(
            content=content,
            model=self.model,
            usage=ModelUsage(prompt_tokens=10, completion_tokens=5),
        )

    @staticmethod
    def _scripted_response(messages: list[ChatMessage], json_schema: dict | None = None) -> str:
        """按 Prompt 中的证据 ID 脚本化生成合法 RagAnswerOutput JSON。"""
        properties = set((json_schema or {}).get("properties", {}))
        prompt = _last_json_object(messages)
        if properties == {"normalized_question", "arxiv_query", "dimensions"}:
            question = str(prompt.get("research_question", "文献综述问题"))
            return json.dumps(
                {
                    "normalized_question": question,
                    "arxiv_query": "all:agent",
                    "dimensions": [
                        {
                            "dimension_key": "method",
                            "name": "方法",
                            "extraction_question": "采用了什么方法？",
                        },
                        {
                            "dimension_key": "evaluation",
                            "name": "评测",
                            "extraction_question": "如何进行评测？",
                        },
                        {
                            "dimension_key": "limitations",
                            "name": "限制",
                            "extraction_question": "有哪些限制？",
                        },
                    ],
                },
                ensure_ascii=False,
            )
        if properties == {"rows"} and "evidence_context" in prompt:
            evidence_context = prompt.get("evidence_context", [])
            evidence_id = evidence_context[0]["evidence_id"] if evidence_context else None
            paper_id = prompt.get("paper", {}).get("paper_id")
            rows = []
            for index, item in enumerate(prompt.get("dimensions", [])):
                extracted = index == 0 and evidence_id is not None
                rows.append(
                    {
                        "paper_id": paper_id,
                        "dimension_key": item["dimension_key"],
                        "status": "extracted" if extracted else "insufficient_evidence",
                        "finding": "Fake 模型基于受控证据提取的方法结论。" if extracted else None,
                        "limitations": None,
                        "evidence_ids": [evidence_id] if extracted else [],
                    }
                )
            return json.dumps({"rows": rows}, ensure_ascii=False)
        if properties == {"sections"} and "dimensions" in prompt:
            dimensions = prompt.get("dimensions", [])
            return json.dumps(
                {
                    "sections": [
                        {
                            "section_key": "synthesis",
                            "title": "研究综合",
                            "purpose": "综合比较现有证据。",
                            "dimension_keys": [item["dimension_key"] for item in dimensions[:3]],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        if properties == {"section_key", "title", "status", "summary", "claims", "terminology"}:
            section = prompt.get("section", {})
            evidence = prompt.get("evidence", [])
            if evidence:
                payload = {
                    "section_key": section.get("section_key"),
                    "title": section.get("title"),
                    "status": "answered",
                    "summary": "Fake 模型根据受控 Evidence 形成章节摘要。",
                    "claims": [
                        {
                            "text": "现有研究提供了可追溯的方法证据。",
                            "evidence_ids": [evidence[0]["evidence_id"]],
                        }
                    ],
                    "terminology": [],
                }
            else:
                payload = {
                    "section_key": section.get("section_key"),
                    "title": section.get("title"),
                    "status": "insufficient_evidence",
                    "summary": "当前章节证据不足。",
                    "claims": [],
                    "terminology": [],
                }
            return json.dumps(payload, ensure_ascii=False)
        if properties == {"status", "issues"} and "sections" in prompt:
            return '{"status":"consistent","issues":[]}'
        evidence_ids: list[str] = []
        for message in messages:
            if message.role != "user":
                continue
            for found in _EVIDENCE_ID_PATTERN.findall(message.content):
                if found not in evidence_ids:
                    evidence_ids.append(found)
        if not evidence_ids:
            return _DEFAULT_CHAT_RESPONSE
        cited = evidence_ids[:_FAKE_MAX_CITATIONS_PER_CLAIM]
        payload = {
            "answer_status": "answered",
            "claims": [
                {
                    "text": "基于给定证据的回答（fake 模型确定性生成）。",
                    "evidence_ids": cited,
                }
            ],
        }
        return json.dumps(payload, ensure_ascii=False)


def _last_json_object(messages: list[ChatMessage]) -> dict:
    for message in reversed(messages):
        if message.role != "user":
            continue
        try:
            value = json.loads(message.content)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    return {}
