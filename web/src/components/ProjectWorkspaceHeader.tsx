import { type ReactNode } from "react";
import { Link } from "react-router-dom";

import type { Project } from "../api/types";
import ProjectNav from "./ProjectNav";

interface ProjectWorkspaceHeaderProps {
  projectId: string;
  project: Project | undefined;
  active: "library" | "chat" | "reviews" | "agent";
  eyebrow: string;
  description: string;
  actions?: ReactNode;
}

export default function ProjectWorkspaceHeader({
  projectId,
  project,
  active,
  eyebrow,
  description,
  actions,
}: ProjectWorkspaceHeaderProps) {
  return (
    <header className="project-workspace-header">
      <div className="project-workspace-identity">
        <p className="breadcrumb"><Link to="/">研究项目</Link><span>/</span>{eyebrow}</p>
        <div className="project-workspace-title-row">
          <h1>{project?.name ?? "正在读取…"}</h1>
          {project?.archived_at ? <span className="badge badge-warn">已归档</span> : null}
        </div>
        <p>{description}</p>
      </div>
      {actions ? <div className="project-workspace-actions">{actions}</div> : null}
      <ProjectNav projectId={projectId} active={active} />
    </header>
  );
}
