/** owner 级个人文献库：只展示一份物理文献及其 Project 收录范围。 */

import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiFetch, errorMessage } from "../api/client";
import type { PaperListItem, Project } from "../api/types";

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export default function PersonalLibraryPage() {
  const papersQuery = useQuery({
    queryKey: ["library-papers"],
    queryFn: () => apiFetch<PaperListItem[]>("/api/v1/library/papers"),
  });
  const projectsQuery = useQuery({
    queryKey: ["projects"],
    queryFn: () => apiFetch<Project[]>("/api/v1/projects"),
  });
  const projectNames = new Map(
    projectsQuery.data?.map((project) => [project.project_id, project.name]) ?? [],
  );

  return (
    <div className="page-flow">
      <header className="page-heading">
        <div>
          <p className="eyebrow">PERSONAL REPOSITORY</p>
          <h1>个人文献库</h1>
          <p>每份 PDF 只保存和解析一次，可被多个研究项目收录。</p>
        </div>
        <div className="metric-block"><strong>{papersQuery.data?.length ?? "—"}</strong><span>唯一文献</span></div>
      </header>

      {papersQuery.isError && <div className="notice error-text">{errorMessage(papersQuery.error)}</div>}
      {papersQuery.isPending && <div className="skeleton-block">正在整理文献索引…</div>}
      {papersQuery.data?.length === 0 && (
        <section className="empty-state">
          <span className="empty-glyph" aria-hidden="true">≡</span>
          <h2>文献库还是空的</h2>
          <p>进入任意项目上传 PDF，文献会自动进入这里。</p>
          <Link className="button-link" to="/">选择项目</Link>
        </section>
      )}
      {papersQuery.data && papersQuery.data.length > 0 && (
        <section className="paper-ledger" aria-label="个人文献列表">
          <div className="ledger-head"><span>文献 / 版本</span><span>解析</span><span>收录项目</span></div>
          {papersQuery.data.map((paper, index) => (
            <article className="ledger-row" key={paper.paper_id}>
              <span className="paper-index">{String(index + 1).padStart(2, "0")}</span>
              <div className="paper-identity">
                <h2>{paper.version.display_filename}</h2>
                <p><span>{formatSize(paper.version.size_bytes)}</span><span className="mono">{paper.paper_id.slice(0, 8)}</span></p>
              </div>
              <div><span className={`status-dot ${paper.version.parse_ready ? "ready" : "working"}`} />{paper.version.parse_ready ? "已解析" : "处理中"}</div>
              <div className="membership-list">
                {paper.project_ids.length === 0 ? <span className="muted">未收录</span> : paper.project_ids.map((id) => (
                  <Link key={id} to={`/projects/${id}`}>{projectNames.get(id) ?? id.slice(0, 8)}</Link>
                ))}
              </div>
            </article>
          ))}
        </section>
      )}
    </div>
  );
}
