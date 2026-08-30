import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import {
  AgentInspectorView,
  nextAgentInspectorTab,
} from "./AgentInspector";

describe("AgentInspectorView", () => {
  it("把证据、浏览器和成果分成可访问的独立页签", () => {
    const html = renderToStaticMarkup(createElement(AgentInspectorView, {
      activeTab: "browser",
      onTabChange: vi.fn(),
      onClose: vi.fn(),
      evidence: createElement("p", null, "Evidence ledger only"),
      browser: createElement("p", null, "Browser viewer only"),
      outputs: createElement("p", null, "Artifacts only"),
    }));

    expect(html).toContain('role="tablist"');
    expect(html).toContain('role="tab"');
    expect(html).toContain('aria-selected="true"');
    expect(html).toContain('role="tabpanel"');
    expect(html).toContain('aria-label="关闭检查器"');
    expect(html).toContain("Browser viewer only");
    expect(html).toMatch(/agent-inspector-panel-evidence[^>]*hidden=""[^>]*>.*Evidence ledger only/);
    expect(html).toMatch(/agent-inspector-panel-browser[^>]*>.*Browser viewer only/);
    expect(html).toMatch(/agent-inspector-panel-outputs[^>]*hidden=""[^>]*>.*Artifacts only/);
  });

  it("支持方向键、Home 与 End 在三个页签间循环", () => {
    expect(nextAgentInspectorTab("evidence", "ArrowRight")).toBe("browser");
    expect(nextAgentInspectorTab("evidence", "ArrowLeft")).toBe("outputs");
    expect(nextAgentInspectorTab("browser", "Home")).toBe("evidence");
    expect(nextAgentInspectorTab("browser", "End")).toBe("outputs");
    expect(nextAgentInspectorTab("browser", "Enter")).toBeNull();
  });
});
