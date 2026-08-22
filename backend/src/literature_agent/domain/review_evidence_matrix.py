"""Evidence Matrix 的固定结构与确定性引用范围校验。"""

import json
import re
from dataclasses import dataclass
from enum import StrEnum

from literature_agent.domain.evidence import Evidence

_DIMENSION_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ROW_KEYS = {
    "paper_id",
    "dimension_key",
    "status",
    "finding",
    "limitations",
    "evidence_ids",
}
# 聚合 Output 最多包含 10 篇 × 6 维；按 UTF-8 最坏情况控制在 256 KiB 下。
MATRIX_TEXT_MAX_CHARS = 500
MATRIX_EVIDENCE_MAX_ITEMS = 10


class EvidenceMatrixStatus(StrEnum):
    """单篇论文、单一维度的提取状态。"""

    EXTRACTED = "extracted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True, slots=True)
class AnalysisDimension:
    """检索策略输出的一项稳定分析维度。"""

    dimension_key: str
    name: str
    extraction_question: str

    def __post_init__(self) -> None:
        if not _DIMENSION_KEY_PATTERN.fullmatch(self.dimension_key):
            raise ValueError("dimension_key 必须是长度不超过 64 的 snake_case 标识符")
        if not self.name.strip() or len(self.name) > 200:
            raise ValueError("维度名称不能为空且不得超过 200 字符")
        if not self.extraction_question.strip() or len(self.extraction_question) > 1_000:
            raise ValueError("维度提取问题不能为空且不得超过 1000 字符")


@dataclass(frozen=True, slots=True)
class EvidenceMatrixRow:
    """经校验后可持久化的最小 Matrix 行。"""

    paper_id: str
    dimension_key: str
    status: EvidenceMatrixStatus
    finding: str | None
    limitations: str | None
    evidence_ids: tuple[str, ...]

    def to_payload(self) -> dict:
        """转换为 ReviewOutput 可序列化载荷。"""
        return {
            "paper_id": self.paper_id,
            "dimension_key": self.dimension_key,
            "status": self.status.value,
            "finding": self.finding,
            "limitations": self.limitations,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True, slots=True)
class EvidenceMatrixValidationIssue:
    """可安全交给一次结构修复调用的确定性错误。"""

    code: str
    path: str
    message: str

    def to_payload(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


class EvidenceMatrixValidationError(Exception):
    """模型 Matrix 输出的结构、引用或业务范围非法。"""

    def __init__(self, issues: list[EvidenceMatrixValidationIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{item.path}: {item.message}" for item in issues))


def parse_evidence_matrix_json(content: str) -> dict:
    """解析模型 JSON；错误只暴露稳定代码，不记录原输出。"""
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise EvidenceMatrixValidationError(
            [EvidenceMatrixValidationIssue("invalid_json", "$", "输出不是合法 JSON")]
        ) from exc
    if not isinstance(value, dict):
        raise EvidenceMatrixValidationError(
            [EvidenceMatrixValidationIssue("schema_invalid", "$", "顶层必须是对象")]
        )
    return value


def validate_evidence_matrix(
    payload: dict,
    *,
    dimensions: tuple[AnalysisDimension, ...],
    paper_id: str,
    version_id: str,
    run_id: str,
    project_id: str,
    allowed_evidence: list[Evidence],
) -> tuple[EvidenceMatrixRow, ...]:
    """校验 Schema、完整维度集合及 Evidence 的 Run/Project/Paper/Version 闭包。"""
    issues: list[EvidenceMatrixValidationIssue] = []
    if set(payload) != {"rows"} or not isinstance(payload.get("rows"), list):
        raise EvidenceMatrixValidationError(
            [EvidenceMatrixValidationIssue("schema_invalid", "$", "必须且只能包含 rows 数组")]
        )
    expected_keys = [dimension.dimension_key for dimension in dimensions]
    if not expected_keys or len(set(expected_keys)) != len(expected_keys):
        raise ValueError("分析维度不能为空或重复")
    evidence_by_id = {item.evidence_id: item for item in allowed_evidence}
    rows: list[EvidenceMatrixRow] = []
    seen: set[str] = set()
    for index, raw in enumerate(payload["rows"]):
        path = f"$.rows[{index}]"
        if not isinstance(raw, dict) or set(raw) != _ROW_KEYS:
            issues.append(
                EvidenceMatrixValidationIssue(
                    "schema_invalid", path, "Matrix 行字段不完整或包含未知字段"
                )
            )
            continue
        dimension_key = raw["dimension_key"]
        if not isinstance(dimension_key, str) or dimension_key not in expected_keys:
            issues.append(
                EvidenceMatrixValidationIssue(
                    "dimension_set_mismatch", f"{path}.dimension_key", "维度不属于当前 Run"
                )
            )
            continue
        if dimension_key in seen:
            issues.append(
                EvidenceMatrixValidationIssue(
                    "duplicate_dimension", f"{path}.dimension_key", "同一论文的维度行重复"
                )
            )
        seen.add(dimension_key)
        if raw["paper_id"] != paper_id:
            issues.append(
                EvidenceMatrixValidationIssue(
                    "paper_scope_mismatch", f"{path}.paper_id", "Paper 不属于当前输入论文"
                )
            )
        try:
            status = EvidenceMatrixStatus(raw["status"])
        except (ValueError, TypeError):
            issues.append(
                EvidenceMatrixValidationIssue("schema_invalid", f"{path}.status", "status 不受支持")
            )
            continue
        finding = raw["finding"]
        limitations = raw["limitations"]
        evidence_ids = raw["evidence_ids"]
        if finding is not None and not isinstance(finding, str):
            issues.append(
                EvidenceMatrixValidationIssue(
                    "schema_invalid", f"{path}.finding", "finding 必须是字符串或 null"
                )
            )
        if limitations is not None and not isinstance(limitations, str):
            issues.append(
                EvidenceMatrixValidationIssue(
                    "schema_invalid", f"{path}.limitations", "limitations 必须是字符串或 null"
                )
            )
        if not isinstance(evidence_ids, list) or not all(
            isinstance(item, str) for item in evidence_ids
        ):
            issues.append(
                EvidenceMatrixValidationIssue(
                    "schema_invalid", f"{path}.evidence_ids", "evidence_ids 必须是字符串数组"
                )
            )
            continue
        if len(evidence_ids) != len(set(evidence_ids)):
            issues.append(
                EvidenceMatrixValidationIssue(
                    "duplicate_evidence", f"{path}.evidence_ids", "Evidence ID 不得重复"
                )
            )
        if len(evidence_ids) > MATRIX_EVIDENCE_MAX_ITEMS:
            issues.append(
                EvidenceMatrixValidationIssue(
                    "text_limit_exceeded", f"{path}.evidence_ids", "Evidence 数量超限"
                )
            )
        for field_name, text in (("finding", finding), ("limitations", limitations)):
            if isinstance(text, str) and len(text) > MATRIX_TEXT_MAX_CHARS:
                issues.append(
                    EvidenceMatrixValidationIssue(
                        "text_limit_exceeded", f"{path}.{field_name}", "文本长度超限"
                    )
                )
        if status is EvidenceMatrixStatus.EXTRACTED:
            if not isinstance(finding, str) or not finding.strip() or not evidence_ids:
                issues.append(
                    EvidenceMatrixValidationIssue(
                        "status_mismatch", path, "extracted 必须有 finding 和 Evidence"
                    )
                )
        elif finding is not None or limitations is not None or evidence_ids:
            issues.append(
                EvidenceMatrixValidationIssue(
                    "status_mismatch", path, "insufficient_evidence 必须清空结论、限制和 Evidence"
                )
            )
        for evidence_id in evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                issues.append(
                    EvidenceMatrixValidationIssue(
                        "fabricated_evidence", f"{path}.evidence_ids", "Evidence 不在当前模型输入中"
                    )
                )
            elif (
                evidence.run_id != run_id
                or evidence.project_id != project_id
                or evidence.paper_id != paper_id
                or evidence.version_id != version_id
            ):
                issues.append(
                    EvidenceMatrixValidationIssue(
                        "evidence_scope_mismatch",
                        f"{path}.evidence_ids",
                        "Evidence 超出当前 Run/Project/Paper/Version 范围",
                    )
                )
        rows.append(
            EvidenceMatrixRow(
                paper_id=paper_id,
                dimension_key=dimension_key,
                status=status,
                finding=finding.strip() if isinstance(finding, str) else None,
                limitations=limitations.strip() if isinstance(limitations, str) else None,
                evidence_ids=tuple(evidence_ids),
            )
        )
    if seen != set(expected_keys):
        issues.append(
            EvidenceMatrixValidationIssue(
                "dimension_set_mismatch", "$.rows", "每个维度必须且只能出现一次"
            )
        )
    if issues:
        raise EvidenceMatrixValidationError(issues)
    by_key = {row.dimension_key: row for row in rows}
    return tuple(by_key[key] for key in expected_keys)
