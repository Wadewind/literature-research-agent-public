"""固定文献综述 Workflow 的领域数据契约。"""

import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

_VERSION_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*\.v[1-9][0-9]*$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_CONFIG_BYTES = 64 * 1024
_MAX_OUTPUT_BYTES = 256 * 1024
_MAX_METADATA_BYTES = 64 * 1024
_STATISTIC_KEYS = (
    "source_discovered",
    "source_ready",
    "source_failed",
    "model_invocations",
    "prompt_tokens",
    "completion_tokens",
)


class ReviewStage(StrEnum):
    """Review Run 当前业务阶段。"""

    VALIDATE_REQUEST = "validate_request"
    FORMULATE_SEARCH_STRATEGY = "formulate_search_strategy"
    SEARCH_ARXIV = "search_arxiv"
    IMPORT_ARXIV_PAPERS = "import_arxiv_papers"
    WAIT_FOR_INGESTION = "wait_for_ingestion"
    BUILD_EVIDENCE_MATRIX = "build_evidence_matrix"
    PROPOSE_OUTLINE = "propose_outline"
    REVIEW_OUTLINE = "review_outline"
    DRAFT_SECTIONS = "draft_sections"
    VALIDATE_SECTIONS = "validate_sections"
    CONSISTENCY_CHECK = "consistency_check"
    EXPORT_REVIEW = "export_review"
    FINALIZE = "finalize"


class ReviewStepKey(StrEnum):
    """固定 Workflow 的稳定 Step key。"""

    VALIDATE_REQUEST = "validate_request"
    FORMULATE_SEARCH_STRATEGY = "formulate_search_strategy"
    SEARCH_ARXIV = "search_arxiv"
    IMPORT_ARXIV_PAPERS = "import_arxiv_papers"
    WAIT_FOR_INGESTION = "wait_for_ingestion"
    BUILD_EVIDENCE_MATRIX = "build_evidence_matrix"
    PROPOSE_OUTLINE = "propose_outline"
    PERSIST_OUTLINE = "persist_outline"
    REVIEW_OUTLINE = "review_outline"
    DRAFT_SECTIONS = "draft_sections"
    VALIDATE_SECTIONS = "validate_sections"
    CONSISTENCY_CHECK = "consistency_check"
    EXPORT_REVIEW = "export_review"
    FINALIZE = "finalize"


class ReviewStepStatus(StrEnum):
    """Run Step 生命周期状态。"""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReviewSourceStatus(StrEnum):
    """arXiv 来源的导入状态。"""

    DISCOVERED = "discovered"
    IMPORTING = "importing"
    READY = "ready"
    FAILED = "failed"


class ReviewDependencyType(StrEnum):
    """父 Review Run 可以等待的依赖目标类型。"""

    RUN = "run"
    PAPER_VERSION = "paper_version"
    CHUNK_SET = "chunk_set"


class ReviewDependencyStatus(StrEnum):
    """依赖满足状态。"""

    PENDING = "pending"
    SATISFIED = "satisfied"
    FAILED = "failed"


class ReviewOutputType(StrEnum):
    """可版本化 Review Output 类型。"""

    SEARCH_STRATEGY = "search_strategy"
    EVIDENCE_MATRIX = "evidence_matrix"
    OUTLINE = "outline"
    SECTION = "section"
    CONSISTENCY_REPORT = "consistency_report"
    FINAL_REVIEW = "final_review"


class HumanInputRequestStatus(StrEnum):
    """人工输入请求状态。"""

    OPEN = "open"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class HumanInputAction(StrEnum):
    """大纲人工输入允许动作。"""

    APPROVE = "approve"
    EDIT = "edit"
    FEEDBACK = "feedback"


class ArtifactType(StrEnum):
    """Phase 3 持久 Artifact 类型。"""

    REVIEW_MARKDOWN = "review_markdown"
    SEARCH_STRATEGY = "search_strategy"
    SOURCE_MANIFEST = "source_manifest"
    EVIDENCE_MATRIX = "evidence_matrix"
    BIBLIOGRAPHY = "bibliography"
    RUN_SUMMARY = "run_summary"


@dataclass(frozen=True, slots=True)
class ReviewRun:
    """通用 Run 的 Review 扩展记录。"""

    run_id: str
    research_question: str
    workflow_version: str
    model_profile_version: str
    prompt_versions: dict[str, str]
    config_snapshot: dict
    statistics_summary: dict[str, int]
    current_stage: ReviewStage
    current_outline_output_id: str | None
    final_artifact_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RunStep:
    """Review Run 中一次可观察、可幂等复用的 Step 执行。"""

    step_id: str
    run_id: str
    step_key: ReviewStepKey
    sequence: int
    status: ReviewStepStatus
    idempotency_key: str
    input_refs: dict
    output_refs: dict
    error_code: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    def start(self) -> "RunStep":
        """开始 Step；重复加载 running Step 时保持幂等。"""
        if self.status is ReviewStepStatus.RUNNING:
            return self
        if self.status is not ReviewStepStatus.PENDING:
            raise ValueError("只有 pending Step 可以开始")
        return replace(
            self,
            status=ReviewStepStatus.RUNNING,
            started_at=datetime.now(UTC),
        )

    def succeed(self, output_refs: dict) -> "RunStep":
        """以小型业务引用完成 running Step。"""
        _validate_json_object(output_refs, "Step 输出引用", _MAX_CONFIG_BYTES)
        if self.status is ReviewStepStatus.SUCCEEDED and self.output_refs == output_refs:
            return self
        if self.status is not ReviewStepStatus.RUNNING:
            raise ValueError("只有 running Step 可以成功")
        return replace(
            self,
            status=ReviewStepStatus.SUCCEEDED,
            output_refs=dict(output_refs),
            error_code=None,
            completed_at=datetime.now(UTC),
        )

    def fail(self, error_code: str) -> "RunStep":
        """以稳定错误码结束 pending/running Step。"""
        if not error_code or len(error_code) > 100:
            raise ValueError("Step error_code 不能为空且不得超过 100 字符")
        if self.status not in {ReviewStepStatus.PENDING, ReviewStepStatus.RUNNING}:
            raise ValueError("只有 pending/running Step 可以失败")
        return replace(
            self,
            status=ReviewStepStatus.FAILED,
            error_code=error_code,
            completed_at=datetime.now(UTC),
        )

    def pause(self, output_refs: dict) -> "RunStep":
        """人工等待时暂停 Step；保留最新请求的小型引用。"""
        _validate_json_object(output_refs, "Step 暂停引用", _MAX_CONFIG_BYTES)
        if self.status is not ReviewStepStatus.RUNNING:
            raise ValueError("只有 running Step 可以暂停")
        return replace(
            self,
            status=ReviewStepStatus.PAUSED,
            output_refs=dict(output_refs),
            error_code=None,
            completed_at=None,
        )

    def resume(self) -> "RunStep":
        """新 Worker 恢复人工等待 Step。"""
        if self.status is not ReviewStepStatus.PAUSED:
            raise ValueError("只有 paused Step 可以恢复")
        return replace(self, status=ReviewStepStatus.RUNNING)


@dataclass(frozen=True, slots=True)
class ReviewSource:
    """Review Run 自动纳入的一条 arXiv 来源。"""

    source_id: str
    review_run_id: str
    arxiv_id: str
    arxiv_version: str
    rank: int
    metadata_snapshot: dict
    status: ReviewSourceStatus
    paper_id: str | None
    paper_version_id: str | None
    failure_code: str | None
    created_at: datetime
    updated_at: datetime

    def mark_importing(self, paper_id: str, paper_version_id: str) -> "ReviewSource":
        """绑定可信 Paper/Version，并进入等待解析索引的导入状态。"""
        if self.status is not ReviewSourceStatus.DISCOVERED:
            raise ValueError("只有 discovered 来源可以开始导入")
        if not paper_id or not paper_version_id:
            raise ValueError("Paper 与 PaperVersion 不能为空")
        return replace(
            self,
            status=ReviewSourceStatus.IMPORTING,
            paper_id=paper_id,
            paper_version_id=paper_version_id,
            failure_code=None,
            updated_at=datetime.now(UTC),
        )

    def mark_ready(self, paper_id: str, paper_version_id: str) -> "ReviewSource":
        """绑定已具有 ready ChunkSet 的 Paper/Version。"""
        if self.status is ReviewSourceStatus.DISCOVERED:
            importing = self.mark_importing(paper_id, paper_version_id)
        elif (
            self.status is ReviewSourceStatus.IMPORTING
            and self.paper_id == paper_id
            and self.paper_version_id == paper_version_id
        ):
            importing = self
        else:
            raise ValueError("只有匹配的 discovered/importing 来源可以就绪")
        return replace(importing, status=ReviewSourceStatus.READY, updated_at=datetime.now(UTC))

    def mark_failed(self, failure_code: str) -> "ReviewSource":
        """以稳定错误码记录单篇永久或重试耗尽失败。"""
        if self.status not in {
            ReviewSourceStatus.DISCOVERED,
            ReviewSourceStatus.IMPORTING,
        } or not failure_code:
            raise ValueError("只有 discovered/importing 来源可以记录导入失败")
        return replace(
            self,
            status=ReviewSourceStatus.FAILED,
            failure_code=failure_code,
            updated_at=datetime.now(UTC),
        )


@dataclass(frozen=True, slots=True)
class ReviewDependency:
    """父 Review Run 对一个明确目标的依赖。"""

    dependency_id: str
    parent_run_id: str
    dependency_type: ReviewDependencyType
    status: ReviewDependencyStatus
    target_run_id: str | None
    target_paper_version_id: str | None
    target_chunk_set_id: str | None
    failure_code: str | None
    created_at: datetime
    satisfied_at: datetime | None

    def mark_satisfied(self) -> "ReviewDependency":
        """将 pending 依赖一次性推进为 satisfied。"""
        if self.status is not ReviewDependencyStatus.PENDING:
            raise ValueError("只有 pending 依赖可以完成")
        return replace(
            self,
            status=ReviewDependencyStatus.SATISFIED,
            failure_code=None,
            satisfied_at=datetime.now(UTC),
        )

    def mark_failed(self, failure_code: str) -> "ReviewDependency":
        """将 pending 依赖一次性推进为 failed。"""
        if self.status is not ReviewDependencyStatus.PENDING or not failure_code:
            raise ValueError("只有 pending 依赖可以记录失败")
        return replace(
            self,
            status=ReviewDependencyStatus.FAILED,
            failure_code=failure_code,
            satisfied_at=None,
        )


@dataclass(frozen=True, slots=True)
class ReviewOutput:
    """追加写入、按版本保留的结构化 Workflow 产物。"""

    output_id: str
    review_run_id: str
    output_type: ReviewOutputType
    output_key: str
    version: int
    schema_version: str
    payload: dict
    idempotency_key: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class HumanInputRequest:
    """一次版本化的大纲人工输入请求。"""

    request_id: str
    review_run_id: str
    request_version: int
    outline_output_id: str
    status: HumanInputRequestStatus
    allowed_actions: tuple[HumanInputAction, ...]
    resolved_input_id: str | None
    created_at: datetime
    resolved_at: datetime | None

    def resolve(self, human_input_id: str) -> "HumanInputRequest":
        """将开放请求解决一次；重复解决属于非法业务操作。"""
        if self.status is not HumanInputRequestStatus.OPEN:
            raise ValueError("HumanInputRequest 已经解决或取消")
        if not human_input_id:
            raise ValueError("human_input_id 不能为空")
        now = datetime.now(UTC)
        return replace(
            self,
            status=HumanInputRequestStatus.RESOLVED,
            resolved_input_id=human_input_id,
            resolved_at=now,
        )


@dataclass(frozen=True, slots=True)
class HumanInput:
    """用户针对一个 HumanInputRequest 提交的不可变输入。"""

    human_input_id: str
    request_id: str
    request_version: int
    action: HumanInputAction
    payload: dict
    submitted_by: str
    idempotency_key: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Artifact:
    """持久文件的业务元数据；正文位于 Artifact Storage。"""

    artifact_id: str
    review_run_id: str
    project_id: str
    owner_id: str
    artifact_type: ArtifactType
    storage_key: str
    content_hash: str
    size_bytes: int
    media_type: str
    idempotency_key: str
    source_output_id: str | None
    metadata: dict
    created_at: datetime


def _validate_version(value: str, field_name: str) -> None:
    if not _VERSION_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} 必须使用 name.vN 格式")


def _validate_json_object(value: dict, field_name: str, max_bytes: int) -> None:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是可序列化 JSON 对象") from exc
    if len(encoded) > max_bytes:
        raise ValueError(f"{field_name} 过大，必须改存 Artifact 或业务引用")


def create_review_run(
    *,
    run_id: str,
    research_question: str,
    workflow_version: str,
    model_profile_version: str,
    prompt_versions: dict[str, str],
    config_snapshot: dict,
) -> ReviewRun:
    """创建 ReviewRun 扩展记录。"""
    question = research_question.strip()
    if not question or len(question) > 4000:
        raise ValueError("研究问题不能为空且长度不得超过 4000")
    _validate_version(workflow_version, "Workflow 版本")
    _validate_version(model_profile_version, "Model Profile 版本")
    if not prompt_versions:
        raise ValueError("Prompt 版本快照不能为空")
    for name, version in prompt_versions.items():
        if not name:
            raise ValueError("Prompt 名称不能为空")
        _validate_version(version, "Prompt 版本")
    _validate_json_object(prompt_versions, "Prompt 版本快照", _MAX_CONFIG_BYTES)
    _validate_json_object(config_snapshot, "配置快照", _MAX_CONFIG_BYTES)
    now = datetime.now(UTC)
    return ReviewRun(
        run_id=run_id,
        research_question=question,
        workflow_version=workflow_version,
        model_profile_version=model_profile_version,
        prompt_versions=dict(prompt_versions),
        config_snapshot=dict(config_snapshot),
        statistics_summary=dict.fromkeys(_STATISTIC_KEYS, 0),
        current_stage=ReviewStage.VALIDATE_REQUEST,
        current_outline_output_id=None,
        final_artifact_id=None,
        created_at=now,
        updated_at=now,
    )


def create_run_step(
    *,
    run_id: str,
    step_key: ReviewStepKey | str,
    sequence: int,
    idempotency_key: str,
    input_refs: dict | None = None,
) -> RunStep:
    """创建一个尚未执行的 Review Step。"""
    if sequence < 1:
        raise ValueError("Step sequence 必须从 1 开始")
    if not idempotency_key:
        raise ValueError("Step 幂等键不能为空")
    refs = input_refs or {}
    _validate_json_object(refs, "Step 输入引用", _MAX_CONFIG_BYTES)
    now = datetime.now(UTC)
    return RunStep(
        step_id=str(uuid4()),
        run_id=run_id,
        step_key=ReviewStepKey(step_key),
        sequence=sequence,
        status=ReviewStepStatus.PENDING,
        idempotency_key=idempotency_key,
        input_refs=refs,
        output_refs={},
        error_code=None,
        started_at=None,
        completed_at=None,
        created_at=now,
    )


def create_review_source(
    *,
    review_run_id: str,
    arxiv_id: str,
    arxiv_version: str,
    rank: int,
    metadata_snapshot: dict,
) -> ReviewSource:
    """创建一条 arXiv 搜索来源记录。"""
    if not arxiv_id.strip() or not arxiv_version.strip():
        raise ValueError("arXiv ID 和版本不能为空")
    if rank < 1:
        raise ValueError("来源 rank 必须从 1 开始")
    _validate_json_object(metadata_snapshot, "arXiv 元数据快照", _MAX_METADATA_BYTES)
    now = datetime.now(UTC)
    return ReviewSource(
        source_id=str(uuid4()),
        review_run_id=review_run_id,
        arxiv_id=arxiv_id.strip(),
        arxiv_version=arxiv_version.strip(),
        rank=rank,
        metadata_snapshot=dict(metadata_snapshot),
        status=ReviewSourceStatus.DISCOVERED,
        paper_id=None,
        paper_version_id=None,
        failure_code=None,
        created_at=now,
        updated_at=now,
    )


def create_review_dependency(
    *,
    parent_run_id: str,
    dependency_type: ReviewDependencyType | str,
    target_run_id: str | None = None,
    target_paper_version_id: str | None = None,
    target_chunk_set_id: str | None = None,
) -> ReviewDependency:
    """创建受限类型依赖，并校验恰有一个匹配目标。"""
    kind = ReviewDependencyType(dependency_type)
    targets = {
        ReviewDependencyType.RUN: target_run_id,
        ReviewDependencyType.PAPER_VERSION: target_paper_version_id,
        ReviewDependencyType.CHUNK_SET: target_chunk_set_id,
    }
    if targets[kind] is None or sum(value is not None for value in targets.values()) != 1:
        raise ValueError("依赖目标必须且只能与 dependency_type 对应")
    return ReviewDependency(
        dependency_id=str(uuid4()),
        parent_run_id=parent_run_id,
        dependency_type=kind,
        status=ReviewDependencyStatus.PENDING,
        target_run_id=target_run_id,
        target_paper_version_id=target_paper_version_id,
        target_chunk_set_id=target_chunk_set_id,
        failure_code=None,
        created_at=datetime.now(UTC),
        satisfied_at=None,
    )


def create_review_output(
    *,
    review_run_id: str,
    output_type: ReviewOutputType | str,
    output_key: str,
    version: int,
    schema_version: str,
    payload: dict,
    idempotency_key: str,
) -> ReviewOutput:
    """创建不可覆盖的版本化结构化 Output。"""
    if version < 1:
        raise ValueError("Output 版本必须从 1 开始")
    if not output_key or len(output_key) > 100:
        raise ValueError("Output key 不能为空且不得超过 100 字符")
    if not idempotency_key:
        raise ValueError("Output 幂等键不能为空")
    _validate_version(schema_version, "Output Schema 版本")
    _validate_json_object(payload, "Output payload", _MAX_OUTPUT_BYTES)
    return ReviewOutput(
        output_id=str(uuid4()),
        review_run_id=review_run_id,
        output_type=ReviewOutputType(output_type),
        output_key=output_key,
        version=version,
        schema_version=schema_version,
        payload=dict(payload),
        idempotency_key=idempotency_key,
        created_at=datetime.now(UTC),
    )


def create_human_input_request(
    *,
    review_run_id: str,
    request_version: int,
    outline_output_id: str,
    allowed_actions: list[HumanInputAction | str],
) -> HumanInputRequest:
    """创建一次大纲人工输入请求。"""
    if request_version < 1:
        raise ValueError("HumanInputRequest 版本必须从 1 开始")
    actions = tuple(HumanInputAction(action) for action in allowed_actions)
    if not actions or len(set(actions)) != len(actions):
        raise ValueError("允许动作不能为空或重复")
    return HumanInputRequest(
        request_id=str(uuid4()),
        review_run_id=review_run_id,
        request_version=request_version,
        outline_output_id=outline_output_id,
        status=HumanInputRequestStatus.OPEN,
        allowed_actions=actions,
        resolved_input_id=None,
        created_at=datetime.now(UTC),
        resolved_at=None,
    )


def create_human_input(
    *,
    request: HumanInputRequest,
    action: HumanInputAction | str,
    payload: dict,
    submitted_by: str,
    idempotency_key: str,
) -> HumanInput:
    """为仍开放的请求创建一次合法输入。"""
    if request.status is not HumanInputRequestStatus.OPEN:
        raise ValueError("HumanInputRequest 必须仍处于开放状态")
    selected_action = HumanInputAction(action)
    if selected_action not in request.allowed_actions:
        raise ValueError(f"动作 {selected_action.value} 不允许")
    if not submitted_by or not idempotency_key:
        raise ValueError("提交者和幂等键不能为空")
    if len(idempotency_key) > 255:
        raise ValueError("幂等键不能超过 255 个字符")
    _validate_json_object(payload, "HumanInput payload", _MAX_METADATA_BYTES)
    return HumanInput(
        human_input_id=str(uuid4()),
        request_id=request.request_id,
        request_version=request.request_version,
        action=selected_action,
        payload=dict(payload),
        submitted_by=submitted_by,
        idempotency_key=idempotency_key,
        created_at=datetime.now(UTC),
    )


def create_artifact(
    *,
    review_run_id: str,
    project_id: str,
    owner_id: str,
    artifact_type: ArtifactType | str,
    storage_key: str,
    content_hash: str,
    size_bytes: int,
    media_type: str,
    idempotency_key: str,
    source_output_id: str | None = None,
    metadata: dict | None = None,
) -> Artifact:
    """创建只含 Storage 引用和校验信息的 Artifact 元数据。"""
    if not storage_key or storage_key.startswith("/") or ".." in storage_key.split("/"):
        raise ValueError("Artifact storage_key 必须是安全的相对键")
    if not _SHA256_PATTERN.fullmatch(content_hash):
        raise ValueError("Artifact 内容哈希必须是小写 SHA-256")
    if size_bytes < 0:
        raise ValueError("Artifact 大小不能为负数")
    if not owner_id or not media_type or not idempotency_key:
        raise ValueError("Artifact owner、media_type 和幂等键不能为空")
    safe_metadata = metadata or {}
    _validate_json_object(safe_metadata, "Artifact metadata", _MAX_METADATA_BYTES)
    return Artifact(
        artifact_id=str(uuid4()),
        review_run_id=review_run_id,
        project_id=project_id,
        owner_id=owner_id,
        artifact_type=ArtifactType(artifact_type),
        storage_key=storage_key,
        content_hash=content_hash,
        size_bytes=size_bytes,
        media_type=media_type,
        idempotency_key=idempotency_key,
        source_output_id=source_output_id,
        metadata=dict(safe_metadata),
        created_at=datetime.now(UTC),
    )
