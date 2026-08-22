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


class RunSchedulingError(Exception):
    """Run 的 Outbox 缺失或状态异常，无法保证再次投递。"""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"Run {run_id} 无法重新调度")


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


class CheckpointUnavailableError(RuntimeError):
    """Checkpoint 数据库临时不可用，可交给 Run 级失败策略重试。"""


class CheckpointDataError(RuntimeError):
    """Checkpoint 状态无效或不能安全反序列化，重复执行无法自愈。"""


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


class EvidenceScopeError(Exception):
    """Evidence 范围类永久错误：Run 缺少版本范围快照，或检索结果
    的 paper/version 不在快照内（防御纵深，正常链路不应发生）。

    重试无法改变结果，直接令 rag_answer Run 进入 FAILED。
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class ConversationNotFoundError(Exception):
    """Conversation 不存在或当前 actor 无权访问。"""

    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        super().__init__(f"Conversation {conversation_id} 不存在")


class ConversationBusyError(Exception):
    """Conversation 已有未完成的回答 Run，拒绝并发提问。"""

    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        super().__init__(f"Conversation {conversation_id} 已有进行中的回答")


class ProjectNotIndexedError(Exception):
    """提问范围内没有任何 ready ChunkSet，快速失败。"""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(f"Project {project_id} 的提问范围尚未完成索引")


class InvalidScopeError(Exception):
    """Conversation 范围非法：scope_mode 非法、selected_papers 为空，
    或含未收录/已归档/其他 owner 的 Paper。"""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class EvidenceNotFoundError(Exception):
    """Evidence 不存在或不属于当前 actor 可见的 Project。"""

    def __init__(self, evidence_id: str) -> None:
        self.evidence_id = evidence_id
        super().__init__(f"Evidence {evidence_id} 不存在")


class RagAnswerInputError(Exception):
    """rag_answer Run 输入类永久错误：缺少 conversation_id、
    user_message_id、版本范围快照，或关联的 User Message 不存在。

    重试无法改变结果，直接令 Run 进入 FAILED。
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class ModelOutputInvalidError(Exception):
    """模型结构化输出经一次修复重试后仍非法（解析失败或引用校验失败）。

    属稳定的永久失败：Run 直接 FAILED（错误类型 model_output_invalid），
    不再消耗 Run 层重试预算。
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class AnswerOutputParseError(Exception):
    """模型结构化输出解析失败：非 JSON 或不符合 RagAnswerOutput Schema。

    属可修复的模型输出问题：由 Run 编排层触发一次结构修复重试，
    仍失败则 Run 稳定 FAILED（不注册为 Run 层永久错误）。
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)
