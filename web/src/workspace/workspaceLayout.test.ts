import { describe, expect, it } from "vitest";

import {
  DEFAULT_WORKSPACE_LAYOUT,
  loadWorkspaceLayout,
  resizeWorkspace,
  saveWorkspaceLayout,
} from "./workspaceLayout";

describe("Shared research workspace layout", () => {
  it("按模式隔离带版本的最小栏宽偏好", () => {
    const values = new Map<string, string>();
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    };

    saveWorkspaceLayout(storage, "chat", { left: 300, right: 400 });
    expect(loadWorkspaceLayout(storage, "chat")).toEqual({ left: 300, right: 400 });
    expect(loadWorkspaceLayout(storage, "agent")).toEqual(DEFAULT_WORKSPACE_LAYOUT);
    expect(values.get("literature-agent:chat-workspace")).toBe(
      '{"version":1,"left":300,"right":400}',
    );
  });

  it("拒绝旧版本与非法宽度，并为中栏保留最小空间", () => {
    const storage = {
      getItem: () => '{"version":2,"left":300,"right":400}',
    };
    expect(loadWorkspaceLayout(storage, "chat")).toEqual(DEFAULT_WORKSPACE_LAYOUT);
    expect(resizeWorkspace(DEFAULT_WORKSPACE_LAYOUT, "left", 500, 1440).left).toBe(420);
    expect(resizeWorkspace(DEFAULT_WORKSPACE_LAYOUT, "right", -500, 1280).right).toBe(300);
    expect(resizeWorkspace(DEFAULT_WORKSPACE_LAYOUT, "right", 500, 1600).right).toBe(720);
  });
});
