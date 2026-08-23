"""版本化 Review Demo Fixture 与离线 arXiv Adapter 契约。"""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from literature_agent.domain.arxiv import ArxivError, ArxivSearchQuery
from literature_agent.infrastructure import fake_arxiv
from literature_agent.infrastructure.fake_arxiv import FixtureArxivGateway


async def test_fixture_search_is_deterministic_versioned_and_budgeted() -> None:
    gateway = FixtureArxivGateway()
    query = ArxivSearchQuery(expression="all:agent", max_results=3)

    first = await gateway.search(query)
    second = await gateway.search(query)

    assert first == second
    assert len(first) == 3
    assert [paper.versioned_id for paper in first] == [
        "2608.00001v1",
        "2608.00002v1",
        "2608.00003v1",
    ]


async def test_fixture_download_returns_stable_pdf_bytes_without_network() -> None:
    gateway = FixtureArxivGateway()
    paper = (await gateway.search(ArxivSearchQuery("all:agent")))[0]

    first = await gateway.download_pdf(paper.pdf_url, remaining_budget_bytes=100_000)
    second = await gateway.download_pdf(paper.pdf_url, remaining_budget_bytes=100_000)

    assert first == second
    assert first.content.startswith(b"%PDF-")
    assert first.content_hash == hashlib.sha256(first.content).hexdigest()


async def test_fixture_contains_stable_partial_source_failure() -> None:
    gateway = FixtureArxivGateway()
    papers = await gateway.search(ArxivSearchQuery("all:agent"))
    failed = next(paper for paper in papers if paper.arxiv_id == "2608.00004")

    with pytest.raises(ArxivError) as first:
        await gateway.download_pdf(failed.pdf_url, remaining_budget_bytes=100_000)
    with pytest.raises(ArxivError) as replay:
        await gateway.download_pdf(failed.pdf_url, remaining_budget_bytes=100_000)

    assert (first.value.code, first.value.temporary) == (
        "fake_arxiv_pdf_unavailable",
        False,
    )
    assert (replay.value.code, replay.value.temporary) == (
        first.value.code,
        first.value.temporary,
    )
    assert replay.value is not first.value


async def test_fixture_download_enforces_remaining_budget() -> None:
    gateway = FixtureArxivGateway()
    paper = (await gateway.search(ArxivSearchQuery("all:agent")))[0]

    with pytest.raises(ArxivError) as captured:
        await gateway.download_pdf(paper.pdf_url, remaining_budget_bytes=1)

    assert captured.value.code == "arxiv_total_download_budget_exceeded"
    assert captured.value.temporary is False


async def test_fixture_rejects_unknown_internal_url() -> None:
    gateway = FixtureArxivGateway()

    with pytest.raises(ArxivError) as captured:
        await gateway.download_pdf(
            "fixture://arxiv/unknown.pdf", remaining_budget_bytes=100_000
        )

    assert captured.value.code == "fake_arxiv_pdf_not_found"
    assert captured.value.temporary is False


def _copied_fixture(tmp_path: Path) -> Path:
    source = Path(fake_arxiv.__file__).with_name("fixtures") / "review" / "v1"
    target = tmp_path / "review-demo-v1"
    shutil.copytree(source, target)
    return target


def test_fixture_fails_fast_when_pdf_is_tampered(tmp_path: Path) -> None:
    root = _copied_fixture(tmp_path)
    pdf = root / "papers" / "2608.00001v1.pdf"
    pdf.write_bytes(pdf.read_bytes() + b"tampered")

    with pytest.raises(ValueError, match="fake_arxiv_fixture_pdf_size_mismatch"):
        FixtureArxivGateway(root)


def test_fixture_fails_fast_when_pdf_hash_changes_at_same_size(tmp_path: Path) -> None:
    root = _copied_fixture(tmp_path)
    pdf = root / "papers" / "2608.00001v1.pdf"
    content = bytearray(pdf.read_bytes())
    content[-2] = ord("X")
    pdf.write_bytes(content)

    with pytest.raises(ValueError, match="fake_arxiv_fixture_pdf_hash_mismatch"):
        FixtureArxivGateway(root)


def test_fixture_fails_fast_when_pdf_is_missing(tmp_path: Path) -> None:
    root = _copied_fixture(tmp_path)
    (root / "papers" / "2608.00001v1.pdf").unlink()

    with pytest.raises(ValueError, match="fake_arxiv_fixture_pdf_missing"):
        FixtureArxivGateway(root)


def test_fixture_fails_fast_when_manifest_file_contract_is_invalid(tmp_path: Path) -> None:
    root = _copied_fixture(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["papers"][0]["pdf_sha256"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="fake_arxiv_fixture_manifest_invalid"):
        FixtureArxivGateway(root)
