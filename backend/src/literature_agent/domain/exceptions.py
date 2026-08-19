"""领域异常。"""


class ProjectNotFoundError(Exception):
    """请求的资源不存在或当前 actor 无权访问。"""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(f"Project {project_id} 不存在")


class RunNotFoundError(Exception):
    """Run 不存在或当前 actor 无权访问。"""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"Run {run_id} 不存在")


class InvalidRunTransitionError(Exception):
    """Run 状态转换非法。"""

    def __init__(
        self,
        run_id: str,
        from_status: str,
        to_status: str,
    ) -> None:
        self.run_id = run_id
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"Run {run_id} 无法从 {from_status} 转换到 {to_status}")


class RunConcurrentModificationError(Exception):
    """Run 并发修改冲突。"""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"Run {run_id} 并发修改冲突")


class FileValidationError(Exception):
    """上传文件校验失败（类型、大小、损坏等）。"""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class IdempotencyConflictError(Exception):
    """相同幂等键对应不同请求。"""

    def __init__(self, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(f"Idempotency-Key {idempotency_key} 已用于不同请求")
