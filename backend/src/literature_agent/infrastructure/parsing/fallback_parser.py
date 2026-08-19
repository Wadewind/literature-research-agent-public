"""带降级的组合 Parser：Docling 主路径 + pypdf 回退。

降级规则（2026-08-20 定稿）：
- 主 Parser 抛 ``InvalidPdfInputError``（损坏/加密/结构类）→ 尝试 pypdf；
- 超时、资源类和未知异常不降级，原样抛出；
- pypdf 也失败 → 永久输入错误，异常原样传播由执行器标记 FAILED。

组合对外以主 Parser 的身份（名称/版本）出现；是否走过降级
通过 ``ParsedDocument.degraded`` 与 warnings 表达。
"""

import logging

from literature_agent.application.ports.document_parser import DocumentParser
from literature_agent.domain.document_element import ParsedDocument
from literature_agent.domain.exceptions import InvalidPdfInputError
from literature_agent.domain.parse_profile import ParseProfile

logger = logging.getLogger(__name__)


class FallbackDocumentParser(DocumentParser):
    """主 Parser 失败于输入类错误时回退到降级 Parser。"""

    def __init__(self, primary: DocumentParser, fallback: DocumentParser) -> None:
        """初始化组合 Parser。

        参数:
            primary: 主 Parser（Docling）。
            fallback: 降级 Parser（pypdf）。
        """
        self._primary = primary
        self._fallback = fallback

    async def parse(self, storage_key: str, profile: ParseProfile) -> ParsedDocument:
        """先走主 Parser，输入类错误时降级。

        异常:
            InvalidPdfInputError: 主路径与降级路径都无法解析（永久输入错误）。
        """
        try:
            return await self._primary.parse(storage_key, profile)
        except InvalidPdfInputError:
            logger.warning("主 Parser 输入类失败，降级到 pypdf: storage_key=%s", storage_key)
        return await self._fallback.parse(storage_key, profile)
