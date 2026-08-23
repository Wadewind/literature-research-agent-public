"""完全离线、版本化的 Review Demo arXiv Adapter。"""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from literature_agent.domain.arxiv import (
    ArxivError,
    ArxivPaper,
    ArxivSearchQuery,
    DownloadedPdf,
)

_FIXTURE_ROOT = Path(__file__).with_name("fixtures") / "review" / "v1"


class FixtureArxivGateway:
    """从仓库内合成论文读取稳定结果，不创建 HTTP 客户端。"""

    def __init__(self, fixture_root: Path = _FIXTURE_ROOT) -> None:
        self._fixture_root = fixture_root
        manifest = json.loads((fixture_root / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("schema_version") != "review-demo.v1":
            raise ValueError("fake_arxiv_fixture_version_invalid")
        try:
            items = manifest["papers"]
            if not isinstance(items, list) or not items:
                raise TypeError
            self._entries = tuple(self._parse_entry(item) for item in items)
            self._downloads = {
                paper.pdf_url: self._load_download(item)
                for paper, item in self._entries
            }
        except (KeyError, TypeError, ValueError) as exc:
            if str(exc).startswith("fake_arxiv_fixture_"):
                raise
            raise ValueError("fake_arxiv_fixture_manifest_invalid") from exc

    async def search(self, query: ArxivSearchQuery) -> list[ArxivPaper]:
        """按 manifest 顺序返回受查询分页预算限制的稳定论文。"""
        papers = [paper for paper, _item in self._entries]
        return papers[query.start : query.start + query.max_results]

    async def download_pdf(
        self, url: str, *, remaining_budget_bytes: int
    ) -> DownloadedPdf:
        """读取本地 PDF 或产生 manifest 声明的稳定单篇失败。"""
        result = self._downloads.get(url)
        if result is None:
            raise ArxivError("fake_arxiv_pdf_not_found", temporary=False)
        if isinstance(result, str):
            raise ArxivError(result, temporary=False)
        if len(result.content) > remaining_budget_bytes:
            raise ArxivError("arxiv_total_download_budget_exceeded", temporary=False)
        return result

    def _load_download(self, item: dict[str, Any]) -> DownloadedPdf | str:
        """校验并缓存单篇 Fixture，启动时即暴露漂移或缺失。"""
        failure_code = item.get("download_failure_code")
        if failure_code is not None:
            return str(failure_code)
        relative_path = Path(item["pdf_file"])
        expected_size = item["pdf_size"]
        expected_hash = item["pdf_sha256"]
        if (
            relative_path.is_absolute()
            or ".." in relative_path.parts
            or not isinstance(expected_size, int)
            or expected_size <= 0
            or not isinstance(expected_hash, str)
            or len(expected_hash) != 64
        ):
            raise ValueError("fake_arxiv_fixture_manifest_invalid")
        path = self._fixture_root / relative_path
        if not path.is_file():
            raise ValueError("fake_arxiv_fixture_pdf_missing")
        content = path.read_bytes()
        if len(content) != expected_size:
            raise ValueError("fake_arxiv_fixture_pdf_size_mismatch")
        if hashlib.sha256(content).hexdigest() != expected_hash:
            raise ValueError("fake_arxiv_fixture_pdf_hash_mismatch")
        try:
            return DownloadedPdf.from_content(content, "application/pdf")
        except ValueError as exc:
            raise ValueError("fake_arxiv_fixture_pdf_invalid") from exc

    @staticmethod
    def _parse_entry(item: dict[str, Any]) -> tuple[ArxivPaper, dict[str, Any]]:
        arxiv_id = str(item["arxiv_id"])
        version = str(item["arxiv_version"])
        paper = ArxivPaper(
            arxiv_id=arxiv_id,
            arxiv_version=version,
            title=str(item["title"]),
            abstract=str(item["abstract"]),
            authors=tuple(str(author) for author in item["authors"]),
            categories=tuple(str(category) for category in item["categories"]),
            published_at=datetime.fromisoformat(str(item["published_at"])),
            updated_at=datetime.fromisoformat(str(item["updated_at"])),
            pdf_url=f"fixture://arxiv/{arxiv_id}{version}.pdf",
        )
        return paper, item
