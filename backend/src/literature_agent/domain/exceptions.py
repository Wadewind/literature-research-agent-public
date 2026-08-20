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


class PaperVersionNotFoundError(Exception):
    """Paper Version 不存在或不属于当前 actor 可见范围。"""

    def __init__(self, version_id: str) -> None:
        self.version_id = version_id
        super().__init__(f"PaperVersion {version_id} 不存在")


class PaperNotFoundError(Exception):
    """Paper 不存在或当前 actor 无权访问。"""

    def __init__(self, paper_id: str) -> None:
        self.paper_id = paper_id
        super().__init__(f"Paper {paper_id} 不存在")


class ProjectArchivedError(Exception):
    """已归档 Project 拒绝写操作。"""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(f"Project {project_id} 已归档")


class ProjectHasActiveRunsError(Exception):
    """Project 存在非终态 Run，不能归档。"""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(f"Project {project_id} 存在未完成的 Run")


class PaperArchivedError(Exception):
    """已归档 Paper 拒绝收录等写操作。"""

    def __init__(self, paper_id: str) -> None:
        self.paper_id = paper_id
        super().__init__(f"Paper {paper_id} 已归档")


class DocumentNotReadyError(Exception):
    """Paper Version 尚无当前 Parse Revision，文档内容不可用。"""

    def __init__(self, version_id: str) -> None:
        self.version_id = version_id
        super().__init__(f"PaperVersion {version_id} 尚未完成解析")


class ParserError(Exception):
    """解析失败的基类，按子类决定降级与重试策略。"""
class InvalidPdfInputError(ParserError):
    """输入类错误：文件损坏、加密或结构异常。

    主 Parser（Docling）抛出时触发 pypdf 降级；降级 Parser 再次抛出时
    视为永久输入错误，直接失败。
    """


class ParserResourceError(ParserError):
    """资源类错误（内存、进程等）：不降级，直接失败并交由重试策略处理。"""


class IndexingInputError(Exception):
    """索引输入类永久错误：Parse Revision 不存在或尚未解析成功。

    重试无法改变结果，直接令 indexing Run 进入 FAILED。
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)
