"""Run Repository 的内存假实现。"""

from literature_agent.application.ports.run_repository import RunRepository
from literature_agent.domain.run import Run, RunStatus


class FakeRunRepository(RunRepository):
    """不依赖数据库的 Run Repository 假实现。"""

    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}

    async def add(self, run: Run) -> Run:
        """将 Run 存入内存。"""
        self._runs[run.run_id] = run
        return run

    async def get_by_id(self, run_id: str) -> Run | None:
        """根据 ID 返回 Run。"""
        return self._runs.get(run_id)

    async def get_by_id_for_update(self, run_id: str, owner_id: str) -> Run | None:
        """根据 ID 和所有者返回 Run（不实现真实锁）。"""
        run = self._runs.get(run_id)
        if run is None or run.owner_id != owner_id:
            return None
        return run

    async def update_status(
        self,
        run_id: str,
        expected_status: RunStatus,
        new_status: RunStatus,
        new_event_sequence: int,
    ) -> bool:
        """条件更新 Run 状态。"""
        run = self._runs.get(run_id)
        if run is None or run.status != expected_status:
            return False
        self._runs[run_id] = Run(
            run_id=run.run_id,
            project_id=run.project_id,
            owner_id=run.owner_id,
            run_type=run.run_type,
            status=new_status,
            input_payload=run.input_payload,
            result_payload=run.result_payload,
            event_sequence=new_event_sequence,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )
        return True
