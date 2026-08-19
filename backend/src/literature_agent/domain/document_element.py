"""Document Element 与来源定位领域模型。

Element 是解析得到的稳定文档单元；SourceLocation 把 Element 回溯到
原始 PDF 页码和可选坐标。Parser 输出 ``ParsedDocument`` 值对象，
由应用层分配 ID、计算内容哈希后持久化为 DocumentElement。
"""

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import uuid4


class ElementType(StrEnum):
    """Element 类型枚举（首版最小集合）。"""

    TITLE = "title"
    SECTION_HEADING = "section_heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    FORMULA = "formula"
    FIGURE = "figure"
    CAPTION = "caption"
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"


def compute_content_hash(element_type: str, text: str | None, payload: dict) -> str:
    """计算 Element 内容哈希（规范化 JSON + SHA-256）。"""
    canonical = json.dumps(
        {"type": element_type, "text": text, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ElementSourceLocation:
    """Element 在原始 PDF 中的一个来源定位。

    属性:
        location_id: 定位标识符。
        element_id: 所属 Element。
        page: PDF 页码（从 1 开始）。
        bbox: 可选 Bounding Box ``[x0, y0, x1, y1]``，无法可靠获得时为 None。
        parser_ref: Parser 原始引用（例如 Docling 节点路径）。
        char_range: 可选字符范围 ``[start, end]``。
    """

    location_id: str
    element_id: str
    page: int
    bbox: list[float] | None = None
    parser_ref: str | None = None
    char_range: list[int] | None = None


@dataclass(frozen=True, slots=True)
class DocumentElement:
    """规范化文档单元。

    属性:
        element_id: Element 标识符。
        revision_id: 所属 Parse Revision。
        element_type: 类型。
        sequence: 全文阅读顺序，从 1 开始，Revision 内唯一。
        parent_element_id: 可选父 Element。
        section_path: 章节路径，例如 ``1.2``；不属于任何章节时为 None。
        text: 规范化文本；表格等结构化内容可为 None。
        payload: 受控结构化负载（表格单元格等）。
        content_hash: 内容哈希。
        warnings: 解析质量/警告标记，例如 ``degraded``。
    """

    element_id: str
    revision_id: str
    element_type: ElementType
    sequence: int
    parent_element_id: str | None = None
    section_path: str | None = None
    text: str | None = None
    payload: dict = field(default_factory=dict)
    content_hash: str = ""
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parser 输出值对象（不含持久化 ID，由应用层规范化）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParsedLocation:
    """Parser 输出的来源定位。"""

    page: int
    bbox: list[float] | None = None
    parser_ref: str | None = None
    char_range: list[int] | None = None


@dataclass(frozen=True, slots=True)
class ParsedElement:
    """Parser 输出的单个文档单元。

    ``parent_index`` 引用同一 ``ParsedDocument`` 中父元素的下标，
    应用层在分配 ID 时解析为 ``parent_element_id``。
    """

    element_type: ElementType
    sequence: int
    text: str | None = None
    payload: dict = field(default_factory=dict)
    section_path: str | None = None
    parent_index: int | None = None
    warnings: list[str] = field(default_factory=list)
    locations: list[ParsedLocation] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Parser 一次解析的完整输出。"""

    elements: list[ParsedElement]


def normalize_parsed_document(
    revision_id: str,
    parsed: ParsedDocument,
) -> tuple[list[DocumentElement], list[ElementSourceLocation]]:
    """把 Parser 输出规范化为可持久化的 Element 与来源定位。

    分配稳定 ID、解析父子引用、计算内容哈希。

    参数:
        revision_id: 目标 Parse Revision 标识符。
        parsed: Parser 输出。

    返回:
        ``(elements, locations)`` 元组，按阅读顺序排列。
    """
    elements: list[DocumentElement] = []
    locations: list[ElementSourceLocation] = []
    ids = [str(uuid4()) for _ in parsed.elements]

    for index, item in enumerate(parsed.elements):
        element_id = ids[index]
        parent_id = ids[item.parent_index] if item.parent_index is not None else None
        elements.append(
            DocumentElement(
                element_id=element_id,
                revision_id=revision_id,
                element_type=item.element_type,
                sequence=item.sequence,
                parent_element_id=parent_id,
                section_path=item.section_path,
                text=item.text,
                payload=item.payload,
                content_hash=compute_content_hash(item.element_type.value, item.text, item.payload),
                warnings=list(item.warnings),
            )
        )
        for loc in item.locations:
            locations.append(
                ElementSourceLocation(
                    location_id=str(uuid4()),
                    element_id=element_id,
                    page=loc.page,
                    bbox=loc.bbox,
                    parser_ref=loc.parser_ref,
                    char_range=loc.char_range,
                )
            )
    return elements, locations
