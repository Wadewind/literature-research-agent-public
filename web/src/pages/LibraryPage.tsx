/** Project 工作台：文献、Conversation 入口、索引状态与归档管理。 */

import { useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import { useMutation, useMutationState, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { apiFetch, errorMessage } from "../api/client";
import type {
  ArxivSearchResult,
  IndexStatus,
  PaperListItem,
  Project,
  ProjectPaperResult,
  UploadResult,
} from "../api/types";
import { createScopeSelection, toggleScopePaper, type ScopeSelection } from "../conversations/scopeSelection";
import { ensureUploadIntent, type UploadIntent } from "../library/uploadIntent";
import PageBar from "../components/PageBar";
import PaperTitle from "../components/PaperTitle";
import { chatHomePath, chatPreselectionPath } from "../workspace/projectWorkspace";

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

type IngestMode = "upload" | "reuse" | "search";
type ArxivImportVariables = { paper: ArxivSearchResult; key: string };

const ARXIV_DATE_FORMATTER = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "short",
  day: "numeric",
});

export default function LibraryPage() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [intent, setIntent] = useState<UploadIntent | null>(null);
  const [lastUpload, setLastUpload] = useState<UploadResult | null>(null);
  const [selection, setSelection] = useState<ScopeSelection>(createScopeSelection);
  const [editing, setEditing] = useState(false);
  const [projectName, setProjectName] = useState("");
  const [projectDescription, setProjectDescription] = useState("");
  const [arxivQuery, setArxivQuery] = useState("");
  const [lastArxivImport, setLastArxivImport] = useState<UploadResult | null>(null);
  const ingestParameter = searchParams.get("add");
  const ingestOpen = ingestParameter !== null;
  const ingestMode: IngestMode =
    ingestParameter === "reuse" || ingestParameter === "search"
      ? ingestParameter
      : "upload";
  const showIngestMode = (mode: IngestMode | null) => {
    const next = new URLSearchParams(searchParams);
    if (mode === null) next.delete("add");
    else next.set("add", mode);
    setSearchParams(next, { replace: true });
  };

  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => apiFetch<Project>(`/api/v1/projects/${projectId}`),
  });
  const papersQuery = useQuery({
    queryKey: ["papers", projectId],
    queryFn: () => apiFetch<PaperListItem[]>(`/api/v1/projects/${projectId}/papers`),
    refetchInterval: (query) =>
      query.state.data?.some((paper) => !paper.version.parse_ready) ? 3000 : false,
  });
  const libraryQuery = useQuery({
    queryKey: ["library-papers"],
    queryFn: () => apiFetch<PaperListItem[]>("/api/v1/library/papers"),
  });

  useEffect(() => {
    if (!projectQuery.data) return;
    setProjectName(projectQuery.data.name);
    setProjectDescription(projectQuery.data.description);
  }, [projectQuery.data]);

  const paperCount = papersQuery.data?.length;
  useEffect(() => {
    if (paperCount !== 0 || searchParams.has("add")) return;
    const next = new URLSearchParams(searchParams);
    next.set("add", "upload");
    setSearchParams(next, { replace: true });
  }, [paperCount, searchParams, setSearchParams]);

  const refreshLibraries = () => {
    void queryClient.invalidateQueries({ queryKey: ["papers", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["library-papers"] });
  };
  const refreshProject = () => {
    void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["projects"] });
  };

  const uploadMutation = useMutation({
    mutationFn: (input: { file: File; key: string }) => {
      const form = new FormData();
      form.append("file", input.file);
      return apiFetch<UploadResult>(`/api/v1/projects/${projectId}/paper-files`, {
        method: "POST",
        headers: { "Idempotency-Key": input.key },
        body: form,
      });
    },
    onSuccess: (result) => {
      setLastUpload(result);
      setFile(null);
      setIntent(null);
      refreshLibraries();
    },
  });
  const addMutation = useMutation({
    mutationFn: (paper: PaperListItem) =>
      apiFetch<ProjectPaperResult>(`/api/v1/projects/${projectId}/papers`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          paper_id: paper.paper_id,
          version_id: paper.version.version_id,
        }),
      }),
    onSuccess: refreshLibraries,
  });
  const arxivSearchMutation = useMutation({
    mutationFn: (query: string) => {
      const params = new URLSearchParams({ q: query, max_results: "10" });
      return apiFetch<ArxivSearchResult[]>(
        `/api/v1/projects/${projectId}/arxiv/search?${params.toString()}`,
      );
    },
  });
  const arxivImportMutationKey = ["project-arxiv-import", projectId] as const;
  const arxivImportMutation = useMutation({
    mutationKey: arxivImportMutationKey,
    mutationFn: ({ paper, key }: ArxivImportVariables) =>
      apiFetch<UploadResult>(`/api/v1/projects/${projectId}/arxiv/import`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": key,
        },
        body: JSON.stringify({ versioned_arxiv_id: paper.versioned_id }),
      }),
    onSuccess: (result) => {
      setLastArxivImport(result);
      refreshLibraries();
    },
  });
  const pendingArxivImportIds = useMutationState({
    filters: { mutationKey: arxivImportMutationKey, status: "pending" },
    select: (mutation) =>
      (mutation.state.variables as ArxivImportVariables | undefined)?.paper.versioned_id,
  });
  const removeMutation = useMutation({
    mutationFn: (paperId: string) =>
      apiFetch<void>(`/api/v1/projects/${projectId}/papers/${paperId}`, {
        method: "DELETE",
      }),
    onSuccess: (_, paperId) => {
      setSelection((current) =>
        current.paperIds.includes(paperId) ? toggleScopePaper(current, paperId) : current,
      );
      refreshLibraries();
    },
  });
  const paperArchiveMutation = useMutation({
    mutationFn: ({ paperId, restore }: { paperId: string; restore: boolean }) =>
      apiFetch<{ paper_id: string; archived_at: string | null }>(
        `/api/v1/library/papers/${paperId}/${restore ? "restore" : "archive"}`,
        { method: "POST" },
      ),
    onSuccess: refreshLibraries,
  });
  const updateMutation = useMutation({
    mutationFn: () =>
      apiFetch<Project>(`/api/v1/projects/${projectId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: projectName.trim(),
          description: projectDescription.trim(),
        }),
      }),
    onSuccess: () => {
      setEditing(false);
      refreshProject();
    },
  });
  const projectArchiveMutation = useMutation({
    mutationFn: (restore: boolean) =>
      apiFetch<Project>(`/api/v1/projects/${projectId}/${restore ? "restore" : "archive"}`, {
        method: "POST",
      }),
    onSuccess: refreshProject,
  });
  const onFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const selected = event.target.files?.[0] ?? null;
    setFile(selected);
    setLastUpload(null);
    uploadMutation.reset();
    setIntent(
      selected ? ensureUploadIntent(intent, selected, () => crypto.randomUUID()) : null,
    );
  };
  const onRename = (event: FormEvent) => {
    event.preventDefault();
    if (projectName.trim()) updateMutation.mutate();
  };
  const onArxivSearch = (event: FormEvent) => {
    event.preventDefault();
    const query = arxivQuery.trim();
    if (query) arxivSearchMutation.mutate(query);
  };

  const project = projectQuery.data;
  const archived = Boolean(project?.archived_at);
  const available =
    libraryQuery.data?.filter((paper) => !paper.project_ids.includes(projectId)) ?? [];
  const actionError =
    projectArchiveMutation.error ??
    updateMutation.error ??
    paperArchiveMutation.error;
  const toggleProjectArchive = () => {
    if (!archived && !window.confirm("归档后项目将变为只读，确定继续吗？")) return;
    projectArchiveMutation.mutate(archived);
  };
  const togglePaperArchive = (paper: PaperListItem) => {
    const restore = Boolean(paper.archived_at);
    if (!restore && !window.confirm("这会归档个人文献库中的对应资产，确定继续吗？")) return;
    paperArchiveMutation.mutate({ paperId: paper.paper_id, restore });
  };
  const removePaper = (paperId: string) => {
    if (!window.confirm("确定将这篇文献移出当前项目吗？个人文献库中的资产会保留。")) return;
    removeMutation.mutate(paperId);
  };

  if (projectQuery.isError) {
    return (
      <div className="page-flow">
        <PageBar breadcrumbs={[{ label: "研究项目", to: "/" }]} title="文献库" />
        <section className="notice">
          <p className="error-text">{errorMessage(projectQuery.error)}</p>
          <Link to="/">返回项目</Link>
        </section>
      </div>
    );
  }

  return (
    <div className="page-flow project-library-page">
      <PageBar
        breadcrumbs={[{ label: "研究项目", to: "/" }, { label: "文献库" }]}
        title={project?.name ?? "正在读取项目…"}
        actions={<div className="page-bar-action-group">
          {archived ? <span className="badge badge-warn">已归档</span> : null}
          {!archived && <button type="button" className="button-plain" onClick={() => setEditing((value) => !value)}>修改信息</button>}
          <button
            type="button"
            className={archived ? "button-quiet" : "button-plain project-archive-action"}
            disabled={projectArchiveMutation.isPending}
            onClick={toggleProjectArchive}
          >
            {archived ? "恢复项目" : "归档项目"}
          </button>
        </div>}
      />

      {archived && (
        <p className="readonly-note">该项目当前只读：历史问答、文献研究、研究会话与引用仍可查看，不能修改文献或创建新的研究执行。</p>
      )}

      {editing && !archived && (
        <form className="project-edit-form panel" onSubmit={onRename}>
          <label><span>项目名称</span><input value={projectName} onChange={(event) => setProjectName(event.target.value)} maxLength={200} required /></label>
          <label><span>研究说明</span><textarea value={projectDescription} onChange={(event) => setProjectDescription(event.target.value)} rows={3} maxLength={4000} /></label>
          <div><button type="submit" disabled={updateMutation.isPending || !projectName.trim()}>{updateMutation.isPending ? "保存中…" : "保存修改"}</button><button type="button" className="button-plain" onClick={() => setEditing(false)}>取消</button></div>
        </form>
      )}

      {actionError && <p className="notice error-text">{errorMessage(actionError)}</p>}

      <section className="section-block">
        <div className="section-title-row project-library-heading">
          <div>
            <p className="project-library-kicker">项目文献库</p>
            <h2>已收录文献</h2>
          </div>
          <span className="section-count">{papersQuery.data?.length ?? "—"} 篇</span>
        </div>
        <div className="library-chat-handoff">
          <p>{selection.paperIds.length > 0 ? `已选择 ${selection.paperIds.length} 篇文献，可直接带入文献问答。` : "勾选文献可进行单篇或多篇问答。"}</p>
          <div>
            <Link className="button-link project-library-primary-action" to={chatHomePath(projectId)}>询问整个项目</Link>
            {selection.paperIds.length > 0 ? (
              <Link
                className="button-link button-outline"
                to={chatPreselectionPath(projectId, selection.paperIds)}
              >
                询问选中（{selection.paperIds.length}）
              </Link>
            ) : null}
            <button
              type="button"
              className="button-quiet project-ingest-toggle"
              aria-expanded={ingestOpen}
              aria-controls="project-ingest-panel"
              disabled={archived}
              onClick={() => showIngestMode(ingestOpen ? null : "upload")}
            >
              {ingestOpen ? "收起添加" : "添加文献"}
              <span aria-hidden="true">{ingestOpen ? "−" : "+"}</span>
            </button>
          </div>
        </div>

        {ingestOpen ? (
          <section id="project-ingest-panel" className="project-ingest-panel" aria-label="添加文献">
            <div className="project-ingest-tabs" role="group" aria-label="添加方式">
              <button
                type="button"
                aria-pressed={ingestMode === "upload"}
                aria-controls="project-upload-panel"
                onClick={() => showIngestMode("upload")}
              >
                上传 PDF
              </button>
              <button
                type="button"
                aria-pressed={ingestMode === "reuse"}
                aria-controls="project-reuse-panel"
                onClick={() => showIngestMode("reuse")}
              >
                从个人文献库收录
              </button>
              <button
                type="button"
                aria-pressed={ingestMode === "search"}
                aria-controls="project-search-panel"
                onClick={() => showIngestMode("search")}
              >
                在线搜索论文
              </button>
            </div>
            {ingestMode === "upload" ? (
              <div id="project-upload-panel" className="project-ingest-content">
                <div className="project-ingest-copy">
                  <h3>上传 PDF</h3>
                  <p>系统会计算内容哈希；如有相同文件，将直接复用已有解析结果。</p>
                </div>
                <label className="file-drop"><input type="file" accept="application/pdf,.pdf" onChange={onFileChange} disabled={archived} /><span className="file-glyph" aria-hidden="true">PDF</span><strong>{file?.name ?? "选择一份 PDF"}</strong><small>{file ? formatSize(file.size) : archived ? "归档项目不可上传" : "支持点击选择"}</small></label>
                <button type="button" onClick={() => file && intent && uploadMutation.mutate({ file, key: intent.key })} disabled={archived || !file || !intent || uploadMutation.isPending}>{uploadMutation.isPending ? "正在提交…" : "导入到当前项目"}<span aria-hidden="true">→</span></button>
                {uploadMutation.isError && <p className="error-text">{errorMessage(uploadMutation.error)}</p>}
                {lastUpload && <UploadNotice result={lastUpload} projectId={projectId} />}
              </div>
            ) : ingestMode === "reuse" ? (
              <div id="project-reuse-panel" className="project-ingest-content">
                <div className="project-ingest-copy">
                  <h3>收录已有文献</h3>
                  <p>不再上传，也不重复解析。</p>
                </div>
                {libraryQuery.isPending && <p className="muted">正在检查个人文献库…</p>}
                {available.length === 0 && !libraryQuery.isPending && <div className="reuse-empty">暂无可收录的其他文献</div>}
                <div className="reuse-list">{available.map((paper) => <div key={paper.paper_id}><span><strong><PaperTitle paper={paper} /></strong><small title={paper.version.display_filename}>{paper.version.display_filename} · {paper.version.parse_ready ? "已解析" : "处理中"} · {formatSize(paper.version.size_bytes)}</small></span><button type="button" className="button-quiet" disabled={archived || addMutation.isPending} onClick={() => addMutation.mutate(paper)}>+收录</button></div>)}</div>
                {addMutation.isError && <p className="error-text">{errorMessage(addMutation.error)}</p>}
                <Link className="text-link" to="/library">查看完整个人文献库 →</Link>
              </div>
            ) : (
              <div id="project-search-panel" className="project-arxiv-panel">
                <div className="project-ingest-copy">
                  <h3>搜索并引入论文</h3>
                  <p>当前仅检索 arXiv。选择论文后，系统会下载官方 PDF，并进入同一套解析与索引流程。</p>
                </div>
                <form className="project-arxiv-search-form" onSubmit={onArxivSearch}>
                  <label htmlFor="project-arxiv-query">检索词</label>
                  <div>
                    <input
                      id="project-arxiv-query"
                      name="arxiv-query"
                      type="search"
                      autoComplete="off"
                      value={arxivQuery}
                      onChange={(event) => setArxivQuery(event.target.value)}
                      placeholder="例如：reinforcement learning path planning…"
                      disabled={archived || arxivSearchMutation.isPending}
                    />
                    <button
                      type="submit"
                      disabled={archived || !arxivQuery.trim() || arxivSearchMutation.isPending}
                    >
                      {arxivSearchMutation.isPending ? "正在搜索…" : "搜索 arXiv"}
                    </button>
                  </div>
                </form>
                {arxivSearchMutation.isError ? (
                  <p className="error-text">{errorMessage(arxivSearchMutation.error)}</p>
                ) : null}
                {arxivSearchMutation.data?.length === 0 ? (
                  <p className="reuse-empty">没有找到匹配论文，请尝试更具体或更简短的检索词。</p>
                ) : null}
                {arxivSearchMutation.data && arxivSearchMutation.data.length > 0 ? (
                  <ol className="project-arxiv-results">
                    {arxivSearchMutation.data.map((paper, index) => {
                      const importing = pendingArxivImportIds.includes(paper.versioned_id);
                      return (
                        <li key={paper.versioned_id}>
                          <span className="source-rank">{String(index + 1).padStart(2, "0")}</span>
                          <div>
                            <h4>{paper.title}</h4>
                            <p>{paper.authors.slice(0, 3).join("、")}{paper.authors.length > 3 ? " 等" : ""}</p>
                            <small>
                              arXiv {paper.versioned_id} · {ARXIV_DATE_FORMATTER.format(new Date(paper.published_at))} · {paper.categories.slice(0, 3).join(" / ")}
                            </small>
                            <details><summary>查看摘要</summary><p>{paper.abstract}</p></details>
                          </div>
                          <button
                            type="button"
                            className="button-quiet"
                            disabled={archived || importing}
                            onClick={() => arxivImportMutation.mutate({ paper, key: crypto.randomUUID() })}
                          >
                            {importing ? "正在引入…" : "引入项目"}
                          </button>
                        </li>
                      );
                    })}
                  </ol>
                ) : null}
                {arxivImportMutation.isError ? (
                  <p className="error-text">{errorMessage(arxivImportMutation.error)}</p>
                ) : null}
                {lastArxivImport ? <UploadNotice result={lastArxivImport} projectId={projectId} /> : null}
              </div>
            )}
          </section>
        ) : null}

        {papersQuery.isError && <p className="notice error-text">{errorMessage(papersQuery.error)}</p>}
        {papersQuery.data?.length === 0 && <p className="project-library-empty-note">当前项目还没有文献，请从上方选择一种方式添加。</p>}
        {papersQuery.data && papersQuery.data.length > 0 && (
          <div className="project-paper-list">{papersQuery.data.map((paper, index) => (
            <PaperRow
              key={paper.paper_id}
              paper={paper}
              index={index}
              projectId={projectId}
              archivedProject={archived}
              selected={selection.paperIds.includes(paper.paper_id)}
              onToggle={() => setSelection((current) => toggleScopePaper(current, paper.paper_id))}
              onAsk={() => navigate(chatPreselectionPath(projectId, [paper.paper_id]))}
              removing={removeMutation.isPending && removeMutation.variables === paper.paper_id}
              onRemove={() => removePaper(paper.paper_id)}
              onArchive={() => togglePaperArchive(paper)}
            />
          ))}</div>
        )}
        {removeMutation.isError && <p className="error-text">{errorMessage(removeMutation.error)}</p>}
      </section>

    </div>
  );
}

function UploadNotice({ result, projectId }: { result: UploadResult; projectId: string }) {
  if (result.run_id) return <p className="result-note"><strong>{result.reused ? "已复用处理中的文献" : "已创建导入任务"}</strong><Link to={`/runs/${result.run_id}`}>查看进度 →</Link></p>;
  return <p className="result-note"><strong>{result.already_added ? "该文献已在当前项目中" : "已复用完成的解析结果"}</strong><Link to={`/projects/${projectId}/versions/${result.version_id}/document`}>查看文档 →</Link></p>;
}

interface PaperRowProps {
  paper: PaperListItem;
  index: number;
  projectId: string;
  archivedProject: boolean;
  selected: boolean;
  removing: boolean;
  onToggle: () => void;
  onAsk: () => void;
  onRemove: () => void;
  onArchive: () => void;
}

function PaperRow({ paper, index, projectId, archivedProject, selected, removing, onToggle, onAsk, onRemove, onArchive }: PaperRowProps) {
  const indexQuery = useQuery({
    queryKey: ["index-status", projectId, paper.version.version_id],
    queryFn: () => apiFetch<IndexStatus>(`/api/v1/projects/${projectId}/paper-versions/${paper.version.version_id}/index-status`),
    enabled: paper.version.parse_ready,
    refetchInterval: (query) => query.state.data?.chunk_set?.status === "ready" ? false : 3000,
    retry: false,
  });
  const indexReady = indexQuery.data?.chunk_set?.status === "ready";
  const indexText = !paper.version.parse_ready ? "等待解析" : indexReady ? "索引已就绪" : indexQuery.data?.chunk_set ? "正在索引" : "等待索引";
  const paperArchived = Boolean(paper.archived_at);

  return (
    <article className={`project-paper-row ${paperArchived ? "archived-row" : ""}`}>
      <label className="paper-select" title="选择用于多篇问答"><input type="checkbox" checked={selected} onChange={onToggle} disabled={archivedProject || paperArchived} /><span>{String(index + 1).padStart(2, "0")}</span></label>
      <div className="paper-identity"><div className="identity-title"><h3><PaperTitle paper={paper} /></h3>{paperArchived && <span className="badge badge-warn">个人库已归档</span>}</div><p><span className="paper-filename" title={paper.version.display_filename}>{paper.version.display_filename}</span><span>{formatSize(paper.version.size_bytes)}</span><span className="mono">VER {paper.version.version_id.slice(0, 8)}</span></p></div>
      <div className="paper-state"><span className={`status-dot ${indexReady ? "ready" : "working"}`} /><span>{indexText}</span>{indexQuery.data?.indexing_run_id && !indexReady && <Link to={`/runs/${indexQuery.data.indexing_run_id}`}>查看索引 Run</Link>}</div>
      <div className="paper-actions">
        <button className="button-ask-inline" type="button" disabled={archivedProject || paperArchived} onClick={onAsk}>询问此篇</button>
        <a href={`/api/v1/projects/${projectId}/paper-versions/${paper.version.version_id}/file`} target="_blank" rel="noreferrer">原文</a>
        {paper.version.parse_ready && <Link to={`/projects/${projectId}/versions/${paper.version.version_id}/document`}>结构预览</Link>}
        <details className="paper-more-actions">
          <summary>更多</summary>
          <div className="paper-more-menu">
            <button className="button-text-warn" type="button" disabled={archivedProject} onClick={onArchive}>{paperArchived ? "恢复个人库资产" : "归档个人库资产"}</button>
            <button className="button-text-danger" type="button" disabled={archivedProject || removing} onClick={onRemove}>{removing ? "移除中…" : "移出项目"}</button>
          </div>
        </details>
      </div>
    </article>
  );
}
