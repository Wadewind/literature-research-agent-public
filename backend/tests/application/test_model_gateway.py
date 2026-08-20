"""ModelGateway 应用服务测试（切片 3）。"""

import pytest

from literature_agent.application.model_gateway import ModelGateway
from literature_agent.domain.model_errors import ModelRateLimitError
from literature_agent.domain.model_invocation import (
    InvocationStatus,
    ModelCapability,
)
from literature_agent.domain.model_types import ChatMessage
from tests.fakes.fake_chat_model import FakeChatModel
from tests.fakes.fake_embedding_model import FakeEmbeddingModel
from tests.fakes.fake_model_invocation_repository import (
    FakeModelInvocationRepository,
)
from tests.fakes.fake_project_repository import fake_session


def _make_gateway(
    invocation_repo: FakeModelInvocationRepository,
    embedding_model: FakeEmbeddingModel | None = None,
    chat_model: FakeChatModel | None = None,
) -> ModelGateway:
    """构造使用 Fake Port 与 Fake Repository 的 Gateway。"""
    return ModelGateway(
        embedding_model=embedding_model or FakeEmbeddingModel(),
        chat_model=chat_model or FakeChatModel(),
        session_factory=fake_session,
        invocation_repo_factory=lambda _session: invocation_repo,
    )


async def test_embed_records_invocation() -> None:
    """Embedding 成功后持久化一条成功调用记录。"""
    repo = FakeModelInvocationRepository()
    gateway = _make_gateway(repo)

    result = await gateway.embed(["第一段文本"], run_id="run-1")

    assert len(result.vectors) == 1
    records = repo.all()
    assert len(records) == 1
    record = records[0]
    assert record.run_id == "run-1"
    assert record.capability == ModelCapability.EMBEDDING
    assert record.provider == "fake"
    assert record.model == "fake-embedding"
    assert record.status == InvocationStatus.SUCCEEDED
    assert record.prompt_tokens == result.usage.prompt_tokens
    assert record.completion_tokens is None
    assert record.error_type is None
    assert record.latency_ms >= 0


async def test_generate_records_invocation() -> None:
    """Chat 生成成功后持久化一条成功调用记录。"""
    repo = FakeModelInvocationRepository()
    chat = FakeChatModel(responses=['{"answer_status": "answered"}'])
    gateway = _make_gateway(repo, chat_model=chat)

    result = await gateway.generate(
        [ChatMessage(role="user", content="问题")],
        json_schema={"type": "object"},
        run_id="run-2",
    )

    assert result.content == '{"answer_status": "answered"}'
    record = repo.all()[0]
    assert record.run_id == "run-2"
    assert record.capability == ModelCapability.CHAT
    assert record.status == InvocationStatus.SUCCEEDED
    assert record.prompt_tokens == 10
    assert record.completion_tokens == 5


async def test_failure_recorded_and_reraised() -> None:
    """模型失败：记录 failed（含错误分类）并把原异常抛给调用方。"""
    repo = FakeModelInvocationRepository()
    chat = FakeChatModel(responses=[ModelRateLimitError("限流")])
    gateway = _make_gateway(repo, chat_model=chat)

    with pytest.raises(ModelRateLimitError):
        await gateway.generate([ChatMessage(role="user", content="问题")])

    record = repo.all()[0]
    assert record.run_id is None
    assert record.status == InvocationStatus.FAILED
    assert record.error_type == "ModelRateLimitError"
    assert record.prompt_tokens is None


async def test_recording_failure_does_not_affect_result() -> None:
    """调用记录持久化失败只记日志，不影响调用结果。"""
    repo = FakeModelInvocationRepository(fail_on_add=True)
    gateway = _make_gateway(repo)

    result = await gateway.embed(["文本"])

    assert len(result.vectors) == 1
    assert repo.all() == []


async def test_recording_failure_does_not_mask_model_error() -> None:
    """记录失败不掩盖模型错误本身。"""
    repo = FakeModelInvocationRepository(fail_on_add=True)
    chat = FakeChatModel(responses=[ModelRateLimitError("限流")])
    gateway = _make_gateway(repo, chat_model=chat)

    with pytest.raises(ModelRateLimitError):
        await gateway.generate([ChatMessage(role="user", content="问题")])
