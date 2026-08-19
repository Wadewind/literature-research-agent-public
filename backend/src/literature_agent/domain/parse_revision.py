"""Document Parse Revision 领域实体。

Parse Revision 表示某个 parser/version/config 组合对一份
Paper Version 的不可变解析结果。
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4


class ParseRevisionStatus(StrEnum):
    """Parse Revision 生命周期状态。"""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DocumentParseRevision:
    """一次解析产出的不可变 Revision。

    属性:
        revision_id: Revision 标识符。
        version_id: 所属 Paper Version。
        parser_name: Parser 名称。
        parser_version: Parser 版本。
        parser_profile_hash: 解析配置画像哈希。
        status: 解析状态。
        config: 解析配置。
        error: 失败信息（类型与截断消息），未失败为 None。
        created_at: 创建时间（UTC）。
        completed_at: 完成时间，未完成为 None。
    """

    revision_id: str
    version_id: str
    parser_name: str
    parser_version: str
    parser_profile_hash: str
    status: ParseRevisionStatus
    config: dict = field(default_factory=dict)
    error: dict | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    def mark_succeeded(self, now: datetime) -> "DocumentParseRevision":
        """返回标记为成功的新 Revision。"""
        return DocumentParseRevision(
            revision_id=self.revision_id,
            version_id=self.version_id,
            parser_name=self.parser_name,
            parser_version=self.parser_version,
            parser_profile_hash=self.parser_profile_hash,
            status=ParseRevisionStatus.SUCCEEDED,
            config=self.config,
            error=None,
            created_at=self.created_at,
            completed_at=now,
        )

    def mark_failed(self, error: dict, now: datetime) -> "DocumentParseRevision":
        """返回标记为失败的新 Revision，只记录错误类型与截断消息。"""
        return DocumentParseRevision(
            revision_id=self.revision_id,
            version_id=self.version_id,
            parser_name=self.parser_name,
            parser_version=self.parser_version,
            parser_profile_hash=self.parser_profile_hash,
            status=ParseRevisionStatus.FAILED,
            config=self.config,
            error=error,
            created_at=self.created_at,
            completed_at=now,
        )


def create_parse_revision(
    version_id: str,
    parser_name: str,
    parser_version: str,
    parser_profile_hash: str,
    config: dict | None = None,
) -> DocumentParseRevision:
    """创建状态为 ``RUNNING`` 的新 Parse Revision。"""
    return DocumentParseRevision(
        revision_id=str(uuid4()),
        version_id=version_id,
        parser_name=parser_name,
        parser_version=parser_version,
        parser_profile_hash=parser_profile_hash,
        status=ParseRevisionStatus.RUNNING,
        config=config or {},
        created_at=datetime.now(UTC),
    )
