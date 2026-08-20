/** Project 列表与创建页。 */

import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiFetch, errorMessage } from "../api/client";
import type { Project } from "../api/types";

export default function ProjectsPage() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const projectsQuery = useQuery({
    queryKey: ["projects"],
    queryFn: () => apiFetch<Project[]>("/api/v1/projects"),
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

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    createMutation.mutate({ name: name.trim(), description: description.trim() });
  };

  return (
    <div className="stack">
      <section className="panel">
        <h1>Research Project</h1>
        <form onSubmit={onSubmit} className="form-row">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="项目名称"
            maxLength={255}
            required
          />
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="描述（可选）"
          />
          <button type="submit" disabled={createMutation.isPending || !name.trim()}>
            创建 Project
          </button>
        </form>
        {createMutation.isError && (
          <p className="error-text">{errorMessage(createMutation.error)}</p>
        )}
      </section>

      <section className="panel">
        {projectsQuery.isPending && <p className="muted">加载中…</p>}
        {projectsQuery.isError && (
          <p className="error-text">{errorMessage(projectsQuery.error)}</p>
        )}
        {projectsQuery.data && projectsQuery.data.length === 0 && (
          <p className="muted">还没有 Project。用上面的表单创建第一个。</p>
        )}
        {projectsQuery.data && projectsQuery.data.length > 0 && (
          <ul className="item-list">
            {projectsQuery.data.map((project) => (
              <li key={project.project_id}>
                <Link to={`/projects/${project.project_id}`} className="item-title">
                  {project.name}
                </Link>
                {project.description && <span className="muted"> {project.description}</span>}
                <span className="mono muted"> {project.project_id.slice(0, 8)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
