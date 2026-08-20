"""ChunkBuilder：把 Parse Revision 的 Element 序列组合成有序 Chunk 草稿。

纯函数风格，不依赖数据库与外部服务，可确定性测试。规则要点：

- 组合相邻文本类 Element，目标 ``max_tokens``；相邻 Chunk 按整
  Element 回带实现 ``overlap_tokens`` 重叠，不切半个 Element；
- 单个超过 ``max_tokens`` 的 Element（如大表格）允许独立成 Chunk
  超限存在，不硬切；
- caption 子 Element 与其 table/figure 父 Element 同 Chunk；
- 章节标题（section_heading）不单独成 Chunk，作为后续 Chunk 的
  上下文前缀（``include_section_prefix`` 时拼入 text 开头，
  前缀计入 token_count）；
- 页眉/页脚（page_header/page_footer）不进入 Chunk；
- text 为空的 Element（如未抽取的 figure）不成 Chunk；表格无 text
  时把 payload 单元格渲染为纯文本；
- page_start/page_end 取 Chunk 内 Element 来源定位的最小/最大页码；
- token 计数使用 tiktoken（默认 ``cl100k_base``）；content_hash 为
  text 的 SHA-256。
"""

import hashlib
from dataclasses import dataclass
from functools import lru_cache

import tiktoken

from literature_agent.domain.chunk_profile import ChunkProfile
from literature_agent.domain.document_element import (
    DocumentElement,
    ElementSourceLocation,
    ElementType,
)

# 不进入 Chunk 的 Element 类型
_EXCLUDED_TYPES = frozenset({ElementType.PAGE_HEADER, ElementType.PAGE_FOOTER})
# caption 允许并入的父 Element 类型
_CAPTION_PARENT_TYPES = frozenset({ElementType.TABLE, ElementType.FIGURE})

_UNIT_SEPARATOR = "\n\n"


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """ChunkBuilder 输出的 Chunk 草稿（未分配持久化 ID）。

    属性:
        sequence: Chunk 顺序，从 1 开始。
        text: 检索文本（可能含章节标题前缀）。
        token_count: 文本 token 数（含前缀）。
        section_path: 章节路径；无章节上下文时为 None。
        page_start/page_end: 来源页码范围；无定位时为 None。
        element_ids: 来源 Element ID 有序列表。
        content_hash: 文本的 SHA-256 哈希。
    """

    sequence: int
    text: str
    token_count: int
    section_path: str | None
    page_start: int | None
    page_end: int | None
    element_ids: list[str]
    content_hash: str


@dataclass(slots=True)
class _Unit:
    """一个原子切分单元（一个 Element，或 table/figure 与其 caption 的组合）。"""

    elements: list[DocumentElement]
    text: str
    tokens: int
    section_path: str | None


@dataclass(frozen=True, slots=True)
class _Heading:
    """章节标题标记：不进入 Chunk，只更新后续 Chunk 的上下文前缀。"""

    text: str
    section_path: str | None


@lru_cache(maxsize=4)
def _get_encoding(tokenizer: str) -> tiktoken.Encoding:
    """按名称加载 tiktoken 编码（进程内缓存）。"""
    return tiktoken.get_encoding(tokenizer)


def _count_tokens(encoding: tiktoken.Encoding, text: str) -> int:
    """计算文本 token 数。"""
    return len(encoding.encode(text))


def _element_text(element: DocumentElement) -> str | None:
    """返回 Element 的可切分文本；无文本内容时返回 None。

    表格无 ``text`` 时把 payload 中的单元格网格渲染为纯文本行，
    保证表格内容可被检索。
    """
    if element.text and element.text.strip():
        return element.text
    if element.element_type == ElementType.TABLE:
        cells = element.payload.get("cells")
        if cells:
            rows = [" | ".join(str(cell) for cell in row) for row in cells]
            rendered = "\n".join(rows)
            if rendered.strip():
                return rendered
    return None


def _build_units(
    elements: list[DocumentElement],
    encoding: tiktoken.Encoding,
) -> list[_Unit | _Heading]:
    """把 Element 序列整理为原子单元流（含章节标题标记）。

    caption 且其父为紧邻上一个单元内的 table/figure 时并入该单元，
    保证表格与题注同 Chunk；其余 caption 按普通文本单元处理。
    """
    stream: list[_Unit | _Heading] = []
    for element in sorted(elements, key=lambda e: e.sequence):
        if element.element_type in _EXCLUDED_TYPES:
            continue
        if element.element_type == ElementType.SECTION_HEADING:
            stream.append(
                _Heading(text=element.text or "", section_path=element.section_path)
            )
            continue
        text = _element_text(element)
        if text is None:
            continue
        if (
            element.element_type == ElementType.CAPTION
            and element.parent_element_id is not None
            and stream
            and isinstance(stream[-1], _Unit)
            and any(
                e.element_id == element.parent_element_id
                and e.element_type in _CAPTION_PARENT_TYPES
                for e in stream[-1].elements
            )
        ):
            unit = stream[-1]
            assert isinstance(unit, _Unit)
            unit.elements.append(element)
            unit.text = f"{unit.text}{_UNIT_SEPARATOR}{text}"
            unit.tokens = _count_tokens(encoding, unit.text)
            continue
        stream.append(
            _Unit(
                elements=[element],
                text=text,
                tokens=_count_tokens(encoding, text),
                section_path=element.section_path,
            )
        )
    return stream


def build_chunks(
    elements: list[DocumentElement],
    locations: list[ElementSourceLocation],
    profile: ChunkProfile,
) -> list[ChunkDraft]:
    """把一个 Parse Revision 的 Element 组合为有序 Chunk 草稿。

    参数:
        elements: 该 Revision 的全部 Element（任意顺序，内部按
            ``sequence`` 排序）。
        locations: 这些 Element 的来源定位。
        profile: 切分配置。

    返回:
        有序 Chunk 草稿列表；无可用内容时返回空列表（空文档合法）。
    """
    encoding = _get_encoding(profile.tokenizer)
    stream = _build_units(elements, encoding)
    pages_by_element: dict[str, list[int]] = {}
    for loc in locations:
        pages_by_element.setdefault(loc.element_id, []).append(loc.page)

    drafts: list[ChunkDraft] = []
    buffer: list[_Unit] = []
    # buffer 中非重叠回带引入的单元数；为 0 时禁止关闭 Chunk，保证进度
    new_units_in_buffer = 0
    heading: _Heading | None = None

    def _close_chunk() -> None:
        """把当前 buffer 输出为一个 Chunk 草稿。"""
        body = _UNIT_SEPARATOR.join(u.text for u in buffer)
        if profile.include_section_prefix and heading is not None and heading.text:
            text = f"{heading.text}{_UNIT_SEPARATOR}{body}"
        else:
            text = body
        pages = [
            page
            for unit in buffer
            for element in unit.elements
            for page in pages_by_element.get(element.element_id, [])
        ]
        drafts.append(
            ChunkDraft(
                sequence=len(drafts) + 1,
                text=text,
                token_count=_count_tokens(encoding, text),
                section_path=buffer[0].section_path,
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                element_ids=[e.element_id for u in buffer for e in u.elements],
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
        )

    def _overlap_units() -> list[_Unit]:
        """从 buffer 末尾整单元回带，总 token 不超过 overlap 上限。"""
        carried: list[_Unit] = []
        carried_tokens = 0
        for unit in reversed(buffer):
            if carried and carried_tokens + unit.tokens > profile.overlap_tokens:
                break
            if not carried and unit.tokens > profile.overlap_tokens:
                break
            carried.append(unit)
            carried_tokens += unit.tokens
        carried.reverse()
        return carried

    prefix_tokens = 0
    for item in stream:
        if isinstance(item, _Heading):
            # 章节边界是天然的 Chunk 边界
            if buffer:
                _close_chunk()
                buffer = []
                new_units_in_buffer = 0
            heading = item
            prefix_tokens = (
                _count_tokens(encoding, item.text)
                if profile.include_section_prefix and item.text
                else 0
            )
            continue
        buffer_tokens = sum(u.tokens for u in buffer)
        if (
            buffer
            and new_units_in_buffer > 0
            and prefix_tokens + buffer_tokens + item.tokens > profile.max_tokens
        ):
            _close_chunk()
            buffer = _overlap_units()
            new_units_in_buffer = 0
        buffer.append(item)
        new_units_in_buffer += 1
    if buffer:
        _close_chunk()
    return drafts
