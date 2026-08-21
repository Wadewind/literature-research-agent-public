"""确定性 Citation Validator（切片 7）。

对模型结构化输出做运行时可判定的结构与权限校验，不依赖 Prompt
约束（§10：Citation Validator 是核心业务模块）。纯函数，不访问
数据库；输入的 Evidence 集合由 EvidenceService 在本次 Run 内固化。

校验规则（2026-08-20/21 定稿，全部有测试）：

1. ``answered``：claims 非空，且每个段落级 Claim 至少绑定一个
   Evidence（严格策略，零引用 Claim 直接判非法）；
2. ``insufficient_evidence``：claims 必须为空；
3. 所有 ``evidence_ids`` 必须存在于本次 Run 固化的 Evidence 集合
   （伪造/缺失 ID 拒绝）；
4. 同一 Claim 内重复引用同一 Evidence 拒绝（不同 Claim 共享同一
   Evidence 合法）；
5. 链完整性复核：Evidence 的 ``run_id`` 必须等于当前 Run
   （paper/version 属于 Run 快照由 EvidenceService 在固化时保证）。

「Evidence 是否在语义上真正支持 Claim」不在本模块范围，由固定人工
样本与评测完成。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from literature_agent.domain.answer_schema import RagAnswerOutput
from literature_agent.domain.evidence import AnswerStatus, Evidence


class CitationFailureReason(StrEnum):
    """校验失败的稳定原因码（供事件与日志使用，不含文本内容）。"""

    EMPTY_CLAIMS = "empty_claims"
    STATUS_MISMATCH = "status_mismatch"
    UNCITED_CLAIM = "uncited_claim"
    FABRICATED_EVIDENCE = "fabricated_evidence"
    CROSS_RUN_EVIDENCE = "cross_run_evidence"
    DUPLICATE_CITATION = "duplicate_citation"


@dataclass(frozen=True, slots=True)
class CitationFailure:
    """一条校验失败记录。

    属性:
        reason: 稳定原因码。
        claim_index: 违规 Claim 在输出中的下标；与具体 Claim 无关的
            失败（状态一致性）为 None。
    """

    reason: CitationFailureReason
    claim_index: int | None = None


@dataclass(frozen=True, slots=True)
class CitationValidationResult:
    """校验结果。

    属性:
        passed: 是否通过（无任何失败）。
        failures: 全部失败记录，按 Claim 顺序排列。
    """

    passed: bool
    failures: list[CitationFailure]


def validate_citations(
    output: RagAnswerOutput,
    *,
    evidence: Sequence[Evidence],
    run_id: str,
) -> CitationValidationResult:
    """校验结构化输出与本次 Run Evidence 集合的结构和权限关系。

    参数:
        output: 已解析的 ``RagAnswerOutput``。
        evidence: 本次 Run 固化的 Evidence 集合。
        run_id: 当前 Run 标识符（链完整性复核）。

    返回:
        校验结果；``passed=False`` 时 ``failures`` 非空且 reason 稳定。
    """
    failures: list[CitationFailure] = []

    if output.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE:
        # 规则 2：证据不足是空 claims 的成功业务结果
        if output.claims:
            failures.append(CitationFailure(CitationFailureReason.STATUS_MISMATCH))
        return CitationValidationResult(passed=not failures, failures=failures)

    # 规则 1a：answered 必须有 claims
    if not output.claims:
        failures.append(CitationFailure(CitationFailureReason.EMPTY_CLAIMS))
        return CitationValidationResult(passed=False, failures=failures)

    evidence_by_id = {e.evidence_id: e for e in evidence}
    for index, claim in enumerate(output.claims):
        # 规则 1b：段落级 Claim 严格绑定，零引用直接判非法
        if not claim.evidence_ids:
            failures.append(CitationFailure(CitationFailureReason.UNCITED_CLAIM, index))
            continue
        # 规则 4：同一 Claim 内重复引用拒绝（先去重检测，严格策略直接判失败）
        if len(set(claim.evidence_ids)) != len(claim.evidence_ids):
            failures.append(
                CitationFailure(CitationFailureReason.DUPLICATE_CITATION, index)
            )
        for evidence_id in dict.fromkeys(claim.evidence_ids):
            found = evidence_by_id.get(evidence_id)
            # 规则 3：伪造/缺失的 Evidence ID 拒绝
            if found is None:
                failures.append(
                    CitationFailure(CitationFailureReason.FABRICATED_EVIDENCE, index)
                )
            # 规则 5：链完整性复核，跨 Run 的 Evidence 拒绝
            elif found.run_id != run_id:
                failures.append(
                    CitationFailure(CitationFailureReason.CROSS_RUN_EVIDENCE, index)
                )

    return CitationValidationResult(passed=not failures, failures=failures)
