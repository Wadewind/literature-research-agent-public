import { describe, expect, it } from "vitest";

import { agentWorkspaceKey } from "./interactionIdentity";

describe("Agent workspace identity", () => {
  it("Project 或 Session 变化都会创建新的交互状态边界", () => {
    expect(agentWorkspaceKey("project-1", "session-1")).not.toBe(
      agentWorkspaceKey("project-1", "session-2"),
    );
    expect(agentWorkspaceKey("project-1", "session-1")).not.toBe(
      agentWorkspaceKey("project-2", "session-1"),
    );
  });
});
