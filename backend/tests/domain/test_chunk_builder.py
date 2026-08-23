"""ChunkBuilder 领域测试（确定性，纯函数）。"""

import hashlib
from uuid import uuid4

from literature_agent.domain.chunk_builder import build_chunks
from literature_agent.domain.chunk_profile import ChunkProfile
from literature_agent.domain.document_element import (
    DocumentElement,
    ElementSourceLocation,
    ElementType,
)
from literature_agent.domain.tokenization import OFFLINE_TOKENIZER, count_tokens


def _profile(**kwargs) -> ChunkProfile:
    return ChunkProfile(tokenizer=OFFLINE_TOKENIZER, **kwargs)


def _tokens(text: str) -> int:
    """用离线确定性编码计算 token 数。"""
    return count_tokens(OFFLINE_TOKENIZER, text)


def _words(n: int) -> str:
    """生成恰好约 n 个 token 的英文文本（每词 1 token）。"""
    return " ".join(["word"] * n)


def _element(
    sequence: int,
    element_type: ElementType = ElementType.PARAGRAPH,
    text: str | None = None,
    section_path: str | None = None,
    parent: DocumentElement | None = None,
    payload: dict | None = None,
) -> DocumentElement:
    """构造测试 Element。"""
    return DocumentElement(
        element_id=str(uuid4()),
        revision_id="rev-1",
        element_type=element_type,
        sequence=sequence,
        parent_element_id=parent.element_id if parent else None,
        section_path=section_path,
        text=text,
        payload=payload or {},
        content_hash="",
    )


def _loc(element: DocumentElement, page: int) -> ElementSourceLocation:
    """构造测试来源定位。"""
    return ElementSourceLocation(
        location_id=str(uuid4()), element_id=element.element_id, page=page
    )


def test_groups_adjacent_elements_within_max_tokens() -> None:
    """相邻文本 Element 按 max_tokens 分组；token_count 与最终文本精确一致。"""
    profile = _profile(max_tokens=10, overlap_tokens=0, include_section_prefix=False)
    elements = [_element(i, text=_words(4)) for i in range(1, 5)]

    drafts = build_chunks(elements, [], profile)

    assert len(drafts) == 2
    assert drafts[0].element_ids == [elements[0].element_id, elements[1].element_id]
    assert drafts[1].element_ids == [elements[2].element_id, elements[3].element_id]
    assert [d.sequence for d in drafts] == [1, 2]
    for draft in drafts:
        assert draft.token_count == _tokens(draft.text)
        assert draft.content_hash == hashlib.sha256(draft.text.encode("utf-8")).hexdigest()


def test_overlap_carries_whole_elements() -> None:
    """相邻 Chunk 按整 Element 回带重叠，不切半个 Element。"""
    profile = _profile(max_tokens=10, overlap_tokens=4, include_section_prefix=False)
    elements = [_element(i, text=_words(4)) for i in range(1, 5)]

    drafts = build_chunks(elements, [], profile)

    assert len(drafts) == 3
    ids = [list(d.element_ids) for d in drafts]
    assert ids[0] == [elements[0].element_id, elements[1].element_id]
    assert ids[1] == [elements[1].element_id, elements[2].element_id]
    assert ids[2] == [elements[2].element_id, elements[3].element_id]
    # 回带的 Element 文本出现在下一个 Chunk 开头
    assert drafts[1].text.startswith(elements[1].text or "")


def test_oversized_single_element_becomes_own_chunk() -> None:
    """单个超过 max_tokens 的 Element 独立成 Chunk 超限存在，不硬切。"""
    profile = _profile(max_tokens=10, overlap_tokens=0, include_section_prefix=False)
    big = _element(2, text=_words(25))
    elements = [_element(1, text=_words(4)), big, _element(3, text=_words(4))]

    drafts = build_chunks(elements, [], profile)

    assert len(drafts) == 3
    assert drafts[1].element_ids == [big.element_id]
    assert drafts[1].token_count > profile.max_tokens


def test_table_and_caption_stay_in_same_chunk() -> None:
    """caption 子 Element 与 table 父 Element 同 Chunk；表格渲染 payload 文本。"""
    profile = _profile(max_tokens=512, overlap_tokens=64, include_section_prefix=False)
    table = _element(
        1,
        element_type=ElementType.TABLE,
        text=None,
        payload={"rows": 2, "cols": 2, "cells": [["metric", "value"], ["accuracy", "0.90"]]},
    )
    caption = _element(2, element_type=ElementType.CAPTION, text="Table 1: results", parent=table)
    paragraph = _element(3, text=_words(4))

    drafts = build_chunks([table, caption, paragraph], [], profile)

    table_chunk = [d for d in drafts if table.element_id in d.element_ids]
    assert len(table_chunk) == 1
    assert caption.element_id in table_chunk[0].element_ids
    assert "accuracy | 0.90" in table_chunk[0].text
    assert "Table 1: results" in table_chunk[0].text


def test_section_heading_becomes_prefix_not_chunk() -> None:
    """章节标题不单独成 Chunk，include_section_prefix 时拼入后续 Chunk 开头。"""
    profile = _profile(max_tokens=512, overlap_tokens=64)
    heading = _element(1, element_type=ElementType.SECTION_HEADING, text="1 Introduction",
                       section_path="1")
    paragraph = _element(2, text=_words(6), section_path="1")

    drafts = build_chunks([heading, paragraph], [], profile)

    assert len(drafts) == 1
    assert heading.element_id not in drafts[0].element_ids
    assert drafts[0].text.startswith("1 Introduction\n\n")
    assert drafts[0].section_path == "1"
    # 前缀计入 token_count
    assert drafts[0].token_count == _tokens(drafts[0].text)
    assert drafts[0].token_count > _tokens(paragraph.text or "")


def test_section_prefix_disabled() -> None:
    """include_section_prefix=False 时章节标题不拼入文本。"""
    profile = _profile(max_tokens=512, overlap_tokens=64, include_section_prefix=False)
    heading = _element(1, element_type=ElementType.SECTION_HEADING, text="1 Introduction",
                       section_path="1")
    paragraph = _element(2, text=_words(6), section_path="1")

    drafts = build_chunks([heading, paragraph], [], profile)

    assert len(drafts) == 1
    assert drafts[0].text == paragraph.text


def test_new_section_starts_new_chunk() -> None:
    """章节边界是天然 Chunk 边界，不跨章节组合。"""
    profile = _profile(max_tokens=512, overlap_tokens=64)
    elements = [
        _element(1, element_type=ElementType.SECTION_HEADING, text="1 A", section_path="1"),
        _element(2, text=_words(4), section_path="1"),
        _element(3, element_type=ElementType.SECTION_HEADING, text="2 B", section_path="2"),
        _element(4, text=_words(4), section_path="2"),
    ]

    drafts = build_chunks(elements, [], profile)

    assert len(drafts) == 2
    assert drafts[0].text.startswith("1 A")
    assert drafts[1].text.startswith("2 B")
    assert drafts[1].section_path == "2"


def test_headers_footers_and_empty_elements_excluded() -> None:
    """页眉/页脚与空文本 Element（如未抽取的 figure）不进入 Chunk。"""
    profile = _profile(max_tokens=512, overlap_tokens=64, include_section_prefix=False)
    header = _element(1, element_type=ElementType.PAGE_HEADER, text="Journal Header")
    footer = _element(2, element_type=ElementType.PAGE_FOOTER, text="Page 1")
    figure = _element(3, element_type=ElementType.FIGURE, text=None)
    blank = _element(4, text="   ")
    paragraph = _element(5, text=_words(4))

    drafts = build_chunks([header, footer, figure, blank, paragraph], [], profile)

    assert len(drafts) == 1
    excluded = {header.element_id, footer.element_id, figure.element_id, blank.element_id}
    assert excluded.isdisjoint(drafts[0].element_ids)
    assert "Journal Header" not in drafts[0].text
    assert "Page 1" not in drafts[0].text


def test_page_range_from_locations() -> None:
    """page_start/page_end 取 Chunk 内 Element 定位的最小/最大页码；无定位为 None。"""
    profile = _profile(max_tokens=512, overlap_tokens=64, include_section_prefix=False)
    spanning = _element(1, text=_words(4))
    no_location = _element(2, text=_words(4))
    locations = [_loc(spanning, 2), _loc(spanning, 3)]

    drafts = build_chunks([spanning], locations, profile)
    assert drafts[0].page_start == 2
    assert drafts[0].page_end == 3

    drafts = build_chunks([no_location], [], profile)
    assert drafts[0].page_start is None
    assert drafts[0].page_end is None


def test_empty_document_produces_no_chunks() -> None:
    """空文档（零 Element 或全部无可切分内容）产生空 Chunk 列表，属合法结果。"""
    profile = _profile()
    assert build_chunks([], [], profile) == []
    only_heading = [
        _element(1, element_type=ElementType.SECTION_HEADING, text="1 A", section_path="1")
    ]
    assert build_chunks(only_heading, [], profile) == []


def test_offline_tokenizer_never_loads_tiktoken(monkeypatch) -> None:
    """Fake profile 在空缓存机器也不能触发 tiktoken 资源加载。"""
    import tiktoken

    monkeypatch.setattr(
        tiktoken,
        "get_encoding",
        lambda _name: (_ for _ in ()).throw(AssertionError("禁止加载 tiktoken")),
    )
    paragraph = _element(1, text="离线 fake review evidence")

    drafts = build_chunks([paragraph], [], _profile())

    assert drafts[0].token_count == 4


def test_real_tokenizer_still_delegates_to_tiktoken(monkeypatch) -> None:
    """非 Fake profile 仍按名称加载真实 tiktoken，不静默降级。"""
    import tiktoken

    requested: list[str] = []

    class Encoding:
        @staticmethod
        def encode(text: str) -> list[int]:
            return list(range(len(text)))

    def get_encoding(name: str) -> Encoding:
        requested.append(name)
        return Encoding()

    monkeypatch.setattr(tiktoken, "get_encoding", get_encoding)
    paragraph = _element(1, text="real")

    drafts = build_chunks(
        [paragraph],
        [],
        ChunkProfile(tokenizer="test-real-tokenizer"),
    )

    assert requested == ["test-real-tokenizer"]
    assert drafts[0].token_count == 4
