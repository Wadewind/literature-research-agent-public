"""Evidence 固化应用服务（切片 7）。

把一次 rag_answer Run 的 RetrievalResult 固化为可引用 Evidence：
denormalize paper/version/parse_revision/章节/页码/摘录，同一短事务
写入；``(run_id, chunk_id)`` 唯一约束兜底重复提交，重复调用幂等
（已固化的 Chunk 回读返回既有行，不报错）。

防御纵深：检索 SQL 强过滤已保证结果属于 Project 范围，本服务再校验
每条结果的 ``(paper_id, version_id)`` 属于 Run ``input_payload`` 固化
的版本范围快照；不在快照内的结果拒绝固化（EvidenceScopeError，
永久错误）。Citation Validator 消费的就是这里固化的 Evidence 集合。
"""

import logging
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from typing import TypeVar

from literature_agent.application.ports.chunk_set_repository import ChunkSetRepository
from literature_agent.application.ports.evidence_repository import EvidenceRepository
from literature_agent.application.ports.session import Session
from literature_agent.application.retriever import RetrievalResult
from literature_agent.domain.evidence import (
    EVIDENCE_EXCERPT_MAX_CHARS,
    RUN_INPUT_VERSION_SCOPE_KEY,
    Evidence,
    create_evidence,
)
from literature_agent.domain.exceptions import EvidenceScopeError
from literature_agent.domain.run import Run

TSession = TypeVar("TSession", bound=Session)

logger = logging.getLogger(__name__)


class EvidenceService[TSession: Session]:
    """Evidence 固化服务。

    不变量:
        - 只固化 Run 版本范围快照内的检索结果；
        - Evidence 在一次短事务内写入，重复提交幂等；
        - excerpt 截断到 ``EVIDENCE_EXCERPT_MAX_CHARS`` 字符，
          不复制 Chunk 全文。
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[TSession]],
        evidence_repo_factory: Callable[[TSession], EvidenceRepository],
        chunk_set_repo_factory: Callable[[TSession], ChunkSetRepository],
    ) -> None:
        """初始化 EvidenceService。

        参数:
            session_factory: 返回异步上下文管理器的工厂（写入短事务）。
            evidence_repo_factory: 根据 session 创建 EvidenceRepository。
            chunk_set_repo_factory: 根据 session 创建 ChunkSetRepository
                （经 chunk_set_id 解析 parse_revision_id）。
        """
        self._session_factory = session_factory
        self._evidence_repo_factory = evidence_repo_factory
        self._chunk_set_repo_factory = chunk_set_repo_factory

    async def commit_evidence(
        self,
        *,
        run: Run,
        retrieval_results: Sequence[RetrievalResult],
    ) -> list[Evidence]:
        """把一次 Run 的检索结果固化为 Evidence，按输入顺序返回。

        参数:
            run: 产生结果的 rag_answer Run（含版本范围快照）。
            retrieval_results: Retriever 返回的最终候选。

        返回:
            与输入一一对应的 Evidence 列表（已固化的回读既有行）。

        异常:
            EvidenceScopeError: Run 缺少快照或结果不在快照内。
        """
        if not retrieval_results:
            return []
        snapshot = self._load_version_scope(run)

        # 输入内 chunk_id 去重（RRF 已去重，这里防御性保留首次出现）
        deduped = list({r.chunk.chunk_id: r for r in retrieval_results}.values())
        for result in deduped:
            pair = (result.paper_id, result.version_id)
            if pair not in snapshot:
                raise EvidenceScopeError(
                    f"检索结果 (paper_id={result.paper_id}, "
                    f"version_id={result.version_id}) 不在 Run {run.run_id} "
                    "的版本范围快照内"
                )

        async with self._session_factory() as session:
            evidence_repo = self._evidence_repo_factory(session)
            chunk_set_repo = self._chunk_set_repo_factory(session)

            existing = {
                e.chunk_id: e for e in await evidence_repo.list_by_run(run.run_id)
            }
            fresh: dict[str, Evidence] = {}
            for result in deduped:
                if result.chunk.chunk_id in existing:
                    continue
                chunk_set = await chunk_set_repo.get_by_id(result.chunk.chunk_set_id)
                if chunk_set is None:
                    raise EvidenceScopeError(
                        f"Chunk {result.chunk.chunk_id} 引用的 ChunkSet "
                        f"{result.chunk.chunk_set_id} 不存在"
                    )
                # excerpt 只保存定位摘录，截断到上限，不复制 Chunk 全文
                excerpt = result.chunk.text[:EVIDENCE_EXCERPT_MAX_CHARS]
                fresh[result.chunk.chunk_id] = create_evidence(
                    run_id=run.run_id,
                    project_id=run.project_id,
                    paper_id=result.paper_id,
                    version_id=result.version_id,
                    parse_revision_id=chunk_set.parse_revision_id,
                    chunk_id=result.chunk.chunk_id,
                    section_path=result.chunk.section_path,
                    page_start=result.chunk.page_start,
                    page_end=result.chunk.page_end,
                    excerpt=excerpt,
                )
            await evidence_repo.add_many(list(fresh.values()))
            await session.commit()

        logger.info(
            "Evidence 固化完成: run_id=%s 候选=%d 新增=%d 复用=%d",
            run.run_id,
            len(deduped),
            len(fresh),
            len(deduped) - len(fresh),
        )
        return [
            existing.get(r.chunk.chunk_id) or fresh[r.chunk.chunk_id] for r in deduped
        ]

    @staticmethod
    def _load_version_scope(run: Run) -> set[tuple[str, str]]:
        """从 Run ``input_payload`` 读取版本范围快照为 (paper_id, version_id) 集合。

        异常:
            EvidenceScopeError: 快照缺失或形状非法。
        """
        raw = run.input_payload.get(RUN_INPUT_VERSION_SCOPE_KEY)
        if not isinstance(raw, list):
            raise EvidenceScopeError(
                f"Run {run.run_id} 缺少版本范围快照 "
                f"input_payload.{RUN_INPUT_VERSION_SCOPE_KEY}"
            )
        snapshot: set[tuple[str, str]] = set()
        for entry in raw:
            if not isinstance(entry, dict) or not all(
                isinstance(entry.get(key), str) for key in ("paper_id", "version_id")
            ):
                raise EvidenceScopeError(
                    f"Run {run.run_id} 的版本范围快照条目形状非法"
                )
            snapshot.add((entry["paper_id"], entry["version_id"]))
        return snapshot
