import { describe, expect, it } from "vitest";

import {
  AGENT_WORKSPACE_DEFAULT,
  loadAgentWorkspaceLayout,
  resizeAgentWorkspace,
  saveAgentWorkspaceLayout,
} from "./workspaceLayout";

describe("Agent workspace resize", () => {
  it("限制左右栏宽度并为中栏保留最小空间", () => {
    expect(resizeAgentWorkspace(AGENT_WORKSPACE_DEFAULT, "left", 500, 1440).left).toBe(420);
    expect(resizeAgentWorkspace(AGENT_WORKSPACE_DEFAULT, "right", -500, 1280).right).toBe(300);
  });

  it("只恢复 version=1 的合法最小记录", () => {
    const values = new Map<string, string>();
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    };
    saveAgentWorkspaceLayout(storage, { left: 310, right: 410 });
    expect(loadAgentWorkspaceLayout(storage)).toEqual({ left: 310, right: 410 });
    values.set("literature-agent:agent-workspace", '{"version":2,"left":400,"right":500}');
    expect(loadAgentWorkspaceLayout(storage)).toEqual(AGENT_WORKSPACE_DEFAULT);
  });
});
