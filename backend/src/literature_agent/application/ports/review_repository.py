"""Review Workflow 聚合持久化端口。"""

from typing import Protocol

from literature_agent.domain.review import (
    Artifact,
    HumanInput,
    HumanInputRequest,
    ReviewDependency,
    ReviewOutput,
    ReviewRun,
    ReviewSource,
    RunStep,
)


class ReviewRepository(Protocol):
    """Review 扩展记录的持久化端口。

    所有读取方法都显式接收 ``project_id`` 与 ``owner_id`` 并通过通用
    Run 校验范围。写入方法只供已经完成授权的应用服务或 Worker 使用，
    其实体必须引用该已授权 Review Run。
    """

    async def add_review_run(self, review_run: ReviewRun) -> ReviewRun:
        """保存 ReviewRun 扩展记录。"""
        ...

    async def get_review_run_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> ReviewRun | None:
        """按 Run、Project、owner 查询 ReviewRun。"""
        ...

    async def get_review_run_scoped_for_update(
        self, run_id: str, project_id: str, owner_id: str
    ) -> ReviewRun | None:
        """锁定并读取当前范围内的 ReviewRun 扩展记录。"""
        ...

    async def advance_review_outline(
        self,
        review_run: ReviewRun,
        *,
        expected_outline_output_id: str | None,
    ) -> bool:
        """条件式推进当前大纲指针和 Review Stage。"""
        ...

    async def advance_review_stage(
        self,
        review_run: ReviewRun,
        *,
        expected_stage: str,
    ) -> bool:
        """仅在当前 Stage 匹配时推进 ReviewRun。"""
        ...

    async def advance_review_final(
        self,
        review_run: ReviewRun,
        *,
        expected_stage: str,
        expected_final_artifact_id: str | None,
    ) -> bool:
        """条件式推进最终 Artifact 指针和 Review Stage。"""
        ...

    async def list_waiting_dependency_run_ids(self, limit: int) -> list[str]:
        """有界列出等待论文依赖的 Review Run ID，供内部对账使用。"""
        ...

    async def add_step(self, step: RunStep) -> RunStep:
        """追加一个 RunStep。"""
        ...

    async def get_or_add_step(self, step: RunStep) -> RunStep:
        """按 ``(run_id, idempotency_key)`` 原子创建或返回既有 Step。"""
        ...

    async def advance_step(self, step: RunStep, expected_status: str) -> bool:
        """仅在当前状态匹配时推进 Step，拒绝并发回退终态。"""
        ...

    async def list_steps_scoped(self, run_id: str, project_id: str, owner_id: str) -> list[RunStep]:
        """按执行顺序列出有权访问的 RunStep。"""
        ...

    async def add_source(self, source: ReviewSource) -> ReviewSource:
        """追加一条 ReviewSource。"""
        ...

    async def list_sources_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> list[ReviewSource]:
        """按 arXiv 排名列出有权访问的来源。"""
        ...

    async def get_source_scoped_for_update(
        self, source_id: str, run_id: str, project_id: str, owner_id: str
    ) -> ReviewSource | None:
        """锁定并读取有权访问的来源，供幂等状态推进。"""
        ...

    async def save_source(self, source: ReviewSource) -> None:
        """保存来源的受控导入状态与 Paper 关联。"""
        ...

    async def add_dependency(self, dependency: ReviewDependency) -> ReviewDependency:
        """追加一条父 Run 依赖。"""
        ...

    async def list_dependencies_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> list[ReviewDependency]:
        """列出有权访问的父 Run 依赖。"""
        ...

    async def save_dependency(self, dependency: ReviewDependency) -> None:
        """保存依赖的单向状态推进。"""
        ...

    async def add_output(self, output: ReviewOutput) -> ReviewOutput:
        """追加一个版本化 ReviewOutput；不提供覆盖更新。"""
        ...

    async def get_or_add_output(self, output: ReviewOutput) -> ReviewOutput:
        """按 ``(review_run_id, idempotency_key)`` 原子创建或返回 Output。"""
        ...

    async def list_outputs_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> list[ReviewOutput]:
        """列出有权访问的 ReviewOutput。"""
        ...

    async def add_human_input_request(self, request: HumanInputRequest) -> HumanInputRequest:
        """追加人工输入请求。"""
        ...

    async def get_or_add_human_input_request(self, request: HumanInputRequest) -> HumanInputRequest:
        """按 Run/request_version 原子创建或返回请求。"""
        ...

    async def get_open_human_input_request_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> HumanInputRequest | None:
        """查询当前有权访问的未解决请求。"""
        ...

    async def get_human_input_request_scoped_for_update(
        self,
        request_id: str,
        run_id: str,
        project_id: str,
        owner_id: str,
    ) -> HumanInputRequest | None:
        """锁定并读取属于当前范围的指定人工输入请求。"""
        ...

    async def resolve_human_input_request(
        self, request: HumanInputRequest, *, expected_status: str
    ) -> bool:
        """仅在状态匹配时解决请求。"""
        ...

    async def add_human_input(self, human_input: HumanInput) -> HumanInput:
        """保存一次不可变人工输入。"""
        ...

    async def get_or_add_human_input(self, human_input: HumanInput) -> HumanInput:
        """按提交者幂等键或 request 唯一键收敛并发提交。"""
        ...

    async def get_human_input_scoped(
        self,
        human_input_id: str,
        run_id: str,
        project_id: str,
        owner_id: str,
    ) -> HumanInput | None:
        """读取属于当前 Review Run 范围的持久人工输入。"""
        ...

    async def get_human_input_by_idempotency_scoped(
        self,
        submitted_by: str,
        idempotency_key: str,
        run_id: str,
        project_id: str,
        owner_id: str,
    ) -> HumanInput | None:
        """按提交者幂等键回放当前范围内的人工输入。"""
        ...

    async def get_latest_resolved_human_input_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> tuple[HumanInputRequest, HumanInput] | None:
        """读取最高版本已解决请求及其持久输入，供 Worker Resume。"""
        ...

    async def add_artifact(self, artifact: Artifact) -> Artifact:
        """保存 Artifact 元数据，不保存文件正文。"""
        ...

    async def get_or_add_artifact(self, artifact: Artifact) -> Artifact:
        """按 Review Run/幂等键原子创建或回读 Artifact。"""
        ...

    async def get_artifact_scoped(
        self,
        artifact_id: str,
        run_id: str,
        project_id: str,
        owner_id: str,
    ) -> Artifact | None:
        """读取当前范围内的指定 Artifact。"""
        ...

    async def list_artifacts_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> list[Artifact]:
        """列出有权访问的 Artifact 元数据。"""
        ...
