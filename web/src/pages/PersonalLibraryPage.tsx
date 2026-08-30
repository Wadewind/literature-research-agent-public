/** owner 级个人文献库：高密度书目目录，并明确区分资产归档与 Project 收录。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiFetch, errorMessage } from "../api/client";
import type { PaperListItem, Project } from "../api/types";
import PageBar from "../components/PageBar";
import PaperTitle from "../components/PaperTitle";
import {
  filterAndSortPapers,
  type PaperSort,
  type PaperStatusFilter,
} from "../library/paperCatalog";

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(value));
}

export default function PersonalLibraryPage() {
  const queryClient = useQueryClient();
  const [includeArchived, setIncludeArchived] = useState(false);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<PaperStatusFilter>("all");
  const [project, setProject] = useState("all");
  const [sort, setSort] = useState<PaperSort>("recent");
  const papersQuery = useQuery({
    queryKey: ["library-papers", includeArchived],
    queryFn: () => apiFetch<PaperListItem[]>(
      `/api/v1/library/papers?include_archived=${includeArchived}`,
    ),
  });
  const projectsQuery = useQuery({
    queryKey: ["projects", "with-archived"],
    queryFn: () => apiFetch<Project[]>("/api/v1/projects?include_archived=true"),
  });
  const archiveMutation = useMutation({
    mutationFn: ({ paperId, restore }: { paperId: string; restore: boolean }) =>
      apiFetch<{ paper_id: string; archived_at: string | null }>(
        `/api/v1/library/papers/${paperId}/${restore ? "restore" : "archive"}`,
        { method: "POST" },
      ),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["library-papers"] }),
  });
  const projectNames = new Map(
    projectsQuery.data?.map((item) => [item.project_id, item.name]) ?? [],
  );
  const visiblePapers = filterAndSortPapers(papersQuery.data ?? [], {
    query,
    status,
    project,
    sort,
  });
  const hasFilters = Boolean(query) || status !== "all" || project !== "all";

  const clearFilters = () => {
    setQuery("");
    setStatus("all");
    setProject("all");
  };

  return (
    <div className="page-flow personal-library-page">
      <PageBar
        title="个人文献库"
        actions={
          <span className="page-bar-stat">
            <strong>{papersQuery.data ? visiblePapers.length : "—"}</strong>
            {hasFilters ? ` / ${papersQuery.data?.length ?? 0}` : ""} 篇文献
          </span>
        }
      />

      <section className="catalog-toolbar" aria-label="筛选个人文献库">
        <label className="catalog-search">
          <span>搜索</span>
          <input
            type="search"
            value={query}
            placeholder="论文标题或文件名"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <label>
          <span>状态</span>
          <select
            value={status}
            onChange={(event) => {
              const next = event.target.value as PaperStatusFilter;
              setStatus(next);
              if (next === "archived") setIncludeArchived(true);
            }}
          >
            <option value="all">全部状态</option>
            <option value="ready">已解析</option>
            <option value="working">处理中</option>
            <option value="archived">已归档</option>
          </select>
        </label>
        <label>
          <span>Project</span>
          <select value={project} onChange={(event) => setProject(event.target.value)}>
            <option value="all">全部 Project</option>
            <option value="unassigned">未收录</option>
            {projectsQuery.data?.map((item) => (
              <option key={item.project_id} value={item.project_id}>{item.name}</option>
            ))}
          </select>
        </label>
        <label>
          <span>排序</span>
          <select value={sort} onChange={(event) => setSort(event.target.value as PaperSort)}>
            <option value="recent">最近添加</option>
            <option value="title">标题 A–Z</option>
          </select>
        </label>
        <label className="catalog-archive-toggle">
          <input
            type="checkbox"
            checked={includeArchived}
            onChange={(event) => {
              setIncludeArchived(event.target.checked);
              if (!event.target.checked && status === "archived") setStatus("all");
            }}
          />
          <span>包含已归档</span>
        </label>
      </section>

      <p className="catalog-note">
        每份 PDF 只保存和解析一次；从 Project 移除不会删除个人库资产。
      </p>
      {papersQuery.isError && (
        <div className="notice error-text">{errorMessage(papersQuery.error)}</div>
      )}
      {papersQuery.isPending && <div className="skeleton-block">正在整理文献索引…</div>}
      {papersQuery.data?.length === 0 && (
        <section className="empty-state">
          <span className="empty-glyph" aria-hidden="true">≡</span>
          <h2>文献库还是空的</h2>
          <p>进入任意项目上传 PDF，文献会自动进入这里。</p>
          <Link className="button-link" to="/">选择项目</Link>
        </section>
      )}
      {papersQuery.data && papersQuery.data.length > 0 && visiblePapers.length === 0 && (
        <section className="empty-state compact">
          <h2>没有符合条件的文献</h2>
          <p>调整关键词或筛选条件后再试。</p>
          <button type="button" className="button-outline" onClick={clearFilters}>
            清除筛选
          </button>
        </section>
      )}
      {visiblePapers.length > 0 && (
        <section className="paper-catalog" aria-label="个人文献列表">
          <div className="catalog-head" aria-hidden="true">
            <span>文献</span><span>状态</span><span>收录项目</span><span>操作</span>
          </div>
          {visiblePapers.map((paper) => {
            const archived = Boolean(paper.archived_at);
            const state = archived ? "archived" : paper.version.parse_ready ? "ready" : "working";
            const membershipNames = paper.project_ids.map(
              (id) => projectNames.get(id) ?? id.slice(0, 8),
            );
            return (
              <article className={`catalog-row catalog-row-${state}`} key={paper.paper_id}>
                <div className="paper-identity catalog-identity">
                  <div className="identity-title">
                    <h2><PaperTitle paper={paper} /></h2>
                    {archived && <span className="badge badge-warn">已归档</span>}
                  </div>
                  <p>
                    <span className="catalog-filename" title={paper.version.display_filename}>
                      {paper.version.display_filename}
                    </span>
                    <span>{formatSize(paper.version.size_bytes)}</span>
                    <time dateTime={paper.created_at}>{formatDate(paper.created_at)}</time>
                  </p>
                </div>
                <div className="catalog-status">
                  <span className={`status-dot ${state}`} />
                  <span>{archived ? "已归档" : paper.version.parse_ready ? "已解析" : "处理中"}</span>
                </div>
                <div className="membership-list">
                  {paper.project_ids.length === 0 ? (
                    <span className="muted">未收录</span>
                  ) : (
                    <>
                      {paper.project_ids.slice(0, 2).map((id) => (
                        <Link key={id} to={`/projects/${id}`} title={projectNames.get(id) ?? id}>
                          {projectNames.get(id) ?? id.slice(0, 8)}
                        </Link>
                      ))}
                      {paper.project_ids.length > 2 && (
                        <span className="membership-more" title={membershipNames.join("、")}>
                          +{paper.project_ids.length - 2}
                        </span>
                      )}
                    </>
                  )}
                </div>
                <div className="catalog-actions">
                  <button
                    type="button"
                    className="button-text-warn"
                    disabled={archiveMutation.isPending}
                    onClick={() => archiveMutation.mutate({
                      paperId: paper.paper_id,
                      restore: archived,
                    })}
                  >
                    {archived ? "恢复" : "归档"}
                  </button>
                </div>
              </article>
            );
          })}
        </section>
      )}
      {archiveMutation.isError && (
        <p className="error-text">{errorMessage(archiveMutation.error)}</p>
      )}
    </div>
  );
}
