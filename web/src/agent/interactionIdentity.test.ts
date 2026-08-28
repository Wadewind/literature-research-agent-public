import { describe, expect, it } from "vitest";

import { agentWorkspaceKey } from "./interactionIdentity";

describe("Agent workspace identity", () => {
  it("Project 或 Session 变化都会重建交互边界并清空消息、选择与上传意图", () => {
    expect(agentWorkspaceKey("project-1", "session-1")).not.toBe(
      agentWorkspaceKey("project-1", "session-2"),
    );
    expect(agentWorkspaceKey("project-1", "session-1")).not.toBe(
      agentWorkspaceKey("project-2", "session-1"),
    );
  });
});
