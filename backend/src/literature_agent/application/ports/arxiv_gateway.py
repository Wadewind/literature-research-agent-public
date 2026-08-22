"""arXiv 检索与受限 PDF 下载端口。"""

from typing import Protocol

from literature_agent.domain.arxiv import ArxivPaper, ArxivSearchQuery, DownloadedPdf


class ArxivGateway(Protocol):
    """官方 arXiv API/PDF 的应用层抽象。"""

    async def search(self, query: ArxivSearchQuery) -> list[ArxivPaper]:
        """执行已验证查询，返回确定性去重结果。"""
        ...

    async def download_pdf(self, url: str, *, remaining_budget_bytes: int) -> DownloadedPdf:
        """在单文件与剩余总预算内下载并验证 PDF。"""
        ...
