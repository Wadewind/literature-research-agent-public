import { useQuery } from "@tanstack/react-query";
import {
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import { Link, NavLink, useLocation } from "react-router-dom";

import { apiFetch } from "../api/client";
import type { AgentSession, Conversation, Project } from "../api/types";
import { chatConversationPath, chatHomePath } from "../workspace/projectWorkspace";

const SIDEBAR_STORAGE_KEY = "literature-atlas.app-sidebar.v1";

export const SIDEBAR_WIDTH = {
  min: 216,
  max: 288,
  default: 232,
  step: 8,
} as const;

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
  width?: number;
}

interface SidebarPreference {
  collapsed: boolean;
  width: number;
}

interface AppSidebarViewProps {
  collapsed: boolean;
  width: number;
  onToggle: () => void;
  onWidthChange: (width: number) => void;
  onWidthCommit: (width: number) => void;
  onWidthReset: () => void;
  pathname: string;
  projectId?: string;
  project?: Pick<Project, "name" | "archived_at">;
  projectUnavailable?: boolean;
  conversations?: Array<Pick<Conversation, "conversation_id" | "title">>;
  conversationsUnavailable?: boolean;
  agentSessions?: Array<Pick<
    AgentSession,
    "session_id" | "title" | "active_turn_run_id" | "last_activity_at"
  >>;
  agentSessionsUnavailable?: boolean;
}

interface ProjectNavItem {
  mode: ProjectMode;
  label: string;
  to: string;
  icon: SidebarIconName;
}

const RECENT_SESSION_LIMIT = 5;

function routeResourceId(pathname: string, mode: "chat" | "agent"): string | undefined {
  const pattern = mode === "chat" ? /\/chat\/([^/?#]+)/ : /\/agent\/([^/?#]+)/;
  const match = pattern.exec(pathname);
  return match?.[1];
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

const DEFAULT_SIDEBAR_PREFERENCE: SidebarPreference = {
  collapsed: false,
  width: SIDEBAR_WIDTH.default,
};

export function clampSidebarWidth(width: number): number {
  if (!Number.isFinite(width)) return SIDEBAR_WIDTH.default;
  return Math.min(SIDEBAR_WIDTH.max, Math.max(SIDEBAR_WIDTH.min, Math.round(width)));
}

function loadSidebarPreference(): SidebarPreference {
  if (typeof window === "undefined") return DEFAULT_SIDEBAR_PREFERENCE;
  try {
    return parseSidebarPreference(window.localStorage.getItem(SIDEBAR_STORAGE_KEY));
  } catch {
    return DEFAULT_SIDEBAR_PREFERENCE;
  }
}

export function parseSidebarPreference(raw: string | null): SidebarPreference {
  if (!raw) return DEFAULT_SIDEBAR_PREFERENCE;
  try {
    const value = JSON.parse(raw) as Partial<SidebarPreferenceV1>;
    if (value.version !== 1 || typeof value.collapsed !== "boolean") {
      return DEFAULT_SIDEBAR_PREFERENCE;
    }
    return {
      collapsed: value.collapsed,
      width: typeof value.width === "number"
        ? clampSidebarWidth(value.width)
        : SIDEBAR_WIDTH.default,
    };
  } catch {
    return DEFAULT_SIDEBAR_PREFERENCE;
  }
}

function saveSidebarPreference(preference: SidebarPreference): void {
  if (typeof window === "undefined") return;
  const value: SidebarPreferenceV1 = { version: 1, ...preference };
  try {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, JSON.stringify(value));
  } catch {
    // 浏览器禁用存储时仍保留本次页面内状态。
  }
}

export default function AppSidebar() {
  const { pathname } = useLocation();
  const projectId = projectIdFromPathname(pathname);
  const [preference, setPreference] = useState(loadSidebarPreference);
  const { collapsed, width } = preference;
  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => apiFetch<Project>(`/api/v1/projects/${projectId}`),
    enabled: Boolean(projectId),
  });
  const conversationsQuery = useQuery({
    queryKey: ["conversations", projectId],
    queryFn: () => apiFetch<Conversation[]>(`/api/v1/projects/${projectId}/conversations`),
    enabled: Boolean(projectId && !collapsed),
  });
  const agentSessionsQuery = useQuery({
    queryKey: ["agent-sessions", projectId],
    queryFn: () => apiFetch<AgentSession[]>(`/api/v1/projects/${projectId}/agent-sessions`),
    enabled: Boolean(projectId && !collapsed),
  });

  const toggleCollapsed = () => {
    setPreference((current) => {
      const next = { ...current, collapsed: !current.collapsed };
      saveSidebarPreference(next);
      return next;
    });
  };

  const updateWidth = (nextWidth: number) => {
    setPreference((current) => ({ ...current, width: clampSidebarWidth(nextWidth) }));
  };

  const commitWidth = (nextWidth: number) => {
    setPreference((current) => {
      const next = { ...current, width: clampSidebarWidth(nextWidth) };
      saveSidebarPreference(next);
      return next;
    });
  };

  const resetWidth = () => commitWidth(SIDEBAR_WIDTH.default);

  return (
    <AppSidebarView
      collapsed={collapsed}
      width={width}
      onToggle={toggleCollapsed}
      onWidthChange={updateWidth}
      onWidthCommit={commitWidth}
      onWidthReset={resetWidth}
      pathname={pathname}
      projectId={projectId}
      project={projectQuery.data}
      projectUnavailable={projectQuery.isError}
      conversations={conversationsQuery.data}
      conversationsUnavailable={conversationsQuery.isError}
      agentSessions={agentSessionsQuery.data}
      agentSessionsUnavailable={agentSessionsQuery.isError}
    />
  );
}

export function AppSidebarView({
  collapsed,
  width,
  onToggle,
  onWidthChange,
  onWidthCommit,
  onWidthReset,
  pathname,
  projectId,
  project,
  projectUnavailable = false,
  conversations,
  conversationsUnavailable = false,
  agentSessions,
  agentSessionsUnavailable = false,
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
  const [expanded, setExpanded] = useState<Record<"chat" | "agent", boolean>>({
    chat: currentMode === "chat",
    agent: currentMode === "agent",
  });
  const [showAll, setShowAll] = useState<Record<"chat" | "agent", boolean>>({
    chat: false,
    agent: false,
  });

  useEffect(() => {
    if (currentMode !== "chat" && currentMode !== "agent") return;
    setExpanded((current) => current[currentMode]
      ? current
      : { ...current, [currentMode]: true });
  }, [currentMode]);

  const activeConversationId = routeResourceId(pathname, "chat");
  const activeSessionId = routeResourceId(pathname, "agent");

  const sessionTree = (mode: "chat" | "agent") => {
    const isChat = mode === "chat";
    const items = isChat ? conversations : agentSessions;
    const unavailable = isChat ? conversationsUnavailable : agentSessionsUnavailable;
    const visibleItems = showAll[mode] ? items : items?.slice(0, RECENT_SESSION_LIMIT);

    return (
      <div className="app-session-tree app-nav-label">
        {items === undefined && !unavailable ? <p>正在读取会话…</p> : null}
        {unavailable ? <p className="error-text">会话读取失败</p> : null}
        {items?.length === 0 ? <p>还没有会话</p> : null}
        {isChat
          ? (visibleItems as typeof conversations)?.map((conversation) => (
              <Link
                key={conversation.conversation_id}
                className={conversation.conversation_id === activeConversationId ? "active" : ""}
                to={chatConversationPath(projectId ?? "", conversation.conversation_id)}
                title={conversation.title || "未命名问答"}
              >
                <span>{conversation.title || "未命名问答"}</span>
              </Link>
            ))
          : (visibleItems as typeof agentSessions)?.map((session) => (
              <Link
                key={session.session_id}
                className={session.session_id === activeSessionId ? "active" : ""}
                to={`/projects/${projectId}/agent/${session.session_id}`}
                title={session.title || "未命名研究会话"}
              >
                <span>{session.title || "未命名研究会话"}</span>
                {session.active_turn_run_id ? <i aria-label="研究进行中" /> : null}
              </Link>
            ))}
        {(items?.length ?? 0) > RECENT_SESSION_LIMIT ? (
          <button
            type="button"
            className="app-session-more"
            onClick={() => setShowAll((current) => ({ ...current, [mode]: !current[mode] }))}
          >
            {showAll[mode] ? "收起" : `查看全部 ${items?.length}`}
          </button>
        ) : null}
      </div>
    );
  };

  return (
    <aside
      className={`app-sidebar${collapsed ? " is-collapsed" : ""}`}
      style={{ "--app-sidebar-width": `${width}px` } as CSSProperties}
    >
      <NavLink to="/" end className="brand app-sidebar-brand" aria-label="返回项目首页">
        <span className="brand-mark" aria-hidden="true">L·A</span>
        <span className="app-nav-label">
          <strong>Literature Atlas</strong>
          <small>Research Agent</small>
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
              const sessionMode: "chat" | "agent" | null =
                item.mode === "chat" || item.mode === "agent" ? item.mode : null;
              return (
                <div className={`app-nav-group${active ? " active" : ""}`} key={item.mode}>
                  <div className="app-nav-group-row">
                    <NavLink
                      to={item.to}
                      end={item.mode === "library"}
                      aria-label={item.label}
                      aria-current={active ? "page" : undefined}
                      className={`app-nav-link${active ? " active" : ""}`}
                      onClick={sessionMode && active ? () => setExpanded((current) => ({
                        ...current,
                        [sessionMode]: !current[sessionMode],
                      })) : undefined}
                    >
                      <SidebarIcon name={item.icon} />
                      <span className="app-nav-label">{item.label}</span>
                    </NavLink>
                    {sessionMode ? (
                      <div className="app-nav-group-actions app-nav-label">
                        <Link
                          className="app-nav-create"
                          to={item.to}
                          aria-label={sessionMode === "chat" ? "新建文献问答" : "新建研究会话"}
                        >
                          <span aria-hidden="true">＋</span>
                        </Link>
                        <button
                          type="button"
                          className="app-nav-disclosure"
                          aria-label={`${expanded[sessionMode] ? "收起" : "展开"}${item.label}会话`}
                          aria-expanded={expanded[sessionMode]}
                          onClick={() => setExpanded((current) => ({
                            ...current,
                            [sessionMode]: !current[sessionMode],
                          }))}
                        >
                          <span aria-hidden="true">›</span>
                        </button>
                      </div>
                    ) : null}
                  </div>
                  {sessionMode && expanded[sessionMode] ? sessionTree(sessionMode) : null}
                </div>
              );
            })}
          </div>
        ) : null}
      </nav>

      <div className="app-sidebar-footer">
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
      {!collapsed ? (
        <SidebarResizeHandle
          width={width}
          onChange={onWidthChange}
          onCommit={onWidthCommit}
          onReset={onWidthReset}
        />
      ) : null}
    </aside>
  );
}

interface SidebarResizeHandleProps {
  width: number;
  onChange: (width: number) => void;
  onCommit: (width: number) => void;
  onReset: () => void;
}

function SidebarResizeHandle({
  width,
  onChange,
  onCommit,
  onReset,
}: SidebarResizeHandleProps) {
  const drag = useRef<{ pointerId: number; clientX: number; width: number } | null>(null);
  const latestWidth = useRef(width);

  useEffect(() => {
    latestWidth.current = width;
  }, [width]);

  useEffect(() => () => {
    document.documentElement.classList.remove("is-resizing-sidebar");
  }, []);

  const finishDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!drag.current || drag.current.pointerId !== event.pointerId) return;
    drag.current = null;
    document.documentElement.classList.remove("is-resizing-sidebar");
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    onCommit(latestWidth.current);
  };

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    let nextWidth: number | undefined;
    if (event.key === "ArrowLeft") nextWidth = width - SIDEBAR_WIDTH.step;
    if (event.key === "ArrowRight") nextWidth = width + SIDEBAR_WIDTH.step;
    if (event.key === "Home") nextWidth = SIDEBAR_WIDTH.min;
    if (event.key === "End") nextWidth = SIDEBAR_WIDTH.max;
    if (nextWidth === undefined) return;
    event.preventDefault();
    onCommit(clampSidebarWidth(nextWidth));
  };

  return (
    <div
      className="app-sidebar-resize-handle"
      role="separator"
      aria-label="调整侧栏宽度"
      aria-orientation="vertical"
      aria-valuemin={SIDEBAR_WIDTH.min}
      aria-valuemax={SIDEBAR_WIDTH.max}
      aria-valuenow={width}
      aria-valuetext={`${width} 像素`}
      tabIndex={0}
      title="拖动调整侧栏宽度；双击复位"
      onDoubleClick={onReset}
      onKeyDown={handleKeyDown}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        drag.current = { pointerId: event.pointerId, clientX: event.clientX, width };
        latestWidth.current = width;
        event.currentTarget.setPointerCapture(event.pointerId);
        document.documentElement.classList.add("is-resizing-sidebar");
      }}
      onPointerMove={(event) => {
        if (!drag.current || drag.current.pointerId !== event.pointerId) return;
        const nextWidth = clampSidebarWidth(
          drag.current.width + event.clientX - drag.current.clientX,
        );
        latestWidth.current = nextWidth;
        onChange(nextWidth);
      }}
      onPointerUp={finishDrag}
      onPointerCancel={finishDrag}
    >
      <span aria-hidden="true" />
    </div>
  );
}

function SidebarIcon({ name }: { name: SidebarIconName }) {
  return (
    <svg className="app-nav-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d={SIDEBAR_ICON_PATHS[name]} />
    </svg>
  );
}
