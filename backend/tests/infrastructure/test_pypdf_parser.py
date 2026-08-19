"""pypdf 降级 Parser 的契约测试（真实解析合成 Fixtures）。"""

from pathlib import Path

import pytest

from literature_agent.domain.document_element import ElementType, detect_document_warnings
from literature_agent.domain.exceptions import InvalidPdfInputError
from literature_agent.domain.parse_profile import ParseProfile
from literature_agent.infrastructure.parsing.pypdf_parser import PypdfDocumentParser
from literature_agent.infrastructure.storage.local_storage import LocalFileStorage

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "pdfs"
_PROFILE = ParseProfile("pypdf", "6", {})


@pytest.fixture
def storage(tmp_path: Path) -> LocalFileStorage:
    """提供指向临时目录的本地存储。"""
    return LocalFileStorage(str(tmp_path))


async def _parse(storage: LocalFileStorage, fixture: str):
    """把 Fixture 写入存储并解析。"""
    content = (FIXTURE_DIR / fixture).read_bytes()
    await storage.write("test/paper.pdf", content)
    return await PypdfDocumentParser(storage).parse("test/paper.pdf", _PROFILE)


async def test_two_page_text_pdf_yields_page_level_paragraphs(storage) -> None:
    """多页文本 PDF：每页一个 paragraph，页级定位，无 bbox。"""
    parsed = await _parse(storage, "text_two_pages.pdf")

    assert parsed.degraded is True
    assert parsed.warnings == ["layout_missing", "table_missing"]
    assert len(parsed.elements) == 2
    first, second = parsed.elements
    assert first.element_type == ElementType.PARAGRAPH
    assert first.sequence == 1
    assert first.text == "Hello Page One"
    assert len(first.locations) == 1
    assert first.locations[0].page == 1
    assert first.locations[0].bbox is None
    assert second.text == "Hello Page Two"
    assert second.locations[0].page == 2
    assert detect_document_warnings(parsed) == []


async def test_blank_pdf_yields_possibly_scanned_warning(storage) -> None:
    """空白 PDF：解析成功但文本为空，领域规则给出 possibly_scanned。"""
    parsed = await _parse(storage, "blank.pdf")

    assert len(parsed.elements) == 1
    assert parsed.elements[0].text is None
    assert detect_document_warnings(parsed) == ["possibly_scanned"]


async def test_corrupted_pdf_raises_invalid_input(storage) -> None:
    """结构损坏的 PDF：抛 InvalidPdfInputError。"""
    with pytest.raises(InvalidPdfInputError):
        await _parse(storage, "corrupted.pdf")


async def test_encrypted_pdf_raises_invalid_input(storage) -> None:
    """加密 PDF：抛 InvalidPdfInputError（永久输入错误）。"""
    with pytest.raises(InvalidPdfInputError):
        await _parse(storage, "encrypted.pdf")
