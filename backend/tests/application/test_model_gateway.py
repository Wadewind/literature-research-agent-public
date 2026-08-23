"""ModelGateway 应用服务测试（切片 3）。"""

import logging
from unittest.mock import Mock

import pytest

from literature_agent.application import model_gateway as gateway_module
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


async def test_embed_records_invocation(monkeypatch) -> None:
    """Embedding 成功后持久化一条成功调用记录。"""
    repo = FakeModelInvocationRepository()
    gateway = _make_gateway(repo)
    recorder = Mock()
    monkeypatch.setattr(gateway_module, "metrics", recorder)

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
    recorder.record_model.assert_called_once()
    metric_call = recorder.record_model.call_args
    assert len(metric_call.args) == 3
    assert metric_call.args[:2] == (
        ModelCapability.EMBEDDING,
        InvocationStatus.SUCCEEDED,
    )
    assert metric_call.kwargs == {}


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


async def test_failure_recorded_and_reraised(monkeypatch) -> None:
    """模型失败：记录 failed（含错误分类）并把原异常抛给调用方。"""
    repo = FakeModelInvocationRepository()
    chat = FakeChatModel(responses=[ModelRateLimitError("限流")])
    gateway = _make_gateway(repo, chat_model=chat)
    recorder = Mock()
    monkeypatch.setattr(gateway_module, "metrics", recorder)

    with pytest.raises(ModelRateLimitError):
        await gateway.generate([ChatMessage(role="user", content="问题")])

    record = repo.all()[0]
    assert record.run_id is None
    assert record.status == InvocationStatus.FAILED
    assert record.error_type == "ModelRateLimitError"
    assert record.prompt_tokens is None
    recorder.record_model.assert_called_once()
    assert len(recorder.record_model.call_args.args) == 3
    assert recorder.record_model.call_args.args[:2] == (
        ModelCapability.CHAT,
        InvocationStatus.FAILED,
    )
    assert recorder.record_model.call_args.kwargs == {}


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


async def test_model_success_log_has_safe_summary_without_prompt(caplog) -> None:
    """成功日志只含 Provider 摘要，不包含 message 或模型结果正文。"""
    repo = FakeModelInvocationRepository()
    chat = FakeChatModel(responses=['{"private": "model-output"}'])
    gateway = _make_gateway(repo, chat_model=chat)
    caplog.set_level(logging.INFO, logger="literature_agent.application.model_gateway")

    await gateway.generate(
        [ChatMessage(role="user", content="private-user-question")], run_id="run-1"
    )

    record = next(
        r for r in caplog.records if getattr(r, "event", None) == "model_request_completed"
    )
    assert record.operation == "chat"
    assert record.provider == "fake"
    assert record.model == "fake-chat"
    assert record.run_id == "run-1"
    assert not hasattr(record, "messages")
    assert "private-user-question" not in record.getMessage()
    assert "model-output" not in record.getMessage()


async def test_model_failure_log_uses_error_type_without_exception_message(caplog) -> None:
    """失败日志只记录稳定错误类型，不记录异常正文。"""
    repo = FakeModelInvocationRepository()
    chat = FakeChatModel(responses=[ModelRateLimitError("secret-provider-body")])
    gateway = _make_gateway(repo, chat_model=chat)
    caplog.set_level(logging.WARNING, logger="literature_agent.application.model_gateway")

    with pytest.raises(ModelRateLimitError):
        await gateway.generate([ChatMessage(role="user", content="private-question")])

    record = next(r for r in caplog.records if getattr(r, "event", None) == "model_request_failed")
    assert record.error_code == "ModelRateLimitError"
    assert record.exception_type == "ModelRateLimitError"
    assert "secret-provider-body" not in record.getMessage()
    assert record.exc_info is None


async def test_logging_failure_does_not_change_model_result() -> None:
    """日志 Handler 故障不能把成功模型调用变成业务失败。"""

    class _FailingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            del record
            raise RuntimeError("logging unavailable")

    repo = FakeModelInvocationRepository()
    gateway = _make_gateway(repo)
    logger = logging.getLogger("literature_agent.application.model_gateway")
    previous_handlers = list(logger.handlers)
    previous_propagate = logger.propagate
    logger.handlers = [_FailingHandler()]
    logger.propagate = False
    try:
        result = await gateway.embed(["文本"], run_id="run-log-failure")
    finally:
        logger.handlers = previous_handlers
        logger.propagate = previous_propagate

    assert len(result.vectors) == 1
    assert repo.all()[0].run_id == "run-log-failure"
