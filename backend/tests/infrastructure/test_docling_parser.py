"""Docling 主 Parser 的真实解析契约测试。

首次运行需要下载 Docling 布局模型（数百 MB），因此默认跳过，
显式启用：``AGENT_RUN_DOCLING_TESTS=1 uv run pytest tests/infrastructure/test_docling_parser.py``。
"""

import os
from pathlib import Path

import pytest

from literature_agent.domain.exceptions import InvalidPdfInputError
from literature_agent.domain.parse_profile import ParseProfile
from literature_agent.infrastructure.storage.local_storage import LocalFileStorage

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT_RUN_DOCLING_TESTS") != "1",
    reason="Docling 真实解析测试需显式启用（AGENT_RUN_DOCLING_TESTS=1）",
)

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "pdfs"


@pytest.fixture(scope="module")
def storage(tmp_path_factory) -> LocalFileStorage:
    """提供指向临时目录的本地存储。"""
    return LocalFileStorage(str(tmp_path_factory.mktemp("storage")))


async def test_docling_parses_text_pdf(storage) -> None:
    """真实 Docling 解析：文本 PDF 产出带页码定位的 Element。"""
    from literature_agent.infrastructure.parsing.docling_parser import (
        PARSER_NAME,
        PARSER_VERSION,
        DoclingDocumentParser,
    )

    content = (FIXTURE_DIR / "text_two_pages.pdf").read_bytes()
    await storage.write("test/paper.pdf", content)
    profile = ParseProfile(PARSER_NAME, PARSER_VERSION, {"ocr_enabled": False})

    parsed = await DoclingDocumentParser(storage).parse("test/paper.pdf", profile)

    texts = [e.text for e in parsed.elements if e.text]
    assert any("Hello Page One" in t for t in texts)
    assert any("Hello Page Two" in t for t in texts)
    pages = sorted({loc.page for e in parsed.elements for loc in e.locations})
    assert pages == [1, 2]
    for element in parsed.elements:
        assert element.locations, f"{element.element_type} 缺少来源定位"
        assert all(loc.page >= 1 for loc in element.locations)


async def test_docling_encrypted_pdf_raises_invalid_input(storage) -> None:
    """加密 PDF：Docling 失败分类为 InvalidPdfInputError（可降级）。"""
    from literature_agent.infrastructure.parsing.docling_parser import (
        DoclingDocumentParser,
    )

    content = (FIXTURE_DIR / "encrypted.pdf").read_bytes()
    await storage.write("test/encrypted.pdf", content)
    profile = ParseProfile("docling", "2", {})

    with pytest.raises(InvalidPdfInputError):
        await DoclingDocumentParser(storage).parse("test/encrypted.pdf", profile)
