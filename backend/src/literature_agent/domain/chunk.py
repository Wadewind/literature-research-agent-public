"""ChunkSet / Chunk / ChunkElementLink 领域实体。

ChunkSet 是某个 Parse Revision 在特定 Chunk Profile 下的版本化
切分结果；Chunk 是面向检索的有序文本块；ChunkElementLink 把 Chunk
回溯到来源 Element（顺序保留），支撑引用与页码跳转。
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4


class ChunkSetStatus(StrEnum):
    """ChunkSet 生命周期状态。"""

    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ChunkSet:
    """某个 Parse Revision 的一次切分结果集合。

    属性:
        chunk_set_id: ChunkSet 标识符。
        parse_revision_id: 来源 Parse Revision。
        profile_hash: Chunk Profile 哈希（chunk 与 embedding 参数共同参与）。
        status: 切分状态。
        config: 切分配置快照。
        error: 失败信息（类型与截断消息），未失败为 None。
        created_at: 创建时间（UTC）。
        completed_at: 完成时间，未完成为 None。
    """

    chunk_set_id: str
    parse_revision_id: str
    profile_hash: str
    status: ChunkSetStatus
    config: dict = field(default_factory=dict)
    error: dict | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    def mark_ready(self, now: datetime) -> "ChunkSet":
        """返回标记为就绪的新 ChunkSet。"""
        return ChunkSet(
            chunk_set_id=self.chunk_set_id,
            parse_revision_id=self.parse_revision_id,
            profile_hash=self.profile_hash,
            status=ChunkSetStatus.READY,
            config=self.config,
            error=None,
            created_at=self.created_at,
            completed_at=now,
        )

    def mark_failed(self, error: dict, now: datetime) -> "ChunkSet":
        """返回标记为失败的新 ChunkSet，只记录错误类型与截断消息。"""
        return ChunkSet(
            chunk_set_id=self.chunk_set_id,
            parse_revision_id=self.parse_revision_id,
            profile_hash=self.profile_hash,
            status=ChunkSetStatus.FAILED,
            config=self.config,
            error=error,
            created_at=self.created_at,
            completed_at=now,
        )

    def reset_running(self) -> "ChunkSet":
        """返回重置为 RUNNING 的新 ChunkSet（失败/崩溃遗留行重跑时复用同一行）。"""
        return ChunkSet(
            chunk_set_id=self.chunk_set_id,
            parse_revision_id=self.parse_revision_id,
            profile_hash=self.profile_hash,
            status=ChunkSetStatus.RUNNING,
            config=self.config,
            error=None,
            created_at=self.created_at,
            completed_at=None,
        )


@dataclass(frozen=True, slots=True)
class Chunk:
    """面向检索的有序文本块。

    属性:
        chunk_id: Chunk 标识符。
        chunk_set_id: 所属 ChunkSet。
        sequence: ChunkSet 内阅读顺序，从 1 开始，ChunkSet 内唯一。
        text: 检索文本（可能含章节标题前缀）。
        token_count: 文本 token 数（含前缀，按 profile tokenizer 计算）。
        section_path: 章节路径；不属于任何章节时为 None。
        page_start/page_end: 来源页码范围；无任何来源定位时为 None。
        content_hash: 文本的 SHA-256 哈希。
    """

    chunk_id: str
    chunk_set_id: str
    sequence: int
    text: str
    token_count: int
    section_path: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    content_hash: str = ""


@dataclass(frozen=True, slots=True)
class ChunkElementLink:
    """Chunk 到来源 Element 的有序映射。

    属性:
        chunk_id: 所属 Chunk。
        element_id: 来源 Element。
        sequence: Element 在 Chunk 内的顺序，从 1 开始。
    """

    chunk_id: str
    element_id: str
    sequence: int


def create_chunk_set(
    parse_revision_id: str,
    profile_hash: str,
    config: dict | None = None,
) -> ChunkSet:
    """创建状态为 ``RUNNING`` 的新 ChunkSet。"""
    return ChunkSet(
        chunk_set_id=str(uuid4()),
        parse_revision_id=parse_revision_id,
        profile_hash=profile_hash,
        status=ChunkSetStatus.RUNNING,
        config=config or {},
        created_at=datetime.now(UTC),
    )
