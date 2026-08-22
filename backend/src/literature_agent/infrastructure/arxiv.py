"""基于 httpx2 的官方 arXiv API/PDF Adapter。"""

from datetime import datetime
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import httpx2

from literature_agent.application.ports.arxiv_gateway import ArxivGateway
from literature_agent.domain.arxiv import (
    ArxivError,
    ArxivPaper,
    ArxivSearchQuery,
    DownloadedPdf,
    parse_versioned_arxiv_id,
)

_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"
_OFFICIAL_PDF_HOSTS = frozenset({"arxiv.org", "export.arxiv.org"})


class HttpxArxivGateway(ArxivGateway):
    """只访问配置中官方 Host 的 arXiv Adapter。"""

    def __init__(
        self,
        *,
        client: httpx2.AsyncClient | None = None,
        api_url: str = "https://export.arxiv.org/api/query",
        allowed_pdf_hosts: frozenset[str] = frozenset({"arxiv.org", "export.arxiv.org"}),
        max_file_bytes: int = 50 * 1024 * 1024,
        max_feed_bytes: int = 2 * 1024 * 1024,
        max_redirects: int = 3,
        max_attempts: int = 3,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._client = client or httpx2.AsyncClient(
            timeout=httpx2.Timeout(timeout_seconds), trust_env=False, follow_redirects=False
        )
        self._api_url = api_url
        api = urlsplit(api_url)
        if api.scheme != "https" or api.hostname != "export.arxiv.org":
            raise ValueError("arxiv_api_host_not_allowed")
        if not allowed_pdf_hosts or not allowed_pdf_hosts <= _OFFICIAL_PDF_HOSTS:
            raise ValueError("arxiv_pdf_hosts_invalid")
        self._allowed_pdf_hosts = allowed_pdf_hosts
        if max_file_bytes < 1:
            raise ValueError("arxiv_max_file_bytes_invalid")
        self._max_file_bytes = max_file_bytes
        if max_feed_bytes < 1:
            raise ValueError("arxiv_max_feed_bytes_invalid")
        self._max_feed_bytes = max_feed_bytes
        if max_redirects < 0:
            raise ValueError("arxiv_max_redirects_invalid")
        self._max_redirects = max_redirects
        if max_attempts < 1:
            raise ValueError("arxiv_max_attempts_invalid")
        self._max_attempts = max_attempts

    async def aclose(self) -> None:
        """关闭底层 HTTP 客户端。"""
        await self._client.aclose()

    async def search(self, query: ArxivSearchQuery) -> list[ArxivPaper]:
        feed_content = None
        params = {
            "search_query": query.expression,
            "start": query.start,
            "max_results": query.max_results,
            "sortBy": query.sort_by.value,
            "sortOrder": query.sort_order.value,
        }
        for attempt in range(self._max_attempts):
            try:
                feed_content = await self._fetch_feed(params)
                break
            except httpx2.TimeoutException as exc:
                error = ArxivError("arxiv_search_timeout", temporary=True)
                if attempt + 1 == self._max_attempts:
                    raise error from exc
            except httpx2.TransportError as exc:
                error = ArxivError("arxiv_search_transport_error", temporary=True)
                if attempt + 1 == self._max_attempts:
                    raise error from exc
            except ArxivError as exc:
                if not exc.temporary or attempt + 1 == self._max_attempts:
                    raise
        if feed_content is None:
            raise AssertionError("unreachable")
        try:
            root = ElementTree.fromstring(feed_content)
        except ElementTree.ParseError as exc:
            raise ArxivError("arxiv_search_feed_invalid", temporary=False) from exc

        found: list[ArxivPaper] = []
        seen: set[tuple[str, str]] = set()
        for entry in root.findall(f"{_ATOM}entry"):
            paper = self._parse_entry(entry)
            key = (paper.arxiv_id, paper.arxiv_version)
            if key in seen:
                continue
            seen.add(key)
            found.append(paper)
            if len(found) == query.max_results:
                break
        return found

    async def _fetch_feed(self, params: dict[str, str | int]) -> bytes:
        async with self._client.stream("GET", self._api_url, params=params) as response:
            self._raise_http_error(response.status_code, operation="search")
            media_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if media_type not in {"application/atom+xml", "application/xml", "text/xml"}:
                raise ArxivError("arxiv_search_content_type_invalid", temporary=False)
            declared = _parse_content_length(
                response.headers.get("content-length"),
                invalid_code="arxiv_search_content_length_invalid",
            )
            if declared is not None and declared > self._max_feed_bytes:
                raise ArxivError("arxiv_search_feed_too_large", temporary=False)
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > self._max_feed_bytes:
                    raise ArxivError("arxiv_search_feed_too_large", temporary=False)
                chunks.append(chunk)
            return b"".join(chunks)

    async def download_pdf(self, url: str, *, remaining_budget_bytes: int) -> DownloadedPdf:
        if remaining_budget_bytes <= 0:
            raise ArxivError("arxiv_total_download_budget_exceeded", temporary=False)
        for attempt in range(self._max_attempts):
            try:
                return await self._download_pdf_once(url, remaining_budget_bytes)
            except ArxivError as exc:
                if not exc.temporary or attempt + 1 == self._max_attempts:
                    raise
        raise AssertionError("unreachable")

    async def _download_pdf_once(
        self, url: str, remaining_budget_bytes: int
    ) -> DownloadedPdf:
        current = url
        for redirect_count in range(self._max_redirects + 1):
            self._validate_pdf_url(current)
            try:
                async with self._client.stream(
                    "GET", current, follow_redirects=False
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise ArxivError("arxiv_pdf_redirect_invalid", temporary=False)
                        if redirect_count == self._max_redirects:
                            raise ArxivError("arxiv_pdf_redirect_limit", temporary=False)
                        current = urljoin(current, location)
                        continue
                    self._raise_http_error(response.status_code, operation="pdf")
                    media_type = (
                        response.headers.get("content-type", "").split(";", 1)[0].lower()
                    )
                    if media_type != "application/pdf":
                        raise ArxivError("arxiv_pdf_content_type_invalid", temporary=False)
                    limit = min(self._max_file_bytes, remaining_budget_bytes)
                    declared = _parse_content_length(
                        response.headers.get("content-length"),
                        invalid_code="arxiv_pdf_content_length_invalid",
                    )
                    if declared is not None and declared > limit:
                        raise ArxivError(
                            _budget_code(declared, self._max_file_bytes), temporary=False
                        )
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > limit:
                            raise ArxivError(
                                _budget_code(size, self._max_file_bytes), temporary=False
                            )
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    if not content.startswith(b"%PDF-"):
                        raise ArxivError("arxiv_pdf_magic_invalid", temporary=False)
                    return DownloadedPdf.from_content(content, media_type)
            except ArxivError:
                raise
            except httpx2.TimeoutException as exc:
                raise ArxivError("arxiv_pdf_timeout", temporary=True) from exc
            except httpx2.TransportError as exc:
                raise ArxivError("arxiv_pdf_transport_error", temporary=True) from exc
        raise AssertionError("unreachable")

    def _validate_pdf_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self._allowed_pdf_hosts
            or parsed.username is not None
            or parsed.password is not None
            or not parsed.path.startswith("/pdf/")
        ):
            raise ArxivError("arxiv_pdf_host_not_allowed", temporary=False)

    @staticmethod
    def _raise_http_error(status_code: int, *, operation: str) -> None:
        if 200 <= status_code < 300:
            return
        if status_code == 404 and operation == "pdf":
            raise ArxivError("arxiv_pdf_not_found", temporary=False)
        if status_code == 429 or status_code >= 500:
            raise ArxivError(f"arxiv_{operation}_temporary_http", temporary=True)
        raise ArxivError(f"arxiv_{operation}_http_error", temporary=False)

    @staticmethod
    def _parse_entry(entry: ElementTree.Element) -> ArxivPaper:
        raw_id = _required_text(entry, f"{_ATOM}id")
        arxiv_id, version = parse_versioned_arxiv_id(raw_id)
        authors = tuple(
            _required_text(author, f"{_ATOM}name")
            for author in entry.findall(f"{_ATOM}author")
        )
        categories = tuple(
            value
            for node in entry.findall(f"{_ATOM}category")
            if (value := node.attrib.get("term"))
        )
        raw_pdf_url = next(
            (
                node.attrib["href"]
                for node in entry.findall(f"{_ATOM}link")
                if node.attrib.get("title") == "pdf" and "href" in node.attrib
            ),
            f"https://arxiv.org/pdf/{arxiv_id}{version}",
        )
        return ArxivPaper(
            arxiv_id=arxiv_id,
            arxiv_version=version,
            title=" ".join(_required_text(entry, f"{_ATOM}title").split()),
            abstract=" ".join(_required_text(entry, f"{_ATOM}summary").split()),
            authors=authors,
            categories=categories,
            published_at=_parse_datetime(_required_text(entry, f"{_ATOM}published")),
            updated_at=_parse_datetime(_required_text(entry, f"{_ATOM}updated")),
            pdf_url=_canonical_pdf_url(raw_pdf_url, arxiv_id, version),
        )


def _required_text(parent: ElementTree.Element, path: str) -> str:
    node = parent.find(path)
    if node is None or not (node.text or "").strip():
        raise ArxivError("arxiv_search_feed_invalid", temporary=False)
    return (node.text or "").strip()


def _parse_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArxivError("arxiv_search_feed_invalid", temporary=False) from exc


def _canonical_pdf_url(raw_url: str, arxiv_id: str, version: str) -> str:
    parsed = urlsplit(raw_url)
    expected_path = f"/pdf/{arxiv_id}{version}"
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"arxiv.org", "export.arxiv.org"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {expected_path, f"{expected_path}.pdf"}
    ):
        raise ArxivError("arxiv_search_pdf_url_invalid", temporary=False)
    return f"https://{parsed.hostname}{parsed.path}"


def _parse_content_length(value: str | None, *, invalid_code: str) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ArxivError(invalid_code, temporary=False) from exc
    if parsed < 0:
        raise ArxivError(invalid_code, temporary=False)
    return parsed


def _budget_code(size: int, max_file_bytes: int) -> str:
    if size > max_file_bytes:
        return "arxiv_pdf_too_large"
    return "arxiv_total_download_budget_exceeded"
