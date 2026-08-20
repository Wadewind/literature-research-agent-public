/** Project 文献库：上传新 PDF、复用已解析文献、移除收录关系。 */

import { useState, type ChangeEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { apiFetch, errorMessage } from "../api/client";
import type { PaperListItem, Project, ProjectPaperResult, UploadResult } from "../api/types";
import { ensureUploadIntent, type UploadIntent } from "../library/uploadIntent";

function formatSize(bytes: number): string { if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`; return `${Math.max(1, Math.round(bytes / 1024))} KB`; }

export default function LibraryPage() {
  const { projectId = "" } = useParams(); const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null); const [intent, setIntent] = useState<UploadIntent | null>(null); const [lastUpload, setLastUpload] = useState<UploadResult | null>(null);
  const projectQuery = useQuery({ queryKey: ["project", projectId], queryFn: () => apiFetch<Project>(`/api/v1/projects/${projectId}`) });
  const papersQuery = useQuery({ queryKey: ["papers", projectId], queryFn: () => apiFetch<PaperListItem[]>(`/api/v1/projects/${projectId}/papers`), refetchInterval: (query) => query.state.data?.some((p) => !p.version.parse_ready) ? 3000 : false });
  const libraryQuery = useQuery({ queryKey: ["library-papers"], queryFn: () => apiFetch<PaperListItem[]>("/api/v1/library/papers") });
  const refreshLibraries = () => { void queryClient.invalidateQueries({ queryKey: ["papers", projectId] }); void queryClient.invalidateQueries({ queryKey: ["library-papers"] }); };
  const uploadMutation = useMutation({ mutationFn: (input: { file: File; key: string }) => { const form = new FormData(); form.append("file", input.file); return apiFetch<UploadResult>(`/api/v1/projects/${projectId}/paper-files`, { method: "POST", headers: { "Idempotency-Key": input.key }, body: form }); }, onSuccess: (result) => { setLastUpload(result); setFile(null); setIntent(null); refreshLibraries(); } });
  const addMutation = useMutation({ mutationFn: (paper: PaperListItem) => apiFetch<ProjectPaperResult>(`/api/v1/projects/${projectId}/papers`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ paper_id: paper.paper_id, version_id: paper.version.version_id }) }), onSuccess: refreshLibraries });
  const removeMutation = useMutation({ mutationFn: (paperId: string) => apiFetch<void>(`/api/v1/projects/${projectId}/papers/${paperId}`, { method: "DELETE" }), onSuccess: refreshLibraries });
  const onFileChange = (event: ChangeEvent<HTMLInputElement>) => { const selected = event.target.files?.[0] ?? null; setFile(selected); setLastUpload(null); uploadMutation.reset(); setIntent(selected ? ensureUploadIntent(intent, selected, () => crypto.randomUUID()) : null); };
  const available = libraryQuery.data?.filter((paper) => !paper.project_ids.includes(projectId)) ?? [];
  if (projectQuery.isError) return <section className="notice"><p className="error-text">{errorMessage(projectQuery.error)}</p><Link to="/">返回项目</Link></section>;

  return <div className="page-flow">
    <header className="project-heading"><div><p className="breadcrumb"><Link to="/">研究项目</Link><span>/</span>文献库</p><p className="eyebrow">PROJECT LIBRARY</p><h1>{projectQuery.data?.name ?? "正在读取…"}</h1><p>{projectQuery.data?.description || "集中管理本课题需要的文献与解析结果。"}</p></div><div className="metric-block"><strong>{papersQuery.data?.length ?? "—"}</strong><span>已收录</span></div></header>
    <section className="ingest-grid">
      <div className="ingest-panel primary-ingest"><p className="eyebrow">UPLOAD / AUTO REUSE</p><h2>上传 PDF</h2><p>系统会计算内容哈希。若个人文献库已有相同文件，将直接复用解析结果。</p><label className="file-drop"><input type="file" accept="application/pdf,.pdf" onChange={onFileChange} /><span className="file-glyph" aria-hidden="true">PDF</span><strong>{file?.name ?? "选择一份 PDF"}</strong><small>{file ? formatSize(file.size) : "支持点击选择"}</small></label><button type="button" onClick={() => file && intent && uploadMutation.mutate({ file, key: intent.key })} disabled={!file || !intent || uploadMutation.isPending}>{uploadMutation.isPending ? "正在提交…" : "导入到当前项目"}<span aria-hidden="true">→</span></button>{uploadMutation.isError && <p className="error-text">{errorMessage(uploadMutation.error)}</p>}{lastUpload && <UploadNotice result={lastUpload} projectId={projectId} />}</div>
      <div className="ingest-panel reuse-panel"><p className="eyebrow">FROM PERSONAL LIBRARY</p><h2>收录已有文献</h2><p>不再上传，也不重复解析。</p>{libraryQuery.isPending && <p className="muted">正在检查个人文献库…</p>}{available.length === 0 && !libraryQuery.isPending && <div className="reuse-empty">暂无可收录的其他文献</div>}<div className="reuse-list">{available.map((paper) => <div key={paper.paper_id}><span><strong>{paper.version.display_filename}</strong><small>{paper.version.parse_ready ? "已解析" : "处理中"} · {formatSize(paper.version.size_bytes)}</small></span><button type="button" className="button-quiet" disabled={addMutation.isPending} onClick={() => addMutation.mutate(paper)}>+收录</button></div>)}</div>{addMutation.isError && <p className="error-text">{errorMessage(addMutation.error)}</p>}<Link className="text-link" to="/library">查看完整个人文献库 →</Link></div>
    </section>
    <section className="section-block"><div className="section-title-row"><div><p className="eyebrow">EVIDENCE SOURCES</p><h2>已收录文献</h2></div><span className="section-count">{String(papersQuery.data?.length ?? 0).padStart(2, "0")}</span></div>{papersQuery.isError && <p className="notice error-text">{errorMessage(papersQuery.error)}</p>}{papersQuery.data?.length === 0 && <div className="empty-state compact"><h3>这个项目还没有文献</h3><p>上传新 PDF，或从右侧收录个人文献库中的已有文献。</p></div>}{papersQuery.data && papersQuery.data.length > 0 && <div className="project-paper-list">{papersQuery.data.map((paper, index) => <PaperRow key={paper.paper_id} paper={paper} index={index} projectId={projectId} removing={removeMutation.isPending && removeMutation.variables === paper.paper_id} onRemove={() => removeMutation.mutate(paper.paper_id)} />)}</div>}{removeMutation.isError && <p className="error-text">{errorMessage(removeMutation.error)}</p>}</section>
  </div>;
}

function UploadNotice({ result, projectId }: { result: UploadResult; projectId: string }) {
  if (result.run_id) return <p className="result-note"><strong>{result.reused ? "已复用处理中的文献" : "已创建导入任务"}</strong><Link to={`/runs/${result.run_id}`}>查看进度 →</Link></p>;
  return <p className="result-note"><strong>{result.already_added ? "该文献已在当前项目中" : "已复用完成的解析结果"}</strong><Link to={`/projects/${projectId}/versions/${result.version_id}/document`}>查看文档 →</Link></p>;
}

function PaperRow({ paper, index, projectId, removing, onRemove }: { paper: PaperListItem; index: number; projectId: string; removing: boolean; onRemove: () => void }) {
  return <article className="project-paper-row"><span className="paper-index">{String(index + 1).padStart(2, "0")}</span><div className="paper-identity"><h3>{paper.version.display_filename}</h3><p><span>{formatSize(paper.version.size_bytes)}</span><span className="mono">VER {paper.version.version_id.slice(0, 8)}</span></p></div><div className="paper-state"><span className={`status-dot ${paper.version.parse_ready ? "ready" : "working"}`} /><span>{paper.version.parse_ready ? "结构已就绪" : "后台处理中"}</span>{!paper.version.parse_ready && paper.version.ingestion_run_id && <Link to={`/runs/${paper.version.ingestion_run_id}`}>查看 Run</Link>}</div><div className="paper-actions"><a href={`/api/v1/projects/${projectId}/paper-versions/${paper.version.version_id}/file`} target="_blank" rel="noreferrer">原文</a>{paper.version.parse_ready && <Link to={`/projects/${projectId}/versions/${paper.version.version_id}/document`}>结构预览</Link>}<button className="button-text-danger" type="button" disabled={removing} onClick={onRemove}>{removing ? "移除中" : "移出项目"}</button></div></article>;
}
