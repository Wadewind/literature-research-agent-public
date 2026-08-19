"""Document Parser 端口。"""

from typing import Protocol

from literature_agent.domain.document_element import ParsedDocument
from literature_agent.domain.parse_profile import ParseProfile


class DocumentParser(Protocol):
    """文档解析器的抽象端口。

    实现接受受控 Storage Object 引用和 Parse Profile，输出项目定义的
    规范化 ``ParsedDocument``，不直接暴露 Docling 等第三方类型。
    """

    async def parse(
        self,
        storage_key: str,
        profile: ParseProfile,
    ) -> ParsedDocument:
        """解析 Storage 中的 PDF 并返回规范化文档。

        参数:
            storage_key: 受控 Storage 中的对象键（PDF）。
            profile: 解析配置画像。

        返回:
            规范化解析结果。

        异常:
            Exception: 解析失败（损坏、加密、超时等），由调用方分类处理。
        """
        ...
