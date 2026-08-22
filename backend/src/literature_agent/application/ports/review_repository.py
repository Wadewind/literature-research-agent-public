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

    async def add_step(self, step: RunStep) -> RunStep:
        """追加一个 RunStep。"""
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

    async def add_output(self, output: ReviewOutput) -> ReviewOutput:
        """追加一个版本化 ReviewOutput；不提供覆盖更新。"""
        ...

    async def list_outputs_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> list[ReviewOutput]:
        """列出有权访问的 ReviewOutput。"""
        ...

    async def add_human_input_request(self, request: HumanInputRequest) -> HumanInputRequest:
        """追加人工输入请求。"""
        ...

    async def get_open_human_input_request_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> HumanInputRequest | None:
        """查询当前有权访问的未解决请求。"""
        ...

    async def add_human_input(self, human_input: HumanInput) -> HumanInput:
        """保存一次不可变人工输入。"""
        ...

    async def add_artifact(self, artifact: Artifact) -> Artifact:
        """保存 Artifact 元数据，不保存文件正文。"""
        ...

    async def list_artifacts_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> list[Artifact]:
        """列出有权访问的 Artifact 元数据。"""
        ...
