"""Paper 领域实体。"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

PAPER_TITLE_MAX_LENGTH = 1000


class PaperTitleSource(StrEnum):
    """Paper 标题的可审计来源。"""

    ARXIV_METADATA = "arxiv_metadata"
    PARSED_DOCUMENT = "parsed_document"


def _normalize_title(title: str) -> str:
    """压平标题空白并校验领域长度上限。"""
    normalized = " ".join(title.split())
    if not normalized:
        raise ValueError("paper_title_required")
    if len(normalized) > PAPER_TITLE_MAX_LENGTH:
        raise ValueError("paper_title_too_long")
    return normalized


@dataclass(frozen=True, slots=True)
class Paper:
    """学术作品在系统中的稳定身份。

    属性:
        paper_id: 稳定的 Paper 标识符。
        owner_id: 所有者标识符。
        created_at: 创建时间（UTC）。
        archived_at: 归档时间（UTC），None 表示 active。
        title: 论文标题；未知时为 None。
        title_source: 标题来源；必须与 title 同时存在或同时为空。
    """

    paper_id: str
    owner_id: str
    created_at: datetime
    archived_at: datetime | None = None
    title: str | None = None
    title_source: PaperTitleSource | None = None

    def __post_init__(self) -> None:
        """校验标题与来源成对存在，并规范化标题。"""
        if self.title is None and self.title_source is not None:
            raise ValueError("paper_title_required")
        if self.title is not None and self.title_source is None:
            raise ValueError("paper_title_source_required")
        if self.title is not None:
            object.__setattr__(self, "title", _normalize_title(self.title))

    @property
    def is_archived(self) -> bool:
        """是否已归档。"""
        return self.archived_at is not None

    def archive(self) -> "Paper":
        """归档 Paper；已归档时幂等返回自身。"""
        if self.is_archived:
            return self
        return replace(self, archived_at=datetime.now(UTC))

    def restore(self) -> "Paper":
        """恢复已归档 Paper；未归档时幂等返回自身。"""
        if not self.is_archived:
            return self
        return replace(self, archived_at=None)

    def with_title(self, title: str, source: PaperTitleSource) -> "Paper":
        """按来源优先级更新标题；arXiv 元数据不会被解析结果降级覆盖。"""
        normalized = _normalize_title(title)
        if (
            self.title_source is PaperTitleSource.ARXIV_METADATA
            and source is PaperTitleSource.PARSED_DOCUMENT
        ):
            return self
        if self.title == normalized and self.title_source is source:
            return self
        return replace(self, title=normalized, title_source=source)


def create_paper(
    owner_id: str,
    title: str | None = None,
    title_source: PaperTitleSource | None = None,
) -> Paper:
    """创建新的 Paper 实体。

    参数:
        owner_id: 所有者标识符。
        title: 可选论文标题。
        title_source: 可选标题来源，必须与 title 同时提供。
    返回:
        新创建的 Paper。
    """
    now = datetime.now(UTC)
    return Paper(
        paper_id=str(uuid4()),
        owner_id=owner_id,
        created_at=now,
        title=title,
        title_source=title_source,
    )
