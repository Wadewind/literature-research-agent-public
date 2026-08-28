import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { AgentBrowserPanelView } from "./AgentBrowserPanel";

const activeControl = {
  control_id: "control-1",
  session_id: "session-1",
  mode: "manual" as const,
  status: "active" as const,
  revision: 2,
  sandbox_generation: 3,
  started_at: "2026-08-28T00:00:00Z",
  expires_at: "2026-08-28T00:05:00Z",
  ended_at: null,
  end_reason: null,
  viewer_connected: false,
};

const common = {
  ticket: null,
  viewUrl: null,
  viewerState: "idle" as const,
  pending: false,
  error: null,
  onStart: vi.fn(),
  onEnd: vi.fn(),
  onViewerState: vi.fn(),
};

describe("AgentBrowserPanelView", () => {
  it("活动 Turn 明确保持 Agent 控制且不提供人工按钮", () => {
    const html = renderToStaticMarkup(createElement(AgentBrowserPanelView, {
      ...common,
      control: null,
      activeTurn: true,
    }));
    expect(html).toContain("Agent 操作中");
    expect(html).toContain("人工与 Agent 不会同时操作");
    expect(html).not.toContain("开始接管</button>");
  });

  it("刷新后的活动控制可重新连接，但不会把 ticket 渲染为文本", () => {
    const html = renderToStaticMarkup(createElement(AgentBrowserPanelView, {
      ...common,
      control: activeControl,
      activeTurn: false,
    }));
    expect(html).toContain("人工控制");
    expect(html).toContain("重新连接");
    expect(html).not.toContain("browser-ticket");
  });

  it("过期状态说明可以重新申请", () => {
    const html = renderToStaticMarkup(createElement(AgentBrowserPanelView, {
      ...common,
      control: {
        ...activeControl,
        status: "expired",
        ended_at: "2026-08-28T00:05:00Z",
        end_reason: "ttl_expired",
      },
      activeTurn: false,
    }));
    expect(html).toContain("控制权已过期");
    expect(html).toContain("开始接管");
  });

  it("旧 generation 不会伪装成普通断线", () => {
    const html = renderToStaticMarkup(createElement(AgentBrowserPanelView, {
      ...common,
      control: {
        ...activeControl,
        status: "expired",
        ended_at: "2026-08-28T00:03:00Z",
        end_reason: "sandbox_generation_changed",
      },
      activeTurn: false,
    }));
    expect(html).toContain("旧 generation");
    expect(html).toContain("票据已失效");
  });
});
