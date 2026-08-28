/** Project 工作台：创建、浏览、归档与恢复。 */

import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiFetch, errorMessage } from "../api/client";
import type { PaperListItem, Project } from "../api/types";
import PageBar from "../components/PageBar";

export default function ProjectsPage() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [includeArchived, setIncludeArchived] = useState(false);
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

  return (
    <div className="page-flow">
      <PageBar
        title="研究项目"
        actions={<div className="page-bar-action-group"><span className="page-bar-stat"><strong>{activeProjectCount}</strong> 个活跃项目</span><Link className="page-bar-link" to="/library">{papers.length} 篇个人文献</Link></div>}
      />
      <section className="section-block">
        <div className="section-title-row"><div><p className="eyebrow">COLLECTIONS</p><h2>项目列表</h2></div><div className="section-tools"><label><input type="checkbox" checked={includeArchived} onChange={(event) => setIncludeArchived(event.target.checked)} />显示已归档</label><span className="section-count">{String(projects.length).padStart(2, "0")}</span></div></div>
        {projectsQuery.isError && <p className="notice error-text">{errorMessage(projectsQuery.error)}</p>}
        {projectsQuery.isPending && <div className="skeleton-block">正在读取项目…</div>}
        {projects.length > 0 && <div className="project-grid">{projects.map((project, index) => {
          const count = papers.filter((paper) => paper.project_ids.includes(project.project_id)).length;
          const archived = Boolean(project.archived_at);
          return <article className={`project-card ${archived ? "archived-card" : ""}`} key={project.project_id}><div className="project-card-top"><span className="project-number">{String(index + 1).padStart(2, "0")}</span>{archived && <span className="badge badge-warn">已归档</span>}</div><div><h3><Link to={`/projects/${project.project_id}`}>{project.name}</Link></h3><p>{project.description || "尚未添加研究说明"}</p></div><footer><span>{count} 篇文献</span><button type="button" className="button-plain" disabled={archiveMutation.isPending} onClick={() => archiveMutation.mutate({ projectId: project.project_id, restore: archived })}>{archived ? "恢复" : "归档"}</button></footer></article>;
        })}</div>}
        {archiveMutation.isError && <p className="error-text">{errorMessage(archiveMutation.error)}</p>}
      </section>
      <section className="create-project"><div><p className="eyebrow">NEW COLLECTION</p><h2>开始一个新课题</h2><p>只需一个名称。文献可以随后上传，也可从个人文献库复用。</p></div><form onSubmit={onSubmit} className="create-form"><label><span>项目名称</span><input value={name} onChange={(event) => setName(event.target.value)} placeholder="例：大模型事实性评估" maxLength={200} required /></label><label><span>研究说明 <small>可选</small></span><textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="记录问题、范围或筛选标准" rows={3} maxLength={4000} /></label><button type="submit" disabled={createMutation.isPending || !name.trim()}>{createMutation.isPending ? "正在创建…" : "创建 Project"}<span aria-hidden="true">→</span></button>{createMutation.isError && <p className="error-text">{errorMessage(createMutation.error)}</p>}</form></section>
    </div>
  );
}
