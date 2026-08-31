"""受限 arXiv 检索与下载领域契约。"""

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from urllib.parse import unquote, urlsplit

_ALLOWED_FIELDS = frozenset({"all", "ti", "au", "abs", "co", "jr", "cat", "rn", "id"})
_FIELD_PATTERN = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z]+):")
_ARXIV_ID_PATTERN = re.compile(
    r"^(?P<base>(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5}))v(?P<version>[1-9]\d*)$",
    re.IGNORECASE,
)
_ARXIV_INPUT_ID_PATTERN = re.compile(
    r"^(?P<base>(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5}))(?:v[1-9]\d*)?$",
    re.IGNORECASE,
)
_ARXIV_INPUT_HOSTS = frozenset({"arxiv.org", "www.arxiv.org", "export.arxiv.org"})


class ArxivSortBy(StrEnum):
    """arXiv API 支持的受限排序字段。"""

    RELEVANCE = "relevance"
    LAST_UPDATED_DATE = "lastUpdatedDate"
    SUBMITTED_DATE = "submittedDate"


class ArxivSortOrder(StrEnum):
    """arXiv API 排序方向。"""

    ASCENDING = "ascending"
    DESCENDING = "descending"


class ArxivError(Exception):
    """arXiv 操作失败，并携带可持久化稳定错误码。"""

    def __init__(
        self,
        code: str,
        *,
        temporary: bool,
        http_status: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        if http_status is not None and not 100 <= http_status <= 599:
            raise ValueError("arxiv_http_status_invalid")
        if retry_after_seconds is not None and (
            not math.isfinite(retry_after_seconds) or retry_after_seconds < 0
        ):
            raise ValueError("arxiv_retry_after_invalid")
        super().__init__(code)
        self.code = code
        self.temporary = temporary
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds


class ArxivQueryValidationError(ValueError):
    """检索查询未通过确定性校验。"""


@dataclass(frozen=True, slots=True)
class ArxivSearchQuery:
    """经确定性校验的 arXiv 查询。"""

    expression: str
    max_results: int = 10
    start: int = 0
    sort_by: ArxivSortBy = ArxivSortBy.RELEVANCE
    sort_order: ArxivSortOrder = ArxivSortOrder.DESCENDING

    def __post_init__(self) -> None:
        if any(ord(char) < 32 for char in self.expression):
            raise ArxivQueryValidationError("arxiv_query_control_character")
        normalized = " ".join(self.expression.split())
        if not normalized or len(normalized) > 1024:
            raise ArxivQueryValidationError("arxiv_query_invalid_length")
        if "://" in normalized:
            raise ArxivQueryValidationError("arxiv_query_url_forbidden")
        fields = {match.group(1).lower() for match in _FIELD_PATTERN.finditer(normalized)}
        if fields - _ALLOWED_FIELDS:
            raise ArxivQueryValidationError("arxiv_query_field_forbidden")
        if not 1 <= self.max_results <= 50 or self.start < 0:
            raise ArxivQueryValidationError("arxiv_query_pagination_invalid")
        object.__setattr__(self, "expression", normalized)


@dataclass(frozen=True, slots=True)
class ArxivPaper:
    """标准化后的单条 arXiv 检索结果。"""

    arxiv_id: str
    arxiv_version: str
    title: str
    abstract: str
    authors: tuple[str, ...]
    categories: tuple[str, ...]
    published_at: datetime
    updated_at: datetime
    pdf_url: str
    page_count: int | None = None

    @property
    def versioned_id(self) -> str:
        """返回包含版本号的稳定 arXiv ID。"""
        return f"{self.arxiv_id}{self.arxiv_version}"


@dataclass(frozen=True, slots=True)
class DownloadedPdf:
    """已验证的 PDF 内容及其内容摘要。"""

    content: bytes
    content_hash: str
    media_type: str

    def __post_init__(self) -> None:
        if not self.content.startswith(b"%PDF-"):
            raise ValueError("downloaded_pdf_magic_invalid")
        if self.media_type != "application/pdf":
            raise ValueError("downloaded_pdf_media_type_invalid")
        expected_hash = hashlib.sha256(self.content).hexdigest()
        if self.content_hash != expected_hash:
            raise ValueError("downloaded_pdf_hash_mismatch")

    @classmethod
    def from_content(cls, content: bytes, media_type: str) -> "DownloadedPdf":
        return cls(
            content=content,
            content_hash=hashlib.sha256(content).hexdigest(),
            media_type=media_type,
        )


def parse_versioned_arxiv_id(value: str) -> tuple[str, str]:
    """解析 API entry ID 中强制存在的 arXiv 版本。"""
    candidate = value.strip().removeprefix("https://arxiv.org/abs/").removeprefix(
        "http://arxiv.org/abs/"
    )
    match = _ARXIV_ID_PATTERN.fullmatch(candidate)
    if match is None:
        raise ArxivError("arxiv_entry_id_invalid", temporary=False)
    return match.group("base"), f"v{match.group('version')}"


def normalize_arxiv_search_input(value: str) -> str:
    """把官方 URL 或 arXiv ID 归一化为精确查询，其余输入保留为检索表达式。"""
    candidate = value.strip()
    if "://" in candidate:
        parsed = urlsplit(candidate)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in _ARXIV_INPUT_HOSTS:
            raise ArxivQueryValidationError("arxiv_query_url_forbidden")
        if parsed.query or parsed.fragment:
            raise ArxivQueryValidationError("arxiv_query_url_invalid")
        path = unquote(parsed.path).strip("/")
        if path.startswith("abs/"):
            candidate = path.removeprefix("abs/")
        elif path.startswith("pdf/"):
            candidate = path.removeprefix("pdf/").removesuffix(".pdf")
        else:
            raise ArxivQueryValidationError("arxiv_query_url_invalid")
    match = _ARXIV_INPUT_ID_PATTERN.fullmatch(candidate)
    if match is not None:
        return f"id:{match.group('base')}"
    return candidate
