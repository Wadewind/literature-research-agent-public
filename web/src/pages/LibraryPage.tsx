/** Project 工作台：文献、Conversation 入口、索引状态与归档管理。 */

import { useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import { apiFetch, errorMessage } from "../api/client";
import type {
  IndexStatus,
  PaperListItem,
  Project,
  ProjectPaperResult,
  UploadResult,
} from "../api/types";
import { createScopeSelection, toggleScopePaper, type ScopeSelection } from "../conversations/scopeSelection";
import { ensureUploadIntent, type UploadIntent } from "../library/uploadIntent";
import ProjectWorkspaceHeader from "../components/ProjectWorkspaceHeader";
import { chatHomePath, chatPreselectionPath } from "../workspace/projectWorkspace";

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export default function LibraryPage() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [intent, setIntent] = useState<UploadIntent | null>(null);
  const [lastUpload, setLastUpload] = useState<UploadResult | null>(null);
  const [selection, setSelection] = useState<ScopeSelection>(createScopeSelection);
  const [editing, setEditing] = useState(false);
  const [projectName, setProjectName] = useState("");
  const [projectDescription, setProjectDescription] = useState("");

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

  const project = projectQuery.data;
  const archived = Boolean(project?.archived_at);
  const available =
    libraryQuery.data?.filter((paper) => !paper.project_ids.includes(projectId)) ?? [];
  const actionError =
    projectArchiveMutation.error ??
    updateMutation.error ??
    paperArchiveMutation.error;

  if (projectQuery.isError) {
    return (
      <section className="notice">
        <p className="error-text">{errorMessage(projectQuery.error)}</p>
        <Link to="/">返回项目</Link>
      </section>
    );
  }

  return (
    <div className="page-flow">
      <ProjectWorkspaceHeader
        projectId={projectId}
        project={project}
        active="library"
        eyebrow="文献库"
        description={project?.description || "集中管理本课题三种研究模式共享的文献、解析结果与固定索引。"}
        actions={<div className="project-heading-actions">
          <div className="metric-block"><strong>{papersQuery.data?.length ?? "—"}</strong><span>已收录</span></div>
          <button type="button" className="button-quiet" disabled={projectArchiveMutation.isPending} onClick={() => projectArchiveMutation.mutate(archived)}>
            {archived ? "恢复 Project" : "归档 Project"}
          </button>
          {!archived && <button type="button" className="button-plain" onClick={() => setEditing((value) => !value)}>修改信息</button>}
        </div>}
      />

      {archived && (
        <p className="readonly-note">该 Project 当前只读：历史问答、综述、研究会话与引用仍可查看，不能修改文献或创建新的研究执行。</p>
      )}

      <section className="project-mode-entry-grid" aria-label="Project 研究模式">
        <Link to={chatHomePath(projectId)}><span>文献问答</span><strong>针对固定范围提出可引用的问题</strong><small>Claim → Citation → Evidence</small></Link>
        <Link to={`/projects/${projectId}/reviews`}><span>综述</span><strong>按固定 Workflow 聚合证据与章节</strong><small>Evidence Matrix → Artifact</small></Link>
        <Link to={`/projects/${projectId}/agent`}><span>研究助手</span><strong>持续分析项目材料与研究上下文</strong><small>Session → Turn → Candidate</small></Link>
      </section>

      {editing && !archived && (
        <form className="project-edit-form panel" onSubmit={onRename}>
          <label><span>项目名称</span><input value={projectName} onChange={(event) => setProjectName(event.target.value)} maxLength={200} required /></label>
          <label><span>研究说明</span><textarea value={projectDescription} onChange={(event) => setProjectDescription(event.target.value)} rows={3} maxLength={4000} /></label>
          <div><button type="submit" disabled={updateMutation.isPending || !projectName.trim()}>{updateMutation.isPending ? "保存中…" : "保存修改"}</button><button type="button" className="button-plain" onClick={() => setEditing(false)}>取消</button></div>
        </form>
      )}

      {actionError && <p className="notice error-text">{errorMessage(actionError)}</p>}

      <section className="ingest-grid" aria-disabled={archived}>
        <div className="ingest-panel primary-ingest">
          <p className="eyebrow">UPLOAD / AUTO REUSE</p><h2>上传 PDF</h2><p>系统会计算内容哈希。若个人文献库已有相同文件，将直接复用解析结果。</p>
          <label className="file-drop"><input type="file" accept="application/pdf,.pdf" onChange={onFileChange} disabled={archived} /><span className="file-glyph" aria-hidden="true">PDF</span><strong>{file?.name ?? "选择一份 PDF"}</strong><small>{file ? formatSize(file.size) : archived ? "归档 Project 不可上传" : "支持点击选择"}</small></label>
          <button type="button" onClick={() => file && intent && uploadMutation.mutate({ file, key: intent.key })} disabled={archived || !file || !intent || uploadMutation.isPending}>{uploadMutation.isPending ? "正在提交…" : "导入到当前项目"}<span aria-hidden="true">→</span></button>
          {uploadMutation.isError && <p className="error-text">{errorMessage(uploadMutation.error)}</p>}
          {lastUpload && <UploadNotice result={lastUpload} projectId={projectId} />}
        </div>
        <div className="ingest-panel reuse-panel">
          <p className="eyebrow">FROM PERSONAL LIBRARY</p><h2>收录已有文献</h2><p>不再上传，也不重复解析。</p>
          {libraryQuery.isPending && <p className="muted">正在检查个人文献库…</p>}
          {available.length === 0 && !libraryQuery.isPending && <div className="reuse-empty">暂无可收录的其他文献</div>}
          <div className="reuse-list">{available.map((paper) => <div key={paper.paper_id}><span><strong>{paper.version.display_filename}</strong><small>{paper.version.parse_ready ? "已解析" : "处理中"} · {formatSize(paper.version.size_bytes)}</small></span><button type="button" className="button-quiet" disabled={archived || addMutation.isPending} onClick={() => addMutation.mutate(paper)}>+收录</button></div>)}</div>
          {addMutation.isError && <p className="error-text">{errorMessage(addMutation.error)}</p>}
          <Link className="text-link" to="/library">查看完整个人文献库 →</Link>
        </div>
      </section>

      <section className="section-block">
        <div className="section-title-row"><div><p className="eyebrow">EVIDENCE SOURCES</p><h2>已收录文献</h2></div><span className="section-count">{String(papersQuery.data?.length ?? 0).padStart(2, "0")}</span></div>
        <div className="library-chat-handoff">
          <p>{selection.paperIds.length > 0 ? `已选择 ${selection.paperIds.length} 篇文献，可带入文献问答继续确认范围。` : "勾选论文可带入单篇或多篇文献问答；不选择则使用整个 Project。"}</p>
          <div>
            <Link className="button-link" to={chatHomePath(projectId)}>询问整个 Project</Link>
            <Link
              className={`button-link button-outline ${selection.paperIds.length === 0 ? "disabled" : ""}`}
              aria-disabled={selection.paperIds.length === 0}
              tabIndex={selection.paperIds.length === 0 ? -1 : undefined}
              to={selection.paperIds.length > 0 ? chatPreselectionPath(projectId, selection.paperIds) : chatHomePath(projectId)}
            >
              询问选中（{selection.paperIds.length}）
            </Link>
          </div>
        </div>
        {papersQuery.isError && <p className="notice error-text">{errorMessage(papersQuery.error)}</p>}
        {papersQuery.data?.length === 0 && <div className="empty-state compact"><h3>这个项目还没有文献</h3><p>上传新 PDF，或从右侧收录个人文献库中的已有文献。</p></div>}
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
              onRemove={() => removeMutation.mutate(paper.paper_id)}
              onArchive={() => paperArchiveMutation.mutate({ paperId: paper.paper_id, restore: Boolean(paper.archived_at) })}
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
      <div className="paper-identity"><div className="identity-title"><h3>{paper.version.display_filename}</h3>{paperArchived && <span className="badge badge-warn">个人库已归档</span>}</div><p><span>{formatSize(paper.version.size_bytes)}</span><span className="mono">VER {paper.version.version_id.slice(0, 8)}</span></p></div>
      <div className="paper-state"><span className={`status-dot ${indexReady ? "ready" : "working"}`} /><span>{indexText}</span>{indexQuery.data?.indexing_run_id && !indexReady && <Link to={`/runs/${indexQuery.data.indexing_run_id}`}>查看索引 Run</Link>}</div>
      <div className="paper-actions"><button className="button-ask-inline" type="button" disabled={archivedProject || paperArchived} onClick={onAsk}>询问此篇</button><a href={`/api/v1/projects/${projectId}/paper-versions/${paper.version.version_id}/file`} target="_blank" rel="noreferrer">原文</a>{paper.version.parse_ready && <Link to={`/projects/${projectId}/versions/${paper.version.version_id}/document`}>结构预览</Link>}<button className="button-text-warn" type="button" disabled={archivedProject} onClick={onArchive}>{paperArchived ? "恢复个人库资产" : "归档个人库资产"}</button><button className="button-text-danger" type="button" disabled={archivedProject || removing} onClick={onRemove}>{removing ? "移除中" : "移出项目"}</button></div>
    </article>
  );
}
