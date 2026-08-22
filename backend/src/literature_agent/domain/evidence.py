"""Evidence / ClaimSet / Claim / Citation 领域实体。

Evidence 是一次 ``rag_answer`` Run 检索结果的固化快照：denormalize
paper/version/parse_revision/章节/页码/摘录，历史回答不因后续移出、
换版或归档而改变（ADR 0002）。ClaimSet 是一次 RAG 回答或 Review
生成结果的结构化 Claim 集合（一个 Run 至多一个）；Citation 关联 Claim
与其依据的 Evidence。
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

# Evidence 摘录字符上限：Evidence 只保存 Chunk 文本的定位摘录，
# 不复制全文，控制行宽与事件/查询载荷大小
EVIDENCE_EXCERPT_MAX_CHARS = 500

# rag_answer Run ``input_payload`` 中版本范围快照的键：
# 值为 ``[{"paper_id": ..., "version_id": ...}, ...]``，提交问题时
# 解析固化，后续移出/换版/归档不影响历史回答
RUN_INPUT_VERSION_SCOPE_KEY = "version_scope"


class AnswerStatus(StrEnum):
    """RAG 回答状态。

    ``INSUFFICIENT_EVIDENCE`` 是成功的业务结果（证据不足明确回答），
    不是系统失败。
    """

    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True, slots=True)
class Evidence:
    """一次 Run 固化的一条可引用证据。

    属性:
        evidence_id: Evidence 标识符。
        run_id: 产生该 Evidence 的 rag_answer Run。
        project_id: 所属 Project（检索范围）。
        paper_id: 来源 Paper。
        version_id: 来源 PaperVersion。
        parse_revision_id: 来源 Parse Revision（链完整性一环）。
        chunk_id: 来源 Chunk。
        section_path: 章节路径；不属于任何章节时为 None。
        page_start/page_end: 来源页码范围；无定位为 None。
        excerpt: Chunk 文本摘录（截断到
            ``EVIDENCE_EXCERPT_MAX_CHARS`` 字符）。
        created_at: 固化时间（UTC）。
    """

    evidence_id: str
    run_id: str
    project_id: str
    paper_id: str
    version_id: str
    parse_revision_id: str
    chunk_id: str
    section_path: str | None
    page_start: int | None
    page_end: int | None
    excerpt: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class ClaimSet:
    """一次生成结果的结构化 Claim 集合（一个 Run 至多提交一个）。

    属性:
        claim_set_id: ClaimSet 标识符。
        run_id: 产生该结果的 RAG 或 Review Run。
        answer_status: 回答状态（answered / insufficient_evidence）。
        created_at: 提交时间（UTC）。
    """

    claim_set_id: str
    run_id: str
    answer_status: AnswerStatus
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class Claim:
    """回答中的一个段落级论述。

    属性:
        claim_id: Claim 标识符。
        claim_set_id: 所属 ClaimSet。
        sequence: 在回答中的顺序，从 1 开始，ClaimSet 内唯一。
        text: 论述文本。
    """

    claim_id: str
    claim_set_id: str
    sequence: int
    text: str


@dataclass(frozen=True, slots=True)
class Citation:
    """Claim 与 Evidence 的关联（同一 Claim 不重复绑定同一 Evidence）。

    属性:
        claim_id: 被支持的 Claim。
        evidence_id: 作为依据的 Evidence。
    """

    claim_id: str
    evidence_id: str


def create_evidence(
    *,
    run_id: str,
    project_id: str,
    paper_id: str,
    version_id: str,
    parse_revision_id: str,
    chunk_id: str,
    section_path: str | None,
    page_start: int | None,
    page_end: int | None,
    excerpt: str,
) -> Evidence:
    """创建一条新 Evidence。"""
    return Evidence(
        evidence_id=str(uuid4()),
        run_id=run_id,
        project_id=project_id,
        paper_id=paper_id,
        version_id=version_id,
        parse_revision_id=parse_revision_id,
        chunk_id=chunk_id,
        section_path=section_path,
        page_start=page_start,
        page_end=page_end,
        excerpt=excerpt,
        created_at=datetime.now(UTC),
    )


def create_claim_set(run_id: str, answer_status: AnswerStatus) -> ClaimSet:
    """创建一个 Run 的 ClaimSet。"""
    return ClaimSet(
        claim_set_id=str(uuid4()),
        run_id=run_id,
        answer_status=answer_status,
        created_at=datetime.now(UTC),
    )


def create_claim(claim_set_id: str, sequence: int, text: str) -> Claim:
    """创建一个段落级 Claim。"""
    return Claim(
        claim_id=str(uuid4()),
        claim_set_id=claim_set_id,
        sequence=sequence,
        text=text,
    )
