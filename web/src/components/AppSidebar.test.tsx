import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import {
  AppSidebarView,
  clampSidebarWidth,
  parseSidebarPreference,
  projectIdFromPathname,
  projectModeFromPathname,
  SIDEBAR_WIDTH,
} from "./AppSidebar";

function renderSidebar(
  pathname: string,
  options: {
    collapsed?: boolean;
    width?: number;
    projectId?: string;
    projectName?: string;
    conversations?: Array<{ conversation_id: string; title: string; scope_type: "project"; paper_ids: string[] }>;
    agentSessions?: Array<{
      session_id: string;
      title: string | null;
      active_turn_run_id: string | null;
      last_activity_at: string;
    }>;
  } = {},
): string {
  return renderToStaticMarkup(
    createElement(
      MemoryRouter,
      { initialEntries: [pathname] },
      createElement(AppSidebarView, {
        collapsed: options.collapsed ?? false,
        width: options.width ?? SIDEBAR_WIDTH.default,
        onToggle: () => undefined,
        onWidthChange: () => undefined,
        onWidthCommit: () => undefined,
        onWidthReset: () => undefined,
        pathname,
        projectId: options.projectId,
        project: options.projectName
          ? { name: options.projectName, archived_at: null }
          : undefined,
        conversations: options.conversations,
        agentSessions: options.agentSessions,
      }),
    ),
  );
}

describe("AppSidebar", () => {
  it("项目路由外只呈现全局入口", () => {
    const html = renderSidebar("/library");

    expect(html).toContain("应用导航");
    expect(html).toContain("项目");
    expect(html).toContain("个人文献库");
    expect(html).not.toContain("当前项目");
    expect(html).not.toContain("研究助手");
  });

  it("项目路由内呈现四个模式，并标出当前模式", () => {
    const html = renderSidebar("/projects/project-1/agent/session-1", {
      projectId: "project-1",
      projectName: "事实性评估方法",
    });

    expect(html).toContain("当前项目");
    expect(html).toContain("事实性评估方法");
    expect(html).toContain("文献库");
    expect(html).toContain("文献问答");
    expect(html).toContain("综述");
    expect(html).toMatch(/aria-current="page"[^>]*>.*研究助手/s);
  });

  it("从项目子路由解析 Sidebar scope 与模式", () => {
    expect(projectIdFromPathname("/library")).toBeUndefined();
    expect(projectIdFromPathname("/projects/project-1/reviews/run-1")).toBe("project-1");
    expect(projectModeFromPathname("/projects/project-1", "project-1")).toBe("library");
    expect(projectModeFromPathname("/projects/project-1/chat/conversation-1", "project-1"))
      .toBe("chat");
    expect(projectModeFromPathname("/projects/project-1/reviews/run-1", "project-1"))
      .toBe("reviews");
  });

  it("折叠态保留可辨识的导航与展开控制", () => {
    const html = renderSidebar("/projects/project-1", {
      collapsed: true,
      projectId: "project-1",
      projectName: "事实性评估方法",
    });

    expect(html).toContain("app-sidebar is-collapsed");
    expect(html).toContain('aria-label="展开侧栏"');
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain('aria-label="文献库"');
    expect(html).toContain('aria-label="研究助手"');
    expect(html).not.toContain('aria-label="调整侧栏宽度"');
  });

  it("展开态提供有界且可访问的侧栏宽度控制", () => {
    const html = renderSidebar("/library", { width: 264 });

    expect(html).toContain('--app-sidebar-width:264px');
    expect(html).toContain('role="separator"');
    expect(html).toContain('aria-label="调整侧栏宽度"');
    expect(html).toContain('aria-valuemin="216"');
    expect(html).toContain('aria-valuemax="288"');
    expect(html).toContain('aria-valuenow="264"');
  });

  it("在当前功能下显示最近会话与独立的新建入口", () => {
    const html = renderSidebar("/projects/project-1/agent/session-1", {
      projectId: "project-1",
      projectName: "事实性评估方法",
      conversations: [],
      agentSessions: [{
        session_id: "session-1",
        title: "路径规划研究缺口",
        active_turn_run_id: null,
        last_activity_at: "2026-08-30T10:00:00Z",
      }],
    });

    expect(html).toContain('aria-label="新建研究会话"');
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain("路径规划研究缺口");
    expect(html).toContain('/projects/project-1/agent/session-1');
  });

  it("解析宽度偏好，并兼容只含折叠状态的既有记录", () => {
    expect(parseSidebarPreference('{"version":1,"collapsed":true,"width":272}'))
      .toEqual({ collapsed: true, width: 272 });
    expect(parseSidebarPreference('{"version":1,"collapsed":true}'))
      .toEqual({ collapsed: true, width: SIDEBAR_WIDTH.default });
    expect(parseSidebarPreference('{"version":1,"collapsed":false,"width":999}'))
      .toEqual({ collapsed: false, width: SIDEBAR_WIDTH.max });
    expect(parseSidebarPreference('{"version":1,"collapsed":true,"width":"wide"}'))
      .toEqual({ collapsed: true, width: SIDEBAR_WIDTH.default });
    expect(parseSidebarPreference('{"version":2,"collapsed":true}'))
      .toEqual({ collapsed: false, width: SIDEBAR_WIDTH.default });
    expect(parseSidebarPreference('{"version":1,"collapsed":"yes"}'))
      .toEqual({ collapsed: false, width: SIDEBAR_WIDTH.default });
    expect(parseSidebarPreference("not-json"))
      .toEqual({ collapsed: false, width: SIDEBAR_WIDTH.default });
    expect(parseSidebarPreference(null))
      .toEqual({ collapsed: false, width: SIDEBAR_WIDTH.default });
  });

  it("将侧栏宽度限制在允许区间", () => {
    expect(clampSidebarWidth(180)).toBe(SIDEBAR_WIDTH.min);
    expect(clampSidebarWidth(252)).toBe(252);
    expect(clampSidebarWidth(340)).toBe(SIDEBAR_WIDTH.max);
  });
});
