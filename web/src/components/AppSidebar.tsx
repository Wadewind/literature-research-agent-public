import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { apiFetch } from "../api/client";
import type { Project } from "../api/types";
import { chatHomePath } from "../workspace/projectWorkspace";

const SIDEBAR_STORAGE_KEY = "literature-atlas.app-sidebar.v1";

type ProjectMode = "library" | "chat" | "reviews" | "agent";
type SidebarIconName = "projects" | "library" | "chat" | "reviews" | "agent";

const SIDEBAR_ICON_PATHS: Record<SidebarIconName, string> = {
  projects: "M3 6.5h6l1.5 2H21v10H3z M3 10h18",
  library: "M5 4h12a2 2 0 0 1 2 2v14H7a2 2 0 0 0-2 2z M5 4v16a2 2 0 0 1 2-2h12",
  chat: "M4 5h16v11H9l-5 4z M8 9h8 M8 12h5",
  reviews: "M5 3h14v18H5z M8 8h8 M8 12h8 M8 16h5",
  agent: "M12 4V2 M6 8h12v10H6z M9 12h.01 M15 12h.01 M9 18v3 M15 18v3",
};

interface SidebarPreferenceV1 {
  version: 1;
  collapsed: boolean;
}

interface AppSidebarViewProps {
  collapsed: boolean;
  onToggle: () => void;
  pathname: string;
  projectId?: string;
  project?: Pick<Project, "name" | "archived_at">;
  projectUnavailable?: boolean;
}

interface ProjectNavItem {
  mode: ProjectMode;
  label: string;
  to: string;
  icon: SidebarIconName;
}

export function projectIdFromPathname(pathname: string): string | undefined {
  const match = /^\/projects\/([^/]+)(?:\/|$)/.exec(pathname);
  if (!match) return undefined;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return undefined;
  }
}

export function projectModeFromPathname(
  pathname: string,
  projectId: string,
): ProjectMode | undefined {
  const base = `/projects/${encodeURIComponent(projectId)}`;
  if (pathname === base || pathname.startsWith(`${base}/versions/`)) return "library";
  if (pathname.startsWith(`${base}/chat`) || pathname.startsWith(`${base}/conversations/`)) {
    return "chat";
  }
  if (pathname.startsWith(`${base}/reviews`)) return "reviews";
  if (pathname.startsWith(`${base}/agent`)) return "agent";
  return undefined;
}

function loadSidebarPreference(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return parseSidebarPreference(window.localStorage.getItem(SIDEBAR_STORAGE_KEY));
  } catch {
    return false;
  }
}

export function parseSidebarPreference(raw: string | null): boolean {
  if (!raw) return false;
  try {
    const value = JSON.parse(raw) as Partial<SidebarPreferenceV1>;
    return value.version === 1 && typeof value.collapsed === "boolean"
      ? value.collapsed
      : false;
  } catch {
    return false;
  }
}

function saveSidebarPreference(collapsed: boolean): void {
  if (typeof window === "undefined") return;
  const value: SidebarPreferenceV1 = { version: 1, collapsed };
  try {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, JSON.stringify(value));
  } catch {
    // 浏览器禁用存储时仍保留本次页面内状态。
  }
}

export default function AppSidebar() {
  const { pathname } = useLocation();
  const projectId = projectIdFromPathname(pathname);
  const [collapsed, setCollapsed] = useState(loadSidebarPreference);
  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => apiFetch<Project>(`/api/v1/projects/${projectId}`),
    enabled: Boolean(projectId),
  });

  const toggleCollapsed = () => {
    setCollapsed((current) => {
      const next = !current;
      saveSidebarPreference(next);
      return next;
    });
  };

  return (
    <AppSidebarView
      collapsed={collapsed}
      onToggle={toggleCollapsed}
      pathname={pathname}
      projectId={projectId}
      project={projectQuery.data}
      projectUnavailable={projectQuery.isError}
    />
  );
}

export function AppSidebarView({
  collapsed,
  onToggle,
  pathname,
  projectId,
  project,
  projectUnavailable = false,
}: AppSidebarViewProps) {
  const currentMode = projectId
    ? projectModeFromPathname(pathname, projectId)
    : undefined;
  const projectItems: ProjectNavItem[] = projectId
    ? [
        { mode: "library", label: "文献库", to: `/projects/${projectId}`, icon: "library" },
        { mode: "chat", label: "文献问答", to: chatHomePath(projectId), icon: "chat" },
        { mode: "reviews", label: "综述", to: `/projects/${projectId}/reviews`, icon: "reviews" },
        { mode: "agent", label: "研究助手", to: `/projects/${projectId}/agent`, icon: "agent" },
      ]
    : [];

  return (
    <aside className={`app-sidebar${collapsed ? " is-collapsed" : ""}`}>
      <NavLink to="/" end className="brand app-sidebar-brand" aria-label="返回项目首页">
        <span className="brand-mark" aria-hidden="true">L·A</span>
        <span className="app-nav-label">
          <strong>Literature Atlas</strong>
          <small>文献综述 Agent</small>
        </span>
      </NavLink>

      <nav className="app-sidebar-nav" aria-label="应用导航">
        <div className="app-sidebar-section">
          <p className="app-sidebar-section-label app-nav-label">全局</p>
          <NavLink
            to="/"
            end
            aria-label="项目"
            className={({ isActive }) => `app-nav-link${isActive ? " active" : ""}`}
          >
            <SidebarIcon name="projects" />
            <span className="app-nav-label">项目</span>
          </NavLink>
          <NavLink
            to="/library"
            aria-label="个人文献库"
            className={({ isActive }) => `app-nav-link${isActive ? " active" : ""}`}
          >
            <SidebarIcon name="library" />
            <span className="app-nav-label">个人文献库</span>
          </NavLink>
        </div>

        {projectId ? (
          <div className="app-sidebar-section app-sidebar-project">
            <p className="app-sidebar-section-label app-nav-label">当前项目</p>
            <p className="app-sidebar-project-name app-nav-label" title={project?.name}>
              {projectUnavailable ? "项目不可用" : project?.name ?? "正在读取项目…"}
              {project?.archived_at ? <span>已归档</span> : null}
            </p>
            {projectItems.map((item) => {
              const active = item.mode === currentMode;
              return (
                <NavLink
                  key={item.mode}
                  to={item.to}
                  end={item.mode === "library"}
                  aria-label={item.label}
                  aria-current={active ? "page" : undefined}
                  className={`app-nav-link${active ? " active" : ""}`}
                >
                  <SidebarIcon name={item.icon} />
                  <span className="app-nav-label">{item.label}</span>
                </NavLink>
              );
            })}
          </div>
        ) : null}
      </nav>

      <div className="app-sidebar-footer">
        <div className="sidebar-phase" aria-label="Research Agent Spike">
          <span className="sidebar-phase-mark" aria-hidden="true">S</span>
          <span className="app-nav-label"><strong>RESEARCH AGENT</strong><small>SPIKE</small></span>
        </div>
        <button
          type="button"
          className="app-sidebar-toggle"
          onClick={onToggle}
          aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
          aria-expanded={!collapsed}
        >
          <span aria-hidden="true">{collapsed ? "›" : "‹"}</span>
          <span className="app-nav-label">{collapsed ? "展开" : "收起"}</span>
        </button>
      </div>
    </aside>
  );
}

function SidebarIcon({ name }: { name: SidebarIconName }) {
  return (
    <svg className="app-nav-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d={SIDEBAR_ICON_PATHS[name]} />
    </svg>
  );
}
