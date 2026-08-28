import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import {
  AppSidebarView,
  parseSidebarPreference,
  projectIdFromPathname,
  projectModeFromPathname,
} from "./AppSidebar";

function renderSidebar(
  pathname: string,
  options: {
    collapsed?: boolean;
    projectId?: string;
    projectName?: string;
  } = {},
): string {
  return renderToStaticMarkup(
    createElement(
      MemoryRouter,
      { initialEntries: [pathname] },
      createElement(AppSidebarView, {
        collapsed: options.collapsed ?? false,
        onToggle: () => undefined,
        pathname,
        projectId: options.projectId,
        project: options.projectName
          ? { name: options.projectName, archived_at: null }
          : undefined,
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
  });

  it("只接受当前版本且字段完整的折叠偏好", () => {
    expect(parseSidebarPreference('{"version":1,"collapsed":true}')).toBe(true);
    expect(parseSidebarPreference('{"version":2,"collapsed":true}')).toBe(false);
    expect(parseSidebarPreference('{"version":1,"collapsed":"yes"}')).toBe(false);
    expect(parseSidebarPreference("not-json")).toBe(false);
    expect(parseSidebarPreference(null)).toBe(false);
  });
});
