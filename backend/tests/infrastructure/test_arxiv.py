from collections.abc import Iterator
from typing import Any

import httpx
import httpx2
import pytest
import respx

from literature_agent.domain.arxiv import ArxivError, ArxivSearchQuery
from literature_agent.infrastructure.arxiv import HttpxArxivGateway

API_URL = "https://export.arxiv.org/api/query"


class ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


class FakeClock:
    """为 arXiv 频率限制测试提供不等待真实时间的单调时钟。"""

    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


def _entry(identifier: str, title: str = " Agent  Systems ") -> str:
    return f"""
    <entry>
      <id>https://arxiv.org/abs/{identifier}</id>
      <updated>2026-08-20T00:00:00Z</updated>
      <published>2026-08-19T00:00:00Z</published>
      <title>{title}</title><summary> A useful  abstract. </summary>
      <author><name>Alice</name></author>
      <category term="cs.AI" />
      <link title="pdf" href="https://arxiv.org/pdf/{identifier}" />
    </entry>"""


def _entry_with_pdf(identifier: str, pdf_url: str) -> str:
    return _entry(identifier).replace(
        f"https://arxiv.org/pdf/{identifier}", pdf_url
    )


def _feed(*entries: str) -> bytes:
    return (
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:arxiv="http://arxiv.org/schemas/atom">'
        + "".join(entries)
        + "</feed>"
    ).encode()


@pytest.fixture
def gateway() -> Iterator[HttpxArxivGateway]:
    client = httpx2.AsyncClient(trust_env=False, follow_redirects=False)
    yield HttpxArxivGateway(client=client, max_file_bytes=32)


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"max_file_bytes": 0}, "arxiv_max_file_bytes_invalid"),
        ({"max_feed_bytes": 0}, "arxiv_max_feed_bytes_invalid"),
        ({"max_redirects": -1}, "arxiv_max_redirects_invalid"),
        ({"allowed_pdf_hosts": frozenset()}, "arxiv_pdf_hosts_invalid"),
        (
            {"allowed_pdf_hosts": frozenset({"arxiv.org", "evil.example"})},
            "arxiv_pdf_hosts_invalid",
        ),
    ],
)
def test_gateway_rejects_unbounded_or_non_official_configuration(
    overrides: dict[str, Any], code: str
) -> None:
    client = httpx2.AsyncClient(trust_env=False, follow_redirects=False)
    with pytest.raises(ValueError, match=code):
        HttpxArxivGateway(client=client, **overrides)


def test_gateway_accepts_nonempty_official_pdf_host_subset() -> None:
    client = httpx2.AsyncClient(trust_env=False, follow_redirects=False)
    HttpxArxivGateway(client=client, allowed_pdf_hosts=frozenset({"arxiv.org"}))


@pytest.mark.asyncio
async def test_search_maps_feed_preserves_rank_deduplicates_and_truncates(
    httpx2_mock: respx.Router, gateway: HttpxArxivGateway
) -> None:
    route = httpx2_mock.get(API_URL).mock(
        return_value=httpx.Response(
            200,
            content=_feed(
                _entry("2401.00002v1", "Second"),
                _entry("2401.00002v1", "Duplicate"),
                _entry("2401.00001v3", "First"),
                _entry("2401.99999v1", "Truncated"),
            ),
            headers={"content-type": "application/atom+xml"},
        )
    )

    papers = await gateway.search(ArxivSearchQuery("all:agents", max_results=2))

    assert [(x.arxiv_id, x.arxiv_version, x.title) for x in papers] == [
        ("2401.00002", "v1", "Second"),
        ("2401.00001", "v3", "First"),
    ]
    assert route.calls[0].request.url.params["search_query"] == "all:agents"
    assert route.calls[0].request.url.params["max_results"] == "2"


@pytest.mark.asyncio
async def test_search_canonicalizes_official_http_pdf_link(
    httpx2_mock: respx.Router, gateway: HttpxArxivGateway
) -> None:
    httpx2_mock.get(API_URL).mock(
        return_value=httpx.Response(
            200,
            content=_feed(
                _entry_with_pdf(
                    "2401.00001v1", "http://export.arxiv.org/pdf/2401.00001v1.pdf"
                )
            ),
            headers={"content-type": "application/atom+xml"},
        )
    )
    papers = await gateway.search(ArxivSearchQuery("all:agents"))
    assert papers[0].pdf_url == "https://export.arxiv.org/pdf/2401.00001v1.pdf"


@pytest.mark.asyncio
async def test_search_extracts_declared_page_count_from_arxiv_comment(
    httpx2_mock: respx.Router, gateway: HttpxArxivGateway
) -> None:
    entry = _entry("2401.00001v1").replace(
        "</entry>", "<arxiv:comment>18 pages, 4 figures</arxiv:comment></entry>"
    )
    httpx2_mock.get(API_URL).mock(
        return_value=httpx.Response(
            200,
            content=_feed(entry),
            headers={"content-type": "application/atom+xml"},
        )
    )

    papers = await gateway.search(ArxivSearchQuery("id:2401.00001"))

    assert papers[0].page_count == 18


@pytest.mark.asyncio
async def test_search_rejects_feed_injected_pdf_url(
    httpx2_mock: respx.Router, gateway: HttpxArxivGateway
) -> None:
    httpx2_mock.get(API_URL).mock(
        return_value=httpx.Response(
            200,
            content=_feed(
                _entry_with_pdf("2401.00001v1", "https://evil.example/a.pdf")
            ),
            headers={"content-type": "application/atom+xml"},
        )
    )
    with pytest.raises(ArxivError, match="arxiv_search_pdf_url_invalid"):
        await gateway.search(ArxivSearchQuery("all:agents"))


@pytest.mark.asyncio
async def test_search_streaming_feed_limit_checks_actual_bytes(
    httpx2_mock: respx.Router,
) -> None:
    client = httpx2.AsyncClient(trust_env=False, follow_redirects=False)
    gateway = HttpxArxivGateway(client=client, max_feed_bytes=8)
    httpx2_mock.get(API_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/atom+xml"},
            stream=ChunkedStream([b"<feed>", b"too-large"]),
        )
    )
    with pytest.raises(ArxivError, match="arxiv_search_feed_too_large"):
        await gateway.search(ArxivSearchQuery("all:agents"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response,code,temporary",
    [
        (httpx.Response(503), "arxiv_search_server_error", True),
        (
            httpx.Response(200, content=b"not atom", headers={"content-type": "text/html"}),
            "arxiv_search_content_type_invalid",
            False,
        ),
    ],
)
async def test_search_classifies_failures(
    httpx2_mock: respx.Router,
    gateway: HttpxArxivGateway,
    response: httpx.Response,
    code: str,
    temporary: bool,
) -> None:
    httpx2_mock.get(API_URL).mock(return_value=response)
    with pytest.raises(ArxivError) as caught:
        await gateway.search(ArxivSearchQuery("all:agents"))
    assert (caught.value.code, caught.value.temporary) == (code, temporary)


@pytest.mark.asyncio
async def test_search_429_preserves_only_safe_rate_limit_diagnostics(
    httpx2_mock: respx.Router, gateway: HttpxArxivGateway
) -> None:
    route = httpx2_mock.get(API_URL).mock(
        return_value=httpx.Response(429, headers={"retry-after": "17"})
    )

    with pytest.raises(ArxivError) as caught:
        await gateway.search(ArxivSearchQuery("all:agents"))

    assert caught.value.code == "arxiv_search_rate_limited"
    assert caught.value.temporary is True
    assert caught.value.http_status == 429
    assert caught.value.retry_after_seconds == 17.0
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_search_timeout_is_temporary(
    httpx2_mock: respx.Router, gateway: HttpxArxivGateway
) -> None:
    httpx2_mock.get(API_URL).mock(side_effect=httpx2.ReadTimeout("timeout"))
    with pytest.raises(ArxivError) as caught:
        await gateway.search(ArxivSearchQuery("all:agents"))
    assert caught.value.code == "arxiv_search_timeout"
    assert caught.value.temporary is True


@pytest.mark.asyncio
async def test_search_does_not_retry_temporary_http_inside_adapter(
    httpx2_mock: respx.Router, gateway: HttpxArxivGateway
) -> None:
    route = httpx2_mock.get(API_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(
                200,
                content=_feed(_entry("2401.00001v1")),
                headers={"content-type": "application/atom+xml"},
            ),
        ]
    )
    with pytest.raises(ArxivError, match="arxiv_search_server_error"):
        await gateway.search(ArxivSearchQuery("all:agents"))
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_search_serializes_requests_and_spaces_starts_by_three_seconds(
    httpx2_mock: respx.Router,
) -> None:
    clock = FakeClock()
    client = httpx2.AsyncClient(trust_env=False, follow_redirects=False)
    gateway = HttpxArxivGateway(
        client=client,
        clock=clock.monotonic,
        sleep=clock.sleep,
    )
    route = httpx2_mock.get(API_URL).mock(
        return_value=httpx.Response(
            200,
            content=_feed(_entry("2401.00001v1")),
            headers={"content-type": "application/atom+xml"},
        )
    )

    await gateway.search(ArxivSearchQuery("all:first"))
    await gateway.search(ArxivSearchQuery("all:second"))

    assert route.call_count == 2
    assert clock.sleeps == [3.0]


@pytest.mark.asyncio
async def test_download_validates_redirect_host_before_second_request(
    httpx2_mock: respx.Router, gateway: HttpxArxivGateway
) -> None:
    start = "https://arxiv.org/pdf/2401.00001v1"
    httpx2_mock.get(start).mock(
        return_value=httpx.Response(302, headers={"location": "https://evil.example/a.pdf"})
    )
    with pytest.raises(ArxivError, match="arxiv_pdf_host_not_allowed"):
        await gateway.download_pdf(start, remaining_budget_bytes=32)
    assert len(httpx2_mock.calls) == 1


@pytest.mark.asyncio
async def test_download_returns_hash_after_pdf_validation(
    httpx2_mock: respx.Router, gateway: HttpxArxivGateway
) -> None:
    url = "https://arxiv.org/pdf/2401.00001v1"
    content = b"%PDF-1.7\nvalid"
    httpx2_mock.get(url).mock(
        return_value=httpx.Response(
            200, content=content, headers={"content-type": "application/pdf"}
        )
    )
    downloaded = await gateway.download_pdf(url, remaining_budget_bytes=32)
    assert downloaded.content == content
    assert len(downloaded.content_hash) == 64


@pytest.mark.asyncio
async def test_download_does_not_retry_temporary_http_inside_adapter(
    httpx2_mock: respx.Router, gateway: HttpxArxivGateway
) -> None:
    url = "https://arxiv.org/pdf/2401.00001v1"
    route = httpx2_mock.get(url).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(
                200,
                content=b"%PDF-valid",
                headers={"content-type": "application/pdf"},
            ),
        ]
    )
    with pytest.raises(ArxivError, match="arxiv_pdf_server_error"):
        await gateway.download_pdf(url, remaining_budget_bytes=32)
    assert route.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response,budget,code,temporary",
    [
        (httpx.Response(404), 32, "arxiv_pdf_not_found", False),
        (httpx.Response(503), 32, "arxiv_pdf_server_error", True),
        (
            httpx.Response(200, content=b"html", headers={"content-type": "text/html"}),
            32,
            "arxiv_pdf_content_type_invalid",
            False,
        ),
        (
            httpx.Response(200, content=b"not-pdf", headers={"content-type": "application/pdf"}),
            32,
            "arxiv_pdf_magic_invalid",
            False,
        ),
        (
            httpx.Response(
                200,
                content=b"%PDF-" + b"x" * 40,
                headers={"content-type": "application/pdf"},
            ),
            100,
            "arxiv_pdf_too_large",
            False,
        ),
        (
            httpx.Response(200, content=b"%PDF-12345", headers={"content-type": "application/pdf"}),
            8,
            "arxiv_total_download_budget_exceeded",
            False,
        ),
    ],
)
async def test_download_classifies_validation_and_http_failures(
    httpx2_mock: respx.Router,
    gateway: HttpxArxivGateway,
    response: httpx.Response,
    budget: int,
    code: str,
    temporary: bool,
) -> None:
    url = "https://arxiv.org/pdf/2401.00001v1"
    httpx2_mock.get(url).mock(return_value=response)
    with pytest.raises(ArxivError) as caught:
        await gateway.download_pdf(url, remaining_budget_bytes=budget)
    assert (caught.value.code, caught.value.temporary) == (code, temporary)


@pytest.mark.asyncio
@pytest.mark.parametrize("length", ["invalid", "-1"])
async def test_download_stably_rejects_invalid_content_length(
    httpx2_mock: respx.Router,
    gateway: HttpxArxivGateway,
    length: str,
) -> None:
    url = "https://arxiv.org/pdf/2401.00001v1"
    httpx2_mock.get(url).mock(
        return_value=httpx.Response(
            200,
            content=b"%PDF-valid",
            headers={"content-type": "application/pdf", "content-length": length},
        )
    )
    with pytest.raises(ArxivError, match="arxiv_pdf_content_length_invalid"):
        await gateway.download_pdf(url, remaining_budget_bytes=32)
