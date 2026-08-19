"""确定性 Fake Parser 适配器。

切片 6 用它在不依赖真实 PDF 解析的情况下打通
Run → Parse Revision → Element/来源定位 的完整闭环；
切片 7 由 Docling 主 Parser 与 pypdf 降级 Parser 替换。

输出结构固定且确定：两页、两个章节、一个带题注的表格，
其中"引言段落二"跨页，用于验证多来源定位。
"""

from literature_agent.application.ports.document_parser import DocumentParser
from literature_agent.domain.document_element import (
    ElementType,
    ParsedDocument,
    ParsedElement,
    ParsedLocation,
)
from literature_agent.domain.parse_profile import ParseProfile

PARSER_NAME = "fake"
PARSER_VERSION = "1.0"


def _bbox(index: int) -> list[float]:
    """生成确定的合成 Bounding Box。"""
    top = float(50 + index * 40)
    return [50.0, top, 550.0, top + 20.0]


class FakeDocumentParser(DocumentParser):
    """返回固定结构的 Fake Parser，用于闭环与契约测试。"""

    async def parse(self, storage_key: str, profile: ParseProfile) -> ParsedDocument:
        """忽略文件内容，返回确定的合成文档结构。

        参数:
            storage_key: PDF 在 Storage 中的键（仅记录，不读取）。
            profile: 解析配置画像。
        """
        elements = [
            ParsedElement(
                element_type=ElementType.TITLE,
                sequence=1,
                text="Fake 论文标题",
                locations=[ParsedLocation(page=1, bbox=_bbox(1), parser_ref="fake:p1:e1")],
            ),
            ParsedElement(
                element_type=ElementType.SECTION_HEADING,
                sequence=2,
                text="1 引言",
                section_path="1",
                locations=[ParsedLocation(page=1, bbox=_bbox(2), parser_ref="fake:p1:e2")],
            ),
            ParsedElement(
                element_type=ElementType.PARAGRAPH,
                sequence=3,
                text="引言段落一：介绍研究背景与动机。",
                section_path="1",
                parent_index=1,
                locations=[ParsedLocation(page=1, bbox=_bbox(3), parser_ref="fake:p1:e3")],
            ),
            ParsedElement(
                element_type=ElementType.PARAGRAPH,
                sequence=4,
                text="引言段落二：跨页段落，用于验证多来源定位。",
                section_path="1",
                parent_index=1,
                locations=[
                    ParsedLocation(page=1, bbox=_bbox(4), parser_ref="fake:p1:e4"),
                    ParsedLocation(page=2, bbox=_bbox(0), parser_ref="fake:p2:e0"),
                ],
            ),
            ParsedElement(
                element_type=ElementType.SECTION_HEADING,
                sequence=5,
                text="2 方法",
                section_path="2",
                locations=[ParsedLocation(page=2, bbox=_bbox(1), parser_ref="fake:p2:e1")],
            ),
            ParsedElement(
                element_type=ElementType.PARAGRAPH,
                sequence=6,
                text="方法段落一：描述实验设置。",
                section_path="2",
                parent_index=4,
                locations=[ParsedLocation(page=2, bbox=_bbox(2), parser_ref="fake:p2:e2")],
            ),
            ParsedElement(
                element_type=ElementType.TABLE,
                sequence=7,
                section_path="2",
                parent_index=4,
                payload={"rows": [["指标", "值"], ["准确率", "0.90"]]},
                locations=[ParsedLocation(page=2, bbox=_bbox(3), parser_ref="fake:p2:e3")],
            ),
            ParsedElement(
                element_type=ElementType.CAPTION,
                sequence=8,
                text="表 1：示例结果",
                section_path="2",
                parent_index=6,
                locations=[ParsedLocation(page=2, bbox=_bbox(4), parser_ref="fake:p2:e4")],
            ),
        ]
        return ParsedDocument(elements=elements)
