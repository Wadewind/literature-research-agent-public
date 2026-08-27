import { Link } from "react-router-dom";

import { chatHomePath } from "../workspace/projectWorkspace";

interface ProjectNavProps {
  projectId: string;
  active: "library" | "chat" | "reviews" | "agent";
}

export default function ProjectNav({ projectId, active }: ProjectNavProps) {
  return (
    <nav className="project-nav" aria-label="Project 工作区">
      <Link className={active === "library" ? "active" : ""} to={`/projects/${projectId}`}>
        文献库
      </Link>
      <Link className={active === "chat" ? "active" : ""} to={chatHomePath(projectId)}>
        文献问答
      </Link>
      <Link className={active === "reviews" ? "active" : ""} to={`/projects/${projectId}/reviews`}>
        综述
      </Link>
      <Link className={active === "agent" ? "active" : ""} to={`/projects/${projectId}/agent`}>
        研究助手
      </Link>
    </nav>
  );
}
