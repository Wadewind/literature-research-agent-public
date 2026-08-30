/** Project 工作台：创建、浏览、归档与恢复。 */

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiFetch, errorMessage } from "../api/client";
import type { PaperListItem, Project } from "../api/types";
import PageBar from "../components/PageBar";
import { shouldAutoOpenCreateModal } from "../projects/createModal";

export default function ProjectsPage() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const autoOpenedRef = useRef(false);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const nameInputRef = useRef<HTMLInputElement>(null);
  // 打开时挂载为模态并聚焦名称输入框，关闭时收起
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (createOpen && !dialog.open) {
      dialog.showModal();
      nameInputRef.current?.focus();
    } else if (!createOpen && dialog.open) {
      dialog.close();
    }
  }, [createOpen]);
  const projectsQuery = useQuery({
    queryKey: ["projects", includeArchived],
    queryFn: () =>
      apiFetch<Project[]>(`/api/v1/projects?include_archived=${includeArchived}`),
  });
  const libraryQuery = useQuery({
    queryKey: ["library-papers", "with-archived"],
    queryFn: () => apiFetch<PaperListItem[]>("/api/v1/library/papers?include_archived=true"),
  });
  const createMutation = useMutation({
    mutationFn: (input: { name: string; description: string }) =>
      apiFetch<Project>("/api/v1/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      setName("");
      setDescription("");
      setCreateOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });
  const archiveMutation = useMutation({
    mutationFn: ({ projectId, restore }: { projectId: string; restore: boolean }) =>
      apiFetch<Project>(`/api/v1/projects/${projectId}/${restore ? "restore" : "archive"}`, {
        method: "POST",
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["projects"] }),
  });
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (name.trim()) {
      createMutation.mutate({ name: name.trim(), description: description.trim() });
    }
  };
  const projects = projectsQuery.data ?? [];
  const papers = libraryQuery.data ?? [];
  const activeProjectCount = projects.filter((project) => !project.archived_at).length;
  // 空态首次进入自动打开创建 Modal 一次，承担首次引导
  useEffect(() => {
    if (shouldAutoOpenCreateModal(autoOpenedRef.current, projects.length, projectsQuery.isPending)) {
      autoOpenedRef.current = true;
      setCreateOpen(true);
    }
  }, [projectsQuery.isPending, projects.length]);

  return (
    <div className="page-flow">
      <PageBar
        title="研究项目"
        actions={<div className="page-bar-action-group"><span className="page-bar-stat"><strong>{activeProjectCount}</strong> 个活跃项目</span><Link className="page-bar-link" to="/library">{papers.length} 篇个人文献</Link></div>}
      />
      <section className="section-block">
        <div className="projects-toolbar section-tools"><label><input type="checkbox" checked={includeArchived} onChange={(event) => setIncludeArchived(event.target.checked)} />显示已归档</label></div>
        {projectsQuery.isError && <p className="notice error-text">{errorMessage(projectsQuery.error)}</p>}
        {projectsQuery.isPending && <div className="skeleton-block">正在读取项目…</div>}
        {!projectsQuery.isPending && <div className="project-grid">{projects.map((project, index) => {
          const count = papers.filter((paper) => paper.project_ids.includes(project.project_id)).length;
          const archived = Boolean(project.archived_at);
          return <article className={`project-card ${archived ? "archived-card" : ""}`} key={project.project_id}><div className="project-card-top"><span className="project-card-flags"><span className="project-number">{String(index + 1).padStart(2, "0")}</span>{archived && <span className="badge badge-warn">已归档</span>}</span><button type="button" className="button-plain project-card-archive" disabled={archiveMutation.isPending} onClick={() => archiveMutation.mutate({ projectId: project.project_id, restore: archived })}>{archived ? "恢复" : "归档"}</button></div><div><h3><Link className="project-card-link" to={`/projects/${project.project_id}`}>{project.name}</Link></h3><p>{project.description || "尚未添加研究说明"}</p></div><footer><span>{count} 篇文献</span><span>{new Date(project.updated_at).toLocaleDateString()} 更新</span></footer></article>;
        })}<button type="button" className="project-card-ghost" aria-expanded={createOpen} onClick={() => setCreateOpen(true)}><span className="project-card-ghost-mark" aria-hidden="true">＋</span><span>新建项目</span></button></div>}
        {archiveMutation.isError && <p className="error-text">{errorMessage(archiveMutation.error)}</p>}
      </section>
      <dialog
        ref={dialogRef}
        className="create-dialog"
        aria-labelledby="create-dialog-title"
        onClose={() => setCreateOpen(false)}
        onClick={(event) => {
          // 点击遮罩区域时事件目标为 dialog 本身，据此关闭
          if (event.target === dialogRef.current) setCreateOpen(false);
        }}
      >
        <form onSubmit={onSubmit} className="create-dialog-form">
          <div className="create-dialog-head">
            <div>
              <p className="eyebrow">NEW COLLECTION</p>
              <h2 id="create-dialog-title">开始一个新课题</h2>
              <p className="create-dialog-subtitle">只需一个名称。文献可以随后上传，也可从个人文献库复用。</p>
            </div>
            <button type="button" className="create-dialog-close" aria-label="关闭" onClick={() => setCreateOpen(false)}>×</button>
          </div>
          <label><span>项目名称</span><input ref={nameInputRef} value={name} onChange={(event) => setName(event.target.value)} placeholder="例：大模型事实性评估" maxLength={200} required /></label>
          <label><span>研究说明 <small>可选</small></span><textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="记录问题、范围或筛选标准" rows={3} maxLength={4000} /></label>
          <button type="submit" disabled={createMutation.isPending || !name.trim()}>{createMutation.isPending ? "正在创建…" : "创建 Project"}<span aria-hidden="true">→</span></button>
          {createMutation.isError && <p className="error-text">{errorMessage(createMutation.error)}</p>}
        </form>
      </dialog>
    </div>
  );
}
