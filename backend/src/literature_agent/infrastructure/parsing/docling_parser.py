"""Docling 主 Parser 适配器。

使用 Docling 标准 PdfPipeline（默认不开 OCR）把 PDF 解析为
项目自己的 ``ParsedDocument`` 契约：Element 类型映射、章节路径、
阅读顺序和带页码/坐标的来源定位。

错误分类约定：
- Docling 抛出的输入/结构类异常 → ``InvalidPdfInputError``（可降级 pypdf）；
- 内存等资源错误 → ``ParserResourceError``（不降级）；
- 其他未知异常原样抛出（不降级，保守处理）；
- 超时不在这里实现，由执行器层统一施加。
"""

import asyncio
import io
import logging
from typing import Any

import docling
from docling.datamodel.base_models import ConversionStatus, DocumentStream, InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.exceptions import ConversionError
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.labels import DocItemLabel

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

PARSER_NAME = "docling"
PARSER_VERSION = docling.__version__

# Docling 标签到项目 ElementType 的直接映射
_LABEL_MAP: dict[DocItemLabel, ElementType] = {
    DocItemLabel.TITLE: ElementType.TITLE,
    DocItemLabel.SECTION_HEADER: ElementType.SECTION_HEADING,
    DocItemLabel.TEXT: ElementType.PARAGRAPH,
    DocItemLabel.LIST_ITEM: ElementType.LIST_ITEM,
    DocItemLabel.TABLE: ElementType.TABLE,
    DocItemLabel.PICTURE: ElementType.FIGURE,
    DocItemLabel.CAPTION: ElementType.CAPTION,
    DocItemLabel.FORMULA: ElementType.FORMULA,
    DocItemLabel.PAGE_HEADER: ElementType.PAGE_HEADER,
    DocItemLabel.PAGE_FOOTER: ElementType.PAGE_FOOTER,
}


class DoclingDocumentParser(DocumentParser):
    """基于 Docling 标准 PdfPipeline 的主 Parser。"""

    def __init__(self, storage: Storage) -> None:
        """初始化 Docling Parser。

        参数:
            storage: 受控文件存储，用于按 storage_key 读取 PDF 字节。
        """
        self._storage = storage

    async def parse(self, storage_key: str, profile: ParseProfile) -> ParsedDocument:
        """解析 PDF 并返回规范化文档。

        异常:
            InvalidPdfInputError: 损坏、加密或结构异常（可降级）。
            ParserResourceError: 内存等资源错误（不降级）。
        """
        content = await self._storage.read(storage_key)
        ocr_enabled = bool(profile.config.get("ocr_enabled", False))
        try:
            return await asyncio.to_thread(self._convert, content, ocr_enabled)
        except (InvalidPdfInputError, ParserResourceError):
            raise
        except MemoryError as exc:
            raise ParserResourceError("Docling 解析时内存不足") from exc
        except ConversionError as exc:
            raise InvalidPdfInputError(f"Docling 无法解析 PDF: {exc}") from exc

    def _convert(self, content: bytes, ocr_enabled: bool) -> ParsedDocument:
        """在线程中执行同步的 Docling 转换（CPU 密集）。"""
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = ocr_enabled
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            },
        )
        result = converter.convert(DocumentStream(name="paper.pdf", stream=io.BytesIO(content)))
        if result.status == ConversionStatus.FAILURE:
            raise InvalidPdfInputError("Docling 转换失败（文件损坏或结构异常）")
        document = result.document
        warnings: list[str] = []
        if result.status == ConversionStatus.PARTIAL_SUCCESS:
            warnings.append("partial_conversion")
        elements = _map_document(document, warnings)
        return ParsedDocument(elements=elements, warnings=warnings)


def _map_document(document: DoclingDocument, warnings: list[str]) -> list[ParsedElement]:
    """把 DoclingDocument 映射为 ParsedElement 列表。

    维护标题层级栈生成 ``section_path``；紧跟表格/图片的题注
    通过 ``parent_index`` 挂到该元素。映射不到的标签归为
    paragraph 并记录 ``unmapped_label:<label>`` 警告。
    """
    elements: list[ParsedElement] = []
    heading_levels: list[int] = []
    counters: list[int] = []
    current_path: str | None = None

    for item, level in document.iterate_items():
        label = getattr(item, "label", None)
        element_type = _LABEL_MAP.get(label) if isinstance(label, DocItemLabel) else None
        item_warnings: list[str] = []
        if element_type is None:
            element_type = ElementType.PARAGRAPH
            item_warnings.append(f"unmapped_label:{getattr(label, 'value', label)}")

        if element_type == ElementType.SECTION_HEADING:
            while heading_levels and heading_levels[-1] >= level:
                heading_levels.pop()
                counters.pop()
            if not counters:
                counters.append(0)
            counters[-1] += 1
            heading_levels.append(level)
            current_path = ".".join(str(n) for n in counters)

        text = getattr(item, "text", None) or None
        payload: dict[str, Any] = {}
        if element_type == ElementType.TABLE:
            payload = _table_payload(item)
        elif element_type == ElementType.FIGURE:
            # 首版不抽取图片，只保留占位 storage_key 并记录警告
            payload = {"storage_key": None}
            item_warnings.append("figure_not_extracted")
        elif element_type == ElementType.FORMULA:
            payload = {"latex": None}

        locations = _locations(document, item)
        if not locations and text is None and not payload:
            # 容器/组节点：无定位也无内容，不产生 Element
            continue

        # 紧跟表格/图片的题注挂为子元素
        parent_index: int | None = None
        if element_type == ElementType.CAPTION and elements:
            previous = elements[-1]
            if previous.element_type in {ElementType.TABLE, ElementType.FIGURE}:
                parent_index = len(elements) - 1

        elements.append(
            ParsedElement(
                element_type=element_type,
                sequence=len(elements) + 1,
                text=text,
                payload=payload,
                section_path=current_path,
                parent_index=parent_index,
                warnings=item_warnings,
                locations=locations,
            )
        )
    return elements


def _table_payload(item: Any) -> dict[str, Any]:
    """把 Docling TableItem 导出为纯文本网格 Payload。"""
    frame = item.export_to_dataframe()
    cells = [[str(cell) for cell in row] for row in frame.itertuples(index=False)]
    columns = [str(column) for column in frame.columns]
    grid = [columns, *cells] if columns else cells
    return {"rows": len(grid), "cols": len(grid[0]) if grid else 0, "cells": grid}


def _locations(document: DoclingDocument, item: Any) -> list[ParsedLocation]:
    """从 Provenance 提取页码与坐标；坐标统一转为左上角原点。

    无法可靠获得定位时返回空列表（例如组节点），不伪造精度。
    """
    locations: list[ParsedLocation] = []
    for prov in getattr(item, "prov", []) or []:
        bbox = getattr(prov, "bbox", None)
        bbox_list: list[float] | None = None
        if bbox is not None:
            page = document.pages.get(prov.page_no)
            if page is not None:
                bbox = bbox.to_top_left_origin(page_height=page.size.height)
            bbox_list = [float(bbox.l), float(bbox.t), float(bbox.r), float(bbox.b)]
        locations.append(
            ParsedLocation(
                page=prov.page_no,
                bbox=bbox_list,
                parser_ref=f"docling:{getattr(item, 'self_ref', '')}",
            )
        )
    return locations
