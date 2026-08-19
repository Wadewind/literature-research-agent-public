"""pypdf 降级 Parser 适配器。

当主 Parser（Docling）抛出输入类错误时回退到 pypdf：
只提取每页纯文本，产出页级定位的 paragraph Element，
并标记 ``degraded`` 与能力缺失警告，不伪造布局精度。

pypdf 再次失败时抛 ``InvalidPdfInputError``，视为永久输入错误。
"""

import io
import logging

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from literature_agent.application.ports.document_parser import DocumentParser
from literature_agent.application.ports.storage import Storage
from literature_agent.domain.document_element import (
    ElementType,
    ParsedDocument,
    ParsedElement,
    ParsedLocation,
)
from literature_agent.domain.exceptions import InvalidPdfInputError, ParserResourceError
from literature_agent.domain.parse_profile import ParseProfile

logger = logging.getLogger(__name__)

PARSER_NAME = "pypdf"

# 降级路径的能力缺失警告
DEGRADED_WARNINGS = ["layout_missing", "table_missing"]


class PypdfDocumentParser(DocumentParser):
    """基于 pypdf 的降级 Parser：按页提取纯文本。"""

    def __init__(self, storage: Storage) -> None:
        """初始化 pypdf Parser。

        参数:
            storage: 受控文件存储，用于按 storage_key 读取 PDF 字节。
        """
        self._storage = storage

    async def parse(self, storage_key: str, profile: ParseProfile) -> ParsedDocument:
        """按页提取文本，返回降级的 ParsedDocument。

        异常:
            InvalidPdfInputError: 文件损坏、加密或无法解析（永久输入错误）。
            ParserResourceError: 内存等资源错误。
        """
        content = await self._storage.read(storage_key)
        try:
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                raise InvalidPdfInputError("PDF 已加密，无法解析")
            page_texts = [page.extract_text() or "" for page in reader.pages]
        except InvalidPdfInputError:
            raise
        except MemoryError as exc:
            raise ParserResourceError("解析时内存不足") from exc
        except (PdfReadError, ValueError, OSError, EOFError) as exc:
            raise InvalidPdfInputError(f"PDF 无法解析: {exc}") from exc

        elements: list[ParsedElement] = []
        for index, text in enumerate(page_texts):
            elements.append(
                ParsedElement(
                    element_type=ElementType.PARAGRAPH,
                    sequence=index + 1,
                    text=text.strip() or None,
                    locations=[ParsedLocation(page=index + 1)],
                )
            )
        return ParsedDocument(
            elements=elements,
            degraded=True,
            warnings=list(DEGRADED_WARNINGS),
        )
