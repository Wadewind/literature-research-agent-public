"""Model Invocation Repository 端口。"""

from typing import Protocol

from literature_agent.domain.model_invocation import ModelInvocation


class ModelInvocationRepository(Protocol):
    """模型调用记录持久化的抽象端口。"""

    async def add(self, invocation: ModelInvocation) -> ModelInvocation:
        """保存一条模型调用记录。"""
        ...

    async def list_by_run(self, run_id: str) -> list[ModelInvocation]:
        """按 Run 查询调用记录，按创建时间升序返回。"""
        ...
