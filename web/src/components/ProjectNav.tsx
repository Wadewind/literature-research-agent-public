import { Link } from "react-router-dom";

interface ProjectNavProps {
  projectId: string;
  active: "library" | "chat" | "reviews";
}

export default function ProjectNav({ projectId, active }: ProjectNavProps) {
  return (
    <nav className="project-nav" aria-label="Project 工作区">
      <Link className={active === "library" ? "active" : ""} to={`/projects/${projectId}`}>
        文献库
      </Link>
      <Link className={active === "chat" ? "active" : ""} to={`/projects/${projectId}#project-chat`}>
        Chat
      </Link>
      <Link className={active === "reviews" ? "active" : ""} to={`/projects/${projectId}/reviews`}>
        Reviews
      </Link>
    </nav>
  );
}
