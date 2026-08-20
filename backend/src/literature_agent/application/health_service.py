"""健康检查的应用服务。"""

from collections.abc import Sequence

from literature_agent.application.ports.readiness_probe import ReadinessProbe
from literature_agent.domain.health import HealthStatus


class HealthService:
    """服务健康的用例层。

    存活检查不访问外部依赖；就绪检查依次执行注入的依赖探针。
    """

    def __init__(self, probes: Sequence[ReadinessProbe] = ()) -> None:
        """保存需要参与就绪判断的依赖探针。"""
        self._probes = tuple(probes)

    def get_live_status(self) -> HealthStatus:
        """返回进程的存活状态。"""
        return HealthStatus(status="ok")

    async def get_ready_status(self) -> HealthStatus:
        """返回数据库和队列等外部依赖的就绪状态。"""
        dependencies: dict[str, str] = {}
        ready = True
        for probe in self._probes:
            try:
                await probe.check()
                dependencies[probe.name] = "ok"
            except Exception:
                dependencies[probe.name] = "unavailable"
                ready = False
        return HealthStatus(
            status="ok" if ready else "not_ready",
            dependencies=dependencies,
        )
