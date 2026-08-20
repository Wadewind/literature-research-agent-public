/** Project 工作台：项目与个人文献库的入口。 */

import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiFetch, errorMessage } from "../api/client";
import type { PaperListItem, Project } from "../api/types";

export default function ProjectsPage() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: () => apiFetch<Project[]>("/api/v1/projects") });
  const libraryQuery = useQuery({ queryKey: ["library-papers"], queryFn: () => apiFetch<PaperListItem[]>("/api/v1/library/papers") });
  const createMutation = useMutation({
    mutationFn: (input: { name: string; description: string }) => apiFetch<Project>("/api/v1/projects", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) }),
    onSuccess: () => { setName(""); setDescription(""); void queryClient.invalidateQueries({ queryKey: ["projects"] }); },
  });
  const onSubmit = (event: FormEvent) => { event.preventDefault(); if (name.trim()) createMutation.mutate({ name: name.trim(), description: description.trim() }); };
  const projects = projectsQuery.data ?? [];
  const papers = libraryQuery.data ?? [];

  return <div className="page-flow">
    <section className="hero-grid">
      <div className="hero-copy"><p className="eyebrow">RESEARCH WORKSPACE / 01</p><h1>把文献变成<br /><em>可追溯的证据</em></h1><p className="hero-lead">在 Project 中组织论文，后台可恢复地解析 PDF，并保留页码与结构定位。</p></div>
      <aside className="workspace-summary" aria-label="工作台摘要"><p>当前工作台</p><div><strong>{projects.length}</strong><span>Projects</span></div><div><strong>{papers.length}</strong><span>Unique papers</span></div><Link to="/library">查看个人文献库 <span aria-hidden="true">→</span></Link></aside>
    </section>
    <section className="section-block">
      <div className="section-title-row"><div><p className="eyebrow">COLLECTIONS</p><h2>研究项目</h2></div><span className="section-count">{String(projects.length).padStart(2, "0")}</span></div>
      {projectsQuery.isError && <p className="notice error-text">{errorMessage(projectsQuery.error)}</p>}
      {projectsQuery.isPending && <div className="skeleton-block">正在读取项目…</div>}
      {projects.length > 0 && <div className="project-grid">{projects.map((project, index) => {
        const count = papers.filter((paper) => paper.project_ids.includes(project.project_id)).length;
        return <Link className="project-card" to={`/projects/${project.project_id}`} key={project.project_id}><span className="project-number">{String(index + 1).padStart(2, "0")}</span><div><h3>{project.name}</h3><p>{project.description || "尚未添加研究说明"}</p></div><footer><span>{count} 篇文献</span><span aria-hidden="true">↗</span></footer></Link>;
      })}</div>}
    </section>
    <section className="create-project"><div><p className="eyebrow">NEW COLLECTION</p><h2>开始一个新课题</h2><p>只需一个名称。文献可以随后上传，也可从个人文献库复用。</p></div><form onSubmit={onSubmit} className="create-form"><label><span>项目名称</span><input value={name} onChange={(e) => setName(e.target.value)} placeholder="例：大模型事实性评估" maxLength={255} required /></label><label><span>研究说明 <small>可选</small></span><textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="记录问题、范围或筛选标准" rows={3} /></label><button type="submit" disabled={createMutation.isPending || !name.trim()}>{createMutation.isPending ? "正在创建…" : "创建 Project"}<span aria-hidden="true">→</span></button>{createMutation.isError && <p className="error-text">{errorMessage(createMutation.error)}</p>}</form></section>
  </div>;
}
