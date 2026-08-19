"""解析领域模型测试：ParseProfile、ParseRevision、DocumentElement。"""

from datetime import UTC, datetime

from literature_agent.domain.document_element import (
    ElementType,
    ParsedDocument,
    ParsedElement,
    ParsedLocation,
    compute_content_hash,
    detect_document_warnings,
    normalize_parsed_document,
)
from literature_agent.domain.parse_profile import ParseProfile, compute_profile_hash
from literature_agent.domain.parse_revision import (
    ParseRevisionStatus,
    create_parse_revision,
)


def test_profile_hash_is_deterministic() -> None:
    """相同语义的配置应得到相同哈希（键序无关）。"""
    h1 = compute_profile_hash("fake", "1.0", {"ocr": False, "dpi": 144})
    h2 = compute_profile_hash("fake", "1.0", {"dpi": 144, "ocr": False})
    assert h1 == h2
    assert len(h1) == 64


def test_profile_hash_changes_with_input() -> None:
    """名称、版本或配置变化应改变哈希。"""
    base = ParseProfile("fake", "1.0", {})
    assert base.profile_hash != ParseProfile("fake", "2.0", {}).profile_hash
    assert base.profile_hash != ParseProfile("docling", "1.0", {}).profile_hash
    assert base.profile_hash != ParseProfile("fake", "1.0", {"ocr": True}).profile_hash


def test_parse_revision_lifecycle() -> None:
    """Revision 应从 RUNNING 推进到 SUCCEEDED/FAILED。"""
    revision = create_parse_revision("v-1", "fake", "1.0", "hash")
    assert revision.status == ParseRevisionStatus.RUNNING
    assert revision.completed_at is None

    now = datetime.now(UTC)
    done = revision.mark_succeeded(now)
    assert done.status == ParseRevisionStatus.SUCCEEDED
    assert done.completed_at == now

    failed = revision.mark_failed({"type": "ValueError", "message": "损坏"}, now)
    assert failed.status == ParseRevisionStatus.FAILED
    assert failed.error == {"type": "ValueError", "message": "损坏"}


def test_content_hash_is_deterministic() -> None:
    """相同内容应得到相同内容哈希。"""
    h1 = compute_content_hash("paragraph", "你好", {"a": 1})
    h2 = compute_content_hash("paragraph", "你好", {"a": 1})
    assert h1 == h2
    assert h1 != compute_content_hash("paragraph", "你好2", {"a": 1})


def test_normalize_parsed_document_assigns_ids_and_order() -> None:
    """规范化应分配 ID、解析父子引用并生成来源定位。"""
    parsed = ParsedDocument(
        elements=[
            ParsedElement(
                element_type=ElementType.SECTION_HEADING,
                sequence=1,
                text="1 引言",
                section_path="1",
                locations=[ParsedLocation(page=1, parser_ref="fake:p1:e1")],
            ),
            ParsedElement(
                element_type=ElementType.PARAGRAPH,
                sequence=2,
                text="段落一",
                section_path="1",
                parent_index=0,
                locations=[
                    ParsedLocation(page=1, bbox=[10.0, 20.0, 30.0, 40.0]),
                    ParsedLocation(page=2, parser_ref="fake:p2:e1"),
                ],
            ),
        ]
    )

    elements, locations = normalize_parsed_document("rev-1", parsed)

    assert len(elements) == 2
    assert [e.sequence for e in elements] == [1, 2]
    assert elements[0].revision_id == "rev-1"
    assert elements[0].parent_element_id is None
    assert elements[1].parent_element_id == elements[0].element_id
    assert all(len(e.content_hash) == 64 for e in elements)

    assert len(locations) == 3
    paragraph_locations = [loc for loc in locations if loc.element_id == elements[1].element_id]
    assert [loc.page for loc in paragraph_locations] == [1, 2]
    assert paragraph_locations[0].bbox == [10.0, 20.0, 30.0, 40.0]


def test_mark_succeeded_carries_degraded_and_warnings() -> None:
    """mark_succeeded 应记录降级标记与文档级警告。"""
    revision = create_parse_revision("v-1", "pypdf", "6", "h")
    succeeded = revision.mark_succeeded(
        datetime.now(UTC), degraded=True, warnings=["layout_missing"]
    )

    assert succeeded.status == ParseRevisionStatus.SUCCEEDED
    assert succeeded.degraded is True
    assert succeeded.warnings == ["layout_missing"]
    # 默认路径保持非降级
    plain = revision.mark_succeeded(datetime.now(UTC))
    assert plain.degraded is False
    assert plain.warnings == []


def test_detect_document_warnings_flags_empty_text() -> None:
    """全文文本长度为 0 时应给出 possibly_scanned 警告。"""
    empty = ParsedDocument(
        elements=[ParsedElement(element_type=ElementType.PARAGRAPH, sequence=1, text=None)]
    )
    assert detect_document_warnings(empty) == ["possibly_scanned"]

    with_text = ParsedDocument(
        elements=[ParsedElement(element_type=ElementType.PARAGRAPH, sequence=1, text="内容")]
    )
    assert detect_document_warnings(with_text) == []
