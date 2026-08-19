"""PaperVersion 领域实体。"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class PaperVersion:
    """Paper 的一份不可变 PDF 版本。

    属性:
        version_id: 稳定的 Version 标识符。
        paper_id: 所属 Paper 标识符。
        file_hash: 文件内容 SHA-256 哈希（十六进制）。
        storage_key: 文件在 Storage 中的键。
        size_bytes: 文件字节大小。
        content_type: 文件 MIME 类型。
        current_parse_revision_id: 当前生效的 Parse Revision，未解析为 None。
        created_at: 创建时间（UTC）。
    """

    version_id: str
    paper_id: str
    file_hash: str
    storage_key: str
    size_bytes: int
    content_type: str
    created_at: datetime
    current_parse_revision_id: str | None = None


def create_paper_version(
    paper_id: str,
    file_hash: str,
    storage_key: str,
    size_bytes: int,
    content_type: str,
) -> PaperVersion:
    """创建新的 PaperVersion 实体。

    参数:
        paper_id: 所属 Paper 标识符。
        file_hash: 文件内容 SHA-256 哈希。
        storage_key: 文件在 Storage 中的键。
        size_bytes: 文件字节大小。
        content_type: 文件 MIME 类型。

    返回:
        新创建的 PaperVersion。
    """
    now = datetime.now(UTC)
    return PaperVersion(
        version_id=str(uuid4()),
        paper_id=paper_id,
        file_hash=file_hash,
        storage_key=storage_key,
        size_bytes=size_bytes,
        content_type=content_type,
        created_at=now,
    )
