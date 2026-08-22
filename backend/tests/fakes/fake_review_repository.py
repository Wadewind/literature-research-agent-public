"""ReviewRepository 的内存假实现。"""

from literature_agent.application.ports.review_repository import ReviewRepository
from literature_agent.domain.review import (
    Artifact,
    HumanInput,
    HumanInputRequest,
    HumanInputRequestStatus,
    ReviewDependency,
    ReviewOutput,
    ReviewRun,
    ReviewSource,
    RunStep,
)


class FakeReviewRepository(ReviewRepository):
    """应用测试使用的最小 Review 聚合存储。"""

    def __init__(self) -> None:
        self.review_runs: dict[str, ReviewRun] = {}
        self.run_scopes: dict[str, tuple[str, str]] = {}
        self.steps: list[RunStep] = []
        self.sources: list[ReviewSource] = []
        self.dependencies: list[ReviewDependency] = []
        self.outputs: list[ReviewOutput] = []
        self.requests: list[HumanInputRequest] = []
        self.inputs: list[HumanInput] = []
        self.artifacts: list[Artifact] = []

    def authorize_run(self, run_id: str, project_id: str, owner_id: str) -> None:
        """为 Fake 登记通用 Run 范围。"""
        self.run_scopes[run_id] = (project_id, owner_id)

    def _visible(self, run_id: str, project_id: str, owner_id: str) -> bool:
        return self.run_scopes.get(run_id) == (project_id, owner_id)

    async def add_review_run(self, review_run: ReviewRun) -> ReviewRun:
        self.review_runs[review_run.run_id] = review_run
        return review_run

    async def get_review_run_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> ReviewRun | None:
        if not self._visible(run_id, project_id, owner_id):
            return None
        return self.review_runs.get(run_id)

    async def list_waiting_dependency_run_ids(self, limit: int) -> list[str]:
        # 父 Run 的真实状态由 Service 持锁后二次校验；Fake 只提供候选。
        return list(self.review_runs)[:limit]

    async def add_step(self, step: RunStep) -> RunStep:
        self.steps.append(step)
        return step

    async def get_or_add_step(self, step: RunStep) -> RunStep:
        """按幂等键返回既有 Step。"""
        existing = next(
            (
                item
                for item in self.steps
                if item.run_id == step.run_id
                and item.idempotency_key == step.idempotency_key
            ),
            None,
        )
        if existing is not None:
            return existing
        return await self.add_step(step)

    async def advance_step(self, step: RunStep, expected_status: str) -> bool:
        """仅在当前状态匹配时推进 Step。"""
        for index, current in enumerate(self.steps):
            if current.step_id == step.step_id and current.status.value == expected_status:
                self.steps[index] = step
                return True
        return False

    async def list_steps_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> list[RunStep]:
        if not self._visible(run_id, project_id, owner_id):
            return []
        return sorted((x for x in self.steps if x.run_id == run_id), key=lambda x: x.sequence)

    async def add_source(self, source: ReviewSource) -> ReviewSource:
        self.sources.append(source)
        return source

    async def list_sources_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> list[ReviewSource]:
        if not self._visible(run_id, project_id, owner_id):
            return []
        return sorted(
            (x for x in self.sources if x.review_run_id == run_id), key=lambda x: x.rank
        )

    async def get_source_scoped_for_update(
        self, source_id: str, run_id: str, project_id: str, owner_id: str
    ) -> ReviewSource | None:
        if not self._visible(run_id, project_id, owner_id):
            return None
        return next(
            (
                source
                for source in self.sources
                if source.source_id == source_id and source.review_run_id == run_id
            ),
            None,
        )

    async def save_source(self, source: ReviewSource) -> None:
        for index, current in enumerate(self.sources):
            if current.source_id == source.source_id:
                self.sources[index] = source
                return
        raise KeyError(source.source_id)

    async def add_dependency(self, dependency: ReviewDependency) -> ReviewDependency:
        self.dependencies.append(dependency)
        return dependency

    async def list_dependencies_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> list[ReviewDependency]:
        if not self._visible(run_id, project_id, owner_id):
            return []
        return [x for x in self.dependencies if x.parent_run_id == run_id]

    async def save_dependency(self, dependency: ReviewDependency) -> None:
        for index, current in enumerate(self.dependencies):
            if current.dependency_id == dependency.dependency_id:
                self.dependencies[index] = dependency
                return
        raise KeyError(dependency.dependency_id)

    async def add_output(self, output: ReviewOutput) -> ReviewOutput:
        self.outputs.append(output)
        return output

    async def get_or_add_output(self, output: ReviewOutput) -> ReviewOutput:
        """按 Review Run 与幂等键复用既有 Output。"""
        existing = next(
            (
                item
                for item in self.outputs
                if item.review_run_id == output.review_run_id
                and item.idempotency_key == output.idempotency_key
            ),
            None,
        )
        return existing if existing is not None else await self.add_output(output)

    async def list_outputs_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> list[ReviewOutput]:
        if not self._visible(run_id, project_id, owner_id):
            return []
        return [x for x in self.outputs if x.review_run_id == run_id]

    async def add_human_input_request(
        self, request: HumanInputRequest
    ) -> HumanInputRequest:
        self.requests.append(request)
        return request

    async def get_open_human_input_request_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> HumanInputRequest | None:
        if not self._visible(run_id, project_id, owner_id):
            return None
        return next(
            (
                x
                for x in self.requests
                if x.review_run_id == run_id
                and x.status is HumanInputRequestStatus.OPEN
            ),
            None,
        )

    async def add_human_input(self, human_input: HumanInput) -> HumanInput:
        self.inputs.append(human_input)
        return human_input

    async def add_artifact(self, artifact: Artifact) -> Artifact:
        self.artifacts.append(artifact)
        return artifact

    async def list_artifacts_scoped(
        self, run_id: str, project_id: str, owner_id: str
    ) -> list[Artifact]:
        if not self._visible(run_id, project_id, owner_id):
            return []
        return [x for x in self.artifacts if x.review_run_id == run_id]
