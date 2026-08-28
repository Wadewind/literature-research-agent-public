import { describe, expect, it } from "vitest";

import { canQueryAgentAttachments } from "./sessionQueries";

describe("canQueryAgentAttachments", () => {
  it("没有 Session 或资源闭包未确认时不请求附件", () => {
    expect(canQueryAgentAttachments("", false)).toBe(false);
    expect(canQueryAgentAttachments("", true)).toBe(false);
    expect(canQueryAgentAttachments("session-1", false)).toBe(false);
    expect(canQueryAgentAttachments("session-1", true)).toBe(true);
  });
});
