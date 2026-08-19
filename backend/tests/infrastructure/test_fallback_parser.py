"""Fallback 组合 Parser 的降级分类测试（Stub 实现，不依赖真实解析）。"""

import pytest

from literature_agent.domain.document_element import ParsedDocument
from literature_agent.domain.exceptions import InvalidPdfInputError, ParserResourceError
from literature_agent.domain.parse_profile import ParseProfile
from literature_agent.infrastructure.parsing.fallback_parser import FallbackDocumentParser

_PROFILE = ParseProfile("docling", "2.0", {})


class _StubParser:
    """可配置行为的 Parser 桩。"""

    def __init__(self, result: ParsedDocument | None = None, error: Exception | None = None):
        self._result = result
        self._error = error
        self.calls = 0

    async def parse(self, storage_key: str, profile: ParseProfile) -> ParsedDocument:
        """记录调用并按配置返回或抛错。"""
        self.calls += 1
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


async def test_primary_success_skips_fallback() -> None:
    """主 Parser 成功时不调用降级。"""
    primary = _StubParser(result=ParsedDocument(elements=[]))
    fallback = _StubParser(result=ParsedDocument(elements=[], degraded=True))
    parser = FallbackDocumentParser(primary, fallback)

    parsed = await parser.parse("k", _PROFILE)

    assert parsed.degraded is False
    assert primary.calls == 1
    assert fallback.calls == 0


async def test_invalid_input_triggers_fallback() -> None:
    """主 Parser 输入类错误 → 降级成功并带 degraded 标记。"""
    primary = _StubParser(error=InvalidPdfInputError("损坏"))
    fallback = _StubParser(result=ParsedDocument(elements=[], degraded=True))
    parser = FallbackDocumentParser(primary, fallback)

    parsed = await parser.parse("k", _PROFILE)

    assert parsed.degraded is True
    assert fallback.calls == 1


async def test_resource_error_does_not_fallback() -> None:
    """资源类错误不降级，直接抛出。"""
    primary = _StubParser(error=ParserResourceError("内存不足"))
    fallback = _StubParser(result=ParsedDocument(elements=[]))
    parser = FallbackDocumentParser(primary, fallback)

    with pytest.raises(ParserResourceError):
        await parser.parse("k", _PROFILE)
    assert fallback.calls == 0


async def test_unknown_error_does_not_fallback() -> None:
    """未知异常不降级（保守，不掩盖 bug）。"""
    primary = _StubParser(error=RuntimeError("unexpected"))
    fallback = _StubParser(result=ParsedDocument(elements=[]))
    parser = FallbackDocumentParser(primary, fallback)

    with pytest.raises(RuntimeError):
        await parser.parse("k", _PROFILE)
    assert fallback.calls == 0


async def test_fallback_failure_is_permanent() -> None:
    """降级也失败 → 永久输入错误，异常传播给执行器。"""
    primary = _StubParser(error=InvalidPdfInputError("损坏"))
    fallback = _StubParser(error=InvalidPdfInputError("仍无法解析"))
    parser = FallbackDocumentParser(primary, fallback)

    with pytest.raises(InvalidPdfInputError):
        await parser.parse("k", _PROFILE)
