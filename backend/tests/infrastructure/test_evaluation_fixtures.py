"""评测 Fixture 与 manifest 的一致性校验。

确定性测试：只用 pypdf 解析合成 PDF，不调用 Docling、不调用任何模型。
保证 ``tests/evaluation/corpus/`` 的 PDF 与 ``tests/evaluation/manifest.json``
声明的页数、植入事实页码/章节不漂移，且问题集结构自洽。
"""

import json
from pathlib import Path

import pytest
from pypdf import PdfReader

EVAL_DIR = Path(__file__).parent.parent / "evaluation"
MANIFEST_PATH = EVAL_DIR / "manifest.json"

VALID_CATEGORIES = {
    "single_paper_fact",
    "cross_paper_synthesis",
    "unanswerable",
    "scope_boundary",
}
VALID_SCOPE_MODES = {"project", "selected_papers"}
TOTAL_QUESTIONS_RANGE = (12, 15)


@pytest.fixture(scope="module")
def manifest() -> dict:
    """加载评测 manifest。"""
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def corpus_texts(manifest: dict) -> dict[str, list[str]]:
    """解析全部语料 PDF，返回 {语料 ID: [每页文本]}（1 起页码对应索引 0）。"""
    texts: dict[str, list[str]] = {}
    for paper_id, entry in manifest["corpus"].items():
        path = EVAL_DIR / entry["file"]
        assert path.is_file(), f"语料 PDF 不存在: {path}"
        reader = PdfReader(path)
        texts[paper_id] = [page.extract_text() or "" for page in reader.pages]
    return texts


def test_corpus_page_counts(manifest: dict, corpus_texts: dict[str, list[str]]) -> None:
    """每篇 PDF 的实际页数与 manifest 声明一致，且在 3-5 页范围内。"""
    for paper_id, entry in manifest["corpus"].items():
        actual = len(corpus_texts[paper_id])
        declared = entry["page_count"]
        assert actual == declared, f"{paper_id}: 实际 {actual} 页，声明 {declared} 页"
        assert 3 <= actual <= 5, f"{paper_id}: 页数 {actual} 超出 3-5 页要求"


def test_planted_facts_on_declared_pages(
    manifest: dict, corpus_texts: dict[str, list[str]]
) -> None:
    """每个植入事实的关键词和章节标题必须出现在 manifest 声明的页码上。"""
    for paper_id, entry in manifest["corpus"].items():
        pages = corpus_texts[paper_id]
        for fact in entry["planted_facts"]:
            page_no = fact["page"]
            assert 1 <= page_no <= len(pages), f"{paper_id}: 植入事实页码越界: {page_no}"
            text = pages[page_no - 1]
            assert fact["keyword"] in text, (
                f"{paper_id} 第 {page_no} 页缺少植入关键词 {fact['keyword']!r}"
            )
            assert fact["section"] in text, (
                f"{paper_id} 第 {page_no} 页缺少章节标题 {fact['section']!r}"
            )


def test_questions_structure(manifest: dict) -> None:
    """问题集结构自洽：ID 唯一、分类合法、scope 与 expected 字段一致。"""
    questions = manifest["questions"]
    assert TOTAL_QUESTIONS_RANGE[0] <= len(questions) <= TOTAL_QUESTIONS_RANGE[1]

    ids = [q["id"] for q in questions]
    assert len(ids) == len(set(ids)), "问题 id 重复"

    corpus = manifest["corpus"]
    for q in questions:
        assert q["category"] in VALID_CATEGORIES, f"{q['id']}: 未知分类 {q['category']}"
        assert q["question"].strip(), f"{q['id']}: 问题为空"

        scope = q["scope"]
        assert scope["mode"] in VALID_SCOPE_MODES, f"{q['id']}: 未知 scope mode"
        if scope["mode"] == "selected_papers":
            papers = scope.get("papers") or []
            assert papers, f"{q['id']}: selected_papers 模式必须给出非空 papers"
            for paper_id in papers:
                assert paper_id in corpus, f"{q['id']}: scope 引用未知语料 ID {paper_id}"

        expected = q["expected"]
        status = expected["answer_status"]
        assert status in {"answered", "insufficient_evidence"}, f"{q['id']}: 未知 answer_status"
        if status == "answered":
            must_cite = expected.get("must_cite") or []
            assert must_cite, f"{q['id']}: answered 问题必须声明 must_cite"
            for cite in must_cite:
                assert cite["paper"] in corpus, f"{q['id']}: must_cite 引用未知语料 ID"
                page_count = corpus[cite["paper"]]["page_count"]
                assert cite["pages"], f"{q['id']}: must_cite 必须给出页码"
                for page in cite["pages"]:
                    assert 1 <= page <= page_count, f"{q['id']}: must_cite 页码 {page} 越界"
                assert cite["sections"], f"{q['id']}: must_cite 必须给出章节"
        else:
            assert "must_cite" not in expected, f"{q['id']}: 无答案问题不应声明 must_cite"


def test_question_category_coverage(manifest: dict) -> None:
    """四类问题每类至少 3 题，跨篇综合题的 must_cite 必须覆盖两篇不同 paper。"""
    by_category: dict[str, list[dict]] = {}
    for q in manifest["questions"]:
        by_category.setdefault(q["category"], []).append(q)
    for category in VALID_CATEGORIES:
        assert len(by_category.get(category, [])) >= 3, f"分类 {category} 不足 3 题"

    for q in by_category["cross_paper_synthesis"]:
        papers = {cite["paper"] for cite in q["expected"]["must_cite"]}
        assert len(papers) == 2, f"{q['id']}: 跨篇综合题必须恰好引用两篇不同 paper"

    for q in by_category["scope_boundary"]:
        assert q["scope"]["mode"] == "selected_papers", (
            f"{q['id']}: 范围边界题必须使用 selected_papers scope"
        )
